# RADAR 服务端部署手册

面向**服务端上线与运维**。`webapp/README.md` 讲的是架构与本地启动，本文档讲容量规划、部署、验证、监控、扩容与排障。

---

## 0. 部署拓扑

```
客户端 ──► nginx(:80/443) ──► uvicorn(:8000)
                                  ├ 上传落盘   $RADAR_DATA_DIR/uploads/<case_id>
                                  ├ 转换体数据 $RADAR_DATA_DIR/cases/<case_id>
                                  ├ 结果 JSON  $RADAR_DATA_DIR/results/<case_id>.json
                                  └ 任务队列（内存，单 worker 串行消费）
                                        └► RadarEngine 单例（常驻显存 + infer_lock）
```

三个必须记住的约束：

1. **单进程单卡**。`infer_lock` 是进程内锁，`--workers > 1` 会让它失效并重复加载模型，直接 OOM。
2. **任务队列在内存里**。进程重启后未完成的 `queued` / `inferencing` 任务会丢失（已完成的靠 results JSON 恢复）。
3. **无鉴权**。默认只适合内网，公网必须前置认证。

---

## 1. 容量规划

### 1.1 先实测，别按估算采购

推理耗时与体数据尺寸强相关，波动可达数倍。服务已内置打点，**部署后先跑 10-20 例真实数据再定容量**。

每例完成时会打一行日志：

```
[radar] case=3f2a1b... device=NVIDIA A10 infer=41.3s windows=6 refine=7 peak=9421.0MB
```

| 字段 | 含义 |
| --- | --- |
| `infer` | 纯模型推理耗时（秒），不含上传与预处理 |
| `windows` | 分割滑窗数，由体数据尺寸 / `ROI=(96,256,384)` 决定 |
| `refine` | 补推次数：滑窗未覆盖到的器官，各自再跑一次前向（上限 18） |
| `peak` | `torch.cuda.max_memory_allocated` 峰值，不含 CUDA context 与碎片 |

统计分布：

```bash
# 耗时 p50 / p95
docker logs radarserve 2>&1 | grep -oP 'infer=\K[0-9.]+' | sort -n \
  | awk '{a[NR]=$1} END{print "p50="a[int(NR*0.5)]"s  p95="a[int(NR*0.95)]"s"}'

# 显存峰值上界
docker logs radarserve 2>&1 | grep -oP 'peak=\K[0-9.]+' | sort -n | tail -1
```

同一份数据也会通过 `GET /api/cases/{id}` 的 `result.stats` 返回，并在结果页「病例概览」展示。

### 1.2 显存

按 `ROI=(96,256,384)`、6 阶段最深 320 通道，加上两张全分辨率 `(37, D, H, W)` 的滑窗累加张量推算，fp32 峰值约 **8-12 GB**（此为按张量尺寸推算，非实测）。

- **16 GB 卡**大概率可运行，**24 GB 更稳妥**
- 显存通常不是瓶颈，**串行推理导致的吞吐才是**：单卡吞吐 ≈ `1 / p50(infer)`
- 若实测 `peak` 远小于显存容量，可走 §7.2 的多实例方案，无需改代码

### 1.3 需要几张卡

```
单卡日吞吐 ≈ 可用时长(秒) / p50(infer)
需要卡数   ≈ 日病例数 / 单卡日吞吐 × 安全系数(1.3~1.5)
```

队列是串行的，所以**排队时间会随并发快速恶化**。建议按 `MAX_QUEUE_SIZE × p95(infer)` 估算最坏等待时间，超过可接受值就扩容，而不是调大队列。

---

## 2. 部署前准备

### 2.1 宿主机

- NVIDIA 驱动 + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)（Docker 方式必需）
- CUDA 12.1（镜像 `nvidia/cuda:12.1.1-runtime-ubuntu22.04`，换 CUDA 版本需同步改 `Dockerfile` 里 torch 的 `+cu121` 后缀）
- 磁盘：单个病例原始 DICOM 可达 2 GB，按 `日病例数 × 平均体积 × TTL天数` 预留

### 2.2 下载模型权重

必需三个文件（`config.ensure_ckpt_ready()` 的检查项）：

| 文件 | 说明 |
| --- | --- |
| `ckpt/checkpoint_radar_pretrain.pth` | 主权重 |
| `ckpt/infer_text_embedding_radar.pt` | 文本侧 embedding |
| `ckpt/bert-base-chinese/config.json` | 中文 BERT 配置（**整目录都要**，还含权重与词表） |

```bash
pip install "huggingface_hub<0.23"     # 版本约束见下方说明
cd download_scripts
python download_checkpoints.py          # 默认 --local-dir ../ckpt，须在此目录执行
```

⚠️ **两个会卡住部署的点**

1. `download_checkpoints.py` 里的 `local_dir_use_symlinks=False` 在 `huggingface_hub>=0.23` 已废弃、**1.0 已移除**，装最新版会直接 `TypeError`。要么按上面固定 `<0.23`，要么删掉该参数（新版本默认就存真实文件，行为一致）。
2. 脚本默认 `--local-dir ../ckpt` 是**相对当前工作目录**的。在项目根执行会写到项目外面，务必从 `download_scripts/` 目录运行，或显式指定 `--local-dir ./ckpt`。

校验：

```bash
ls ckpt/checkpoint_radar_pretrain.pth ckpt/infer_text_embedding_radar.pt ckpt/bert-base-chinese/
```

### 2.3 构建上下文

已在项目根提供 `.dockerignore`，排除 `ckpt/`、`data/`、`results/`、`RADAR_train/`、`.git/` 等约 330 MB 无关文件。若自行调整，注意**不要**把 `RADAR_inference/`、`webapp/backend/`、`webapp/frontend/`、`webapp/deploy/` 排除掉。

---

## 3. 部署

### 3.0 一键脚本

```bash
bash webapp/deploy/deploy.sh                          # Docker 模式（推荐）
bash webapp/deploy/deploy.sh native                   # 裸机模式
bash webapp/deploy/deploy.sh macos                    # macOS 本机（仅 CPU，见 §3.5）
bash webapp/deploy/deploy.sh docker --download-ckpt   # 缺权重时自动下载
```

脚本按顺序完成：权重校验 → 环境检查 → 启动 → 健康检查轮询，最后打印访问地址、日志与停止命令。

| 模式 | 前端构建位置 | 依赖安装位置 | 适用场景 |
| --- | --- | --- | --- |
| `docker` | 镜像内（多阶段构建） | 镜像内 | 生产部署，宿主机只需 Docker |
| `native` | 宿主机 `npm run build` | 项目根 `.venv` | 已有 CUDA 环境、不想用容器 |
| `macos` | 宿主机 `npm run build` | 项目根 `.venv`（CPU 版 torch） | macOS 上做界面联调与流程验证 |

两点注意：

- 脚本文件须以 **LF** 换行保存。Windows 下编辑过的话先执行 `sed -i 's/\r$//' webapp/deploy/deploy.sh`
- `native` 模式只装**推理依赖**（`webapp/backend/requirements.txt` + torch）。**不要**安装项目根目录的 `requirements.txt`——那是训练依赖，含 `streamlit`/`spacy`/`diffusers`/`decord`，且其 `torch>=1.10.0` 会覆盖掉需要的 `2.4.0+cu121`

### 3.1 Docker Compose（推荐）

```bash
cd webapp/deploy
docker compose up -d --build
docker compose logs -f
```

- 构建上下文是**项目根**（`context: ../..`），不要移动 `docker-compose.yml`
- 权重以 `../../ckpt:/ckpt:ro` 只读挂载，运行时数据写入 `radarserve-data` 卷
- 镜像内已构建前端，访问 `http://<host>:8000` 即可

首次启动会预热模型（后台线程），`nvidia-smi` 看到显存占用后即可服务。

### 3.2 裸机 / conda

```bash
conda create -n radar python=3.10 && conda activate radar
pip install torch==2.4.0+cu121 --index-url https://download.pytorch.org/whl/cu121
pip install -r webapp/backend/requirements.txt   # 仅推理依赖

cd webapp/frontend && npm install && npm run build   # 前端，构建后由后端托管
bash webapp/deploy/start.sh
```

⚠️ **不要安装项目根目录的 `requirements.txt`** —— 那是训练依赖（含 `streamlit` / `spacy` / `diffusers` / `decord`），且其中 `torch>=1.10.0` 会覆盖掉需要的 `2.4.0+cu121`。

`start.sh` 已导出全部环境变量，覆盖默认值直接在外层 `export` 即可。等价的一键版本见 §3.0 的 `deploy.sh native`。

### 3.3 反向代理（nginx）

上传上限默认 2 GB，nginx 默认 1 MB 会直接拦掉：

```nginx
server {
    listen 80;
    client_max_body_size 0;          # 0 = 不限制，或设为 2048m
    proxy_read_timeout  600s;        # 体数据转换耗时较长
    proxy_send_timeout  600s;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

---

### 3.4 Windows 本机部署（单机自用）

**可行。** 代码层已做过跨平台处理：无 `fcntl` / `os.fork` / `signal` / `pwd` 等 Unix-only 依赖，路径统一用 `pathlib`，`shutil.rmtree` 带 `ignore_errors`。

```powershell
powershell -ExecutionPolicy Bypass -File webapp\deploy\deploy.ps1
powershell -ExecutionPolicy Bypass -File webapp\deploy\deploy.ps1 -DownloadCkpt
```

前提条件：

| 项 | 要求 |
| --- | --- |
| Python | **3.10+**（代码用了 PEP 604 的 `X \| Y` 注解） |
| Node.js | 构建前端用 |
| NVIDIA 驱动 | 支持 CUDA 12.1；驱动过旧需把脚本里的 `cu121` 换成 `cu118` |
| 显存 | **≥12 GB 建议，16 GB 以上稳妥**，见 §1.2 |

注意：

- 脚本用 `Start-Process` 后台启动，PID 写入 `webapp\runtime\server.pid`
- **服务运行期间模型常驻显存**（8-12 GB）。本机自用时，玩游戏或跑重度 GPU 任务前先停掉服务
- 首次运行要装 torch（约 2.5 GB），耗时较长
- 若不想用原生环境，Docker Desktop（WSL2 后端）+ `docker compose up -d --build` 同样可用，但需要配置 WSL2 的 GPU 直通，比原生多一层折腾

---

### 3.5 macOS 本机部署（单机自用）

**能跑，但只有 CPU，定位是联调而不是生产。**

```bash
bash webapp/deploy/deploy.sh macos
bash webapp/deploy/deploy.sh macos --download-ckpt   # 顺带下权重
```

脚本为 macOS 做了三处适配：

| 差异 | 说明 |
| --- | --- |
| `docker` 自动改判 | macOS 无 NVIDIA 直通，基础镜像只有 amd64（需 Rosetta 模拟），直接跑会卡在 `could not select device driver "nvidia"`；脚本检测到 Darwin 后自动切到 `macos` 模式 |
| torch 轮子 | 不指定 `cu121` 源（该源无 macOS 发行版，会 `No matching distribution found`），改为 PyPI 官方 CPU 轮子 |
| 设备与检查 | `RADAR_DEVICE` 默认 `cpu`，不调用 `nvidia-smi`；`/api/health` 不会显示出误导性的 cuda |

前提条件：

| 项 | 要求 |
| --- | --- |
| Python | **3.10 / 3.11**（`requirements.txt` 固定 `transformers==4.25`，过新的 Python 可能没有轮子） |
| Node.js | 构建前端用（`brew install node`） |
| GPU | 无 CUDA；M 系列 GPU 也用不上（代码只认 `cuda` / `cpu`，未接 MPS） |

注意：

- 单例推理耗时可达**数十分钟**（3D 滑窗 + 最多 18 次补推），只适合功能验证与界面联调
- 权重下载若直连 HuggingFace 不通，先 `export HF_ENDPOINT=https://hf-mirror.com`
- 后台启动方式同 `native` 模式：PID 在 `webapp/runtime/server.pid`，日志在 `webapp/runtime/server.log`
- macOS 自带 `/bin/bash` 是 3.2，脚本未使用 bash 4 专属语法，可直接运行；用 `-h` 可查看用法

---

## 4. 配置

全部通过环境变量覆盖（`webapp/backend/config.py`）。

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MODEL_ROOT` / `CONFIGS_ROOT` | `<项目根>/ckpt` | 权重与 tokenizer 目录 |
| `RADAR_DATA_DIR` | `webapp/runtime` | 上传 / 体数据 / 结果 |
| `RADAR_DEVICE` | `cuda` | `cuda` / `cpu`（CPU 慢一到两个数量级，仅连通性自测） |
| `RADAR_CHECKPOINT` | `checkpoint_radar_pretrain.pth` | 权重文件名 |
| `RADAR_TEXT_EMBEDDING` | `infer_text_embedding_radar.pt` | 文本 embedding 文件名 |
| `RADAR_BERT_DIR` | `bert-base-chinese` | BERT 目录名 |
| `RADAR_MAX_UPLOAD_MB` | `2048` | 上传体积上限，超限 413 |
| `RADAR_MAX_SPATIAL_DIM` | `1000` | 单空间维度上限，超限直接失败（对应原实现的静默 skip） |
| `RADAR_MAX_QUEUE_SIZE` | `64` | 排队任务上限，超限返回 **429** |
| `RADAR_RESULT_TTL_DAYS` | `7` | 结果保留天数，`0` 表示不清理 |
| `RADAR_CORS_ORIGINS` | `*` | **生产环境改成实际域名** |

关于后两项：

- 达到 `MAX_QUEUE_SIZE` 时，上传接口返回 429 并清理已落盘文件；选序列接口返回 429 但**保留数据**，用户可稍后重试。
- TTL 清理每小时扫描一次，**只清理 `done` / `failed` 终态病例**，排队中的任务不会被误删。

---

## 5. 验证

```bash
# 1) 权重与服务状态
curl -s http://<host>:8000/api/health | python -m json.tool
```

| `status` | 含义 | 处理 |
| --- | --- | --- |
| `ready` | 就绪 | 可用 |
| `loading` | 模型预热中 | 等待后重试 |
| `ckpt_missing` | 权重缺失 | 看 `missing_files` 字段，回 §2.2 |

```bash
# 2) 提交一例（异步，立即返回 case_id）
curl -s -F "file=@/path/to/case.nii.gz" http://<host>:8000/api/cases

# 3) 轮询进度
curl -s http://<host>:8000/api/cases/<case_id> | python -m json.tool
```

状态流转：`pending`（多序列待选）→ `queued` → `preprocessing` → `inferencing` → `done` / `failed`。

多期相 DICOM 若返回 `pending` 且 `available_series` 非空，先调 `POST /api/cases/{id}/series` 确认期相再入队。

---

## 6. 运维

### 6.1 队列监控

`GET /api/health` 的 `queue_size` 是待处理任务数（不含正在执行的那个）。持续增长说明吞吐不足，应扩容而不是调大队列。

### 6.2 磁盘

三个目录都在 `RADAR_DATA_DIR` 下：

- `uploads/` — 原始上传，推理成功后**自动删除**；失败的会保留，便于排障
- `cases/` — 转换后的体数据与预览图，按 TTL 清理
- `results/` — 结果 JSON，按 TTL 清理

建议给 `RADAR_DATA_DIR` 单独挂盘并加监控。

### 6.3 日志关注点

```bash
docker logs -f radarserve 2>&1 | grep -E '\[radar\]|Error|Traceback'
```

- `[radar] model warmup done` — 预热完成
- `[radar] model warmup failed: ...` — 权重问题，必查
- `[radar] case=...` — 每例打点（§1.1）
- `[radar] purged N expired case(s)` — TTL 清理

---

## 7. 扩容路径

### 7.1 现状

单进程 + 单 GPU + 内存队列。适合内网小批量使用。

### 7.2 多卡多实例（无需改代码）

若实测 `peak` 远小于显存，可在同一台机器上按卡起多个容器：

```bash
docker run -d --name radarserve-0 --gpus '"device=0"' \
  -p 8001:8000 -v /srv/radarserve/DAMO-RADAR/ckpt:/ckpt:ro -v radarserve-data:/data \
  -e MODEL_ROOT=/ckpt -e CONFIGS_ROOT=/ckpt -e RADAR_DATA_DIR=/data \
  radarserve:latest

docker run -d --name radarserve-1 --gpus '"device=1"' \
  -p 8002:8000 -v /srv/radarserve/DAMO-RADAR/ckpt:/ckpt:ro -v radarserve-data:/data \
  -e MODEL_ROOT=/ckpt -e CONFIGS_ROOT=/ckpt -e RADAR_DATA_DIR=/data \
  radarserve:latest
```

⚠️ **必须先做会话粘滞，否则会随机 404**

任务状态保存在处理它的那个**进程的内存**里。若 `POST /api/cases` 落到实例 0、而轮询落到实例 1，实例 1 会返回 404 —— 它只持有自己处理过的病例（外加重启时从磁盘恢复的旧病例）。

用 nginx `ip_hash` 把同一客户端固定到同一实例：

```nginx
upstream radar {
    ip_hash;
    server 127.0.0.1:8001;
    server 127.0.0.1:8002;
}
```

注意此时**任务队列仍是各实例独立**的，负载均衡只需保证上传分发均匀。

### 7.3 大规模：外部队列

并发再往上，`TaskManager` 的内存 `asyncio.Queue` 会成为瓶颈（且进程重启丢任务）。届时替换为 Celery + Redis，每张 GPU 一个 worker —— `radar_engine.py` 的 `infer_lock` 设计已经预留了这个前提，改造集中在 `task_manager.py`。

---

## 8. 排障

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `/api/health` → `ckpt_missing` | 权重缺失 | 看 `missing_files`，回 §2.2 |
| `/api/health` → `loading` 长时间不转 `ready` | 权重加载慢或失败 | 查日志 `model warmup failed` |
| 上传 413 | 超过 `RADAR_MAX_UPLOAD_MB` | 调大该值，同时调 nginx `client_max_body_size` |
| 上传 429 | 队列已满 | 扩容（§7.2）或调大 `RADAR_MAX_QUEUE_SIZE` |
| 400「体数据超出可处理范围」 | 某空间维度 > `RADAR_MAX_SPATIAL_DIM` | 提供更小范围或更厚层重建 |
| 400「DICOM 目录中未找到可用序列」 | zip 内无有效 DICOM | 检查打包结构 |
| 停留在 `pending` | 检出多个序列未确认期相 | 调 `/series` 接口 |
| 409 | 非 `pending` 状态又调了选序列 | 状态已流转，无需再选 |
| `CUDA out of memory` | 显存不足或跑了多 worker | 确认 `--workers` 为 1；换更大显存 |
| 结果页提示 CT 值范围异常 | HU 标定可疑 | 检查 `RescaleSlope`/`RescaleIntercept`，结果可信度存疑 |
| 重启后任务消失 | 队列在内存 | 已知限制，重跑；量大走 §7.3 |

---

## 9. 上线检查清单

- [ ] `/api/health` 返回 `ready`，`model_loaded: true`
- [ ] `RADAR_CORS_ORIGINS` 已改为实际域名（不再是 `*`）
- [ ] nginx 已设 `client_max_body_size` 与超时
- [ ] 已跑 10-20 例真实数据，`infer` / `peak` 分布已记录（§1.1）
- [ ] 按实测 p95 校准了 `RADAR_MAX_QUEUE_SIZE`
- [ ] `RADAR_DATA_DIR` 单独挂盘并加了磁盘监控
- [ ] 非内网部署时已前置认证
- [ ] 已确认只加载官方权重（`torch.load(..., weights_only=False)` 会执行 pickle）
