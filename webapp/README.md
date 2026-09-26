# RadarServe · Web 服务（FastAPI + React）

把 DAMO RADAR 腹部 CT 模型包装成可上传、可查询、可视化的 Web 服务。

- 上传 `.nii` / `.nii.gz`，或打包 DICOM 序列的 `.zip`
- 服务端自动完成 DICOM 排序、HU 标定、重采样与标准化
- 输出 146 项「器官_病灶」阳性概率，按器官分组展示，附公开验证集参考 AUC
- 推理走异步任务队列，前端轮询进度（单例耗时数十秒至数分钟，不能同步等待）

---

## 1. 架构

```
浏览器 (React + Vite)
    │  POST /api/cases      上传
    │  GET  /api/cases/{id} 轮询进度
    ▼
FastAPI  (webapp/backend)
    │  上传落盘 -> 解析/标准化 -> 投递任务
    ▼
单 worker 任务队列 ──► RADAR 推理引擎（常驻显存，互斥锁串行）
                          ├ 预处理：重采样 + HU 截断 + 裁切 + pad
                          ├ 滑窗：37 类器官分割 + 器官级图文对比
                          └ 汇总：146 项病灶阳性概率
```

## 2. 目录

```
webapp/
├── backend/
│   ├── main.py           FastAPI 路由
│   ├── config.py         路径/设备/限额配置（全部支持环境变量）
│   ├── preprocessor.py   DICOM/NIfTI -> 标准体数据（HU 标定、层排序、预览）
│   ├── radar_engine.py   模型单例 + 单体数据推理
│   ├── task_manager.py   任务队列与结果落盘
│   ├── findings_meta.py  146 项标签 / 英文映射 / 参考 AUC
│   ├── findings_meta.json（由 inference_demo.py 自动抽取生成）
│   └── requirements.txt
├── frontend/             React + TypeScript + Vite
└── deploy/               Dockerfile / docker-compose / start.sh
                           deploy_gui.py    图形化部署助手（本机 → 服务器）
```

## 3. 前置条件

**GPU 是必需的。** 3D 滑窗网络在 CPU 上比 GPU 慢一到两个数量级，单例可能从数十秒延长到数十分钟。推荐 A100 80G 或 H20 单卡。

下载权重到项目 `ckpt/`：

```bash
cd download_scripts
python download_checkpoints.py      # checkpoint_radar_pretrain.pth + bert-base-chinese
python download_auxiliary_data.py
```

确认 `ckpt/` 下至少有：
- `checkpoint_radar_pretrain.pth`
- `infer_text_embedding_radar.pt`
- `bert-base-chinese/config.json`

## 4. 部署

> 部署总指南见 [`doc/DEPLOYMENT.md`](doc/DEPLOYMENT.md)，服务端运维（容量规划、监控、扩容、排障）见 [`doc/OPS.md`](doc/OPS.md)。

### 方式 A：Docker Compose（推荐）

```bash
cd webapp/deploy
docker compose up -d --build
docker compose logs -f
```

镜像内已构建好前端，`http://<host>:8000` 直接访问。权重通过 `../../DAMO-RADAR/ckpt:/ckpt:ro` 只读挂载，运行时数据写入 `radarserve-data` 卷。

宿主机需安装 [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html)。

### 方式 B：直接运行

```bash
bash webapp/deploy/deploy.sh native      # 一键：venv + 推理依赖 + 前端构建 + 启动
```

手动分步（等价于上面的脚本）：

```bash
conda create -n radar python=3.10 && conda activate radar
pip install torch==2.4.0+cu121 --index-url https://download.pytorch.org/whl/cu121
pip install -r webapp/backend/requirements.txt   # 仅推理依赖

bash webapp/deploy/start.sh              # 后端 :8000

cd webapp/frontend && npm install && npm run build   # 前端，构建后由后端托管
```

> ⚠️ 不要安装项目根目录的 `requirements.txt`——那是**训练依赖**（含 `streamlit` / `spacy` / `diffusers` / `decord`），且其中 `torch>=1.10.0` 会覆盖掉需要的 `2.4.0+cu121`。

后端会自动托管 `webapp/frontend/dist`（若存在），因此生产环境只开 8000 端口即可。
开发时另起 Vite：`npm run dev`（:5173，已配置 `/api` 代理到 :8000）。

## 5. 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `MODEL_ROOT` / `CONFIGS_ROOT` | `<项目根>/ckpt` | 权重与 tokenizer 目录 |
| `RADAR_DATA_DIR` | `webapp/runtime` | 上传、转换后体数据、结果 |
| `RADAR_DEVICE` | `cuda` | `cuda` / `cpu` |
| `RADAR_MAX_UPLOAD_MB` | `2048` | 上传体积上限 |
| `RADAR_MAX_SPATIAL_DIM` | `1000` | 单空间维度上限，超出直接报错 |
| `RADAR_CORS_ORIGINS` | `*` | 生产环境改成实际域名 |
| `RADAR_RESULT_TTL_DAYS` | `7` | 结果保留天数 |

## 6. 关于 DICOM

服务端做了三件原项目没有的事，缺一不可：

1. **层排序**：按 `ImagePositionPatient` 在层法向上的投影距离排序，缺失时退化到 `InstanceNumber` 再到文件名。顺序错了体数据就是乱的。
2. **HU 标定**：应用 `RescaleSlope` / `RescaleIntercept`。模型预处理按 HU 做 `[-300, 400]` 截断，不转换结果完全不可用。
3. **期相选择**：一个 zip 里常含平扫/动脉期/静脉期/延迟期。检测到多个序列时接口返回 `pending`，前端列出序列供选择（增强腹部 CT 建议选门静脉期）。

另外会做 CT 值范围合理性检查，异常时在结果页给出警告。

## 7. 接口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/health` | 服务与权重状态 |
| POST | `/api/cases` | 上传（multipart，可选 `series_id`） |
| POST | `/api/cases/{id}/series` | 多期相时确认序列 |
| GET | `/api/cases/{id}` | 状态 + 进度 + 结果 + 金标准 |
| GET | `/api/cases` | 历史列表，支持 `keyword`（文件名搜索）/ `status`（逗号分隔）/ `limit` / `offset`，条目含结果摘要与判读进度 |
| DELETE | `/api/cases/{id}` | 删除病例与数据 |
| POST | `/api/cases/{id}/cancel` | 取消排队中或推理中的任务；取消只改状态，产出的文件保留 |
| POST | `/api/cases/{id}/rerun` | **重新推理**失败或已取消的病例；体数据还在时自动跳过预处理 |
| PATCH | `/api/cases/{id}/annotation` | **金标准录入**：增量更新逐标签判读结论（`positive` / `negative` / `uncertain`）与判读者、备注 |
| GET | `/api/export/cases.csv` | **队列长表导出**：一行 = 一个标签，含概率、金标准与阈值重算后的分级，支持 `keyword` / `status` / `high` / `medium` |
| GET | `/api/export/stats.csv` | **统计表导出**：逐标签的 AUC、95% 置信区间、Youden 最优切点与敏感性/特异性 |
| GET | `/api/stats/labels` | **逐标签统计**：自有数据样本量、AUC（含 95% CI）、Youden 最优切点，并对照文献参考 AUC |
| GET | `/api/stats/roc` | 单个标签的 ROC 曲线点（`item` 参数） |
| GET | `/api/cases/{id}/preview/{i}` | 轴位切片 PNG（预处理阶段生成的三张等距快照） |
| GET | `/api/cases/{id}/slice/{i}` | 按需渲染单层切片，`window` 选窗位预设，`ww`/`wl` 自定义窗，`plane` 选 axial / coronal / sagittal |
| GET | `/api/meta/windows` | 窗宽窗位预设列表 |
| GET | `/api/meta/findings` | 标签元数据 |

交互式文档：`http://<host>:8000/docs`

## 8. 科研工作流

前端分「单例判读」与「队列统计」两个视图，构成一条完整的研究闭环：

1. **判读**：上传病例 → MPR 三联视图阅片（冠状/矢状按体素间距等比缩放，点击可交叉定位）→ 拖动调窗。
2. **录入金标准**：结果页每个标签右侧的「阳 / 阴 / ?」逐条录入参考标准。**未判读与判为阴性是两回事**——只有已判读的标签才构成 AUC 的有效样本，「不确定」不计入统计。
3. **标定阈值**：判读阈值可调并持久化，实时重算风险分级；导出的 `risk` 列按当前阈值重算，保证与界面所见一致。
4. **统计**：队列统计视图查看逐标签 AUC（含 95% 置信区间）、Youden 最优切点；点击标签查看 ROC 曲线。
5. **导出**：
   - 单例：结果页「导出 CSV」，一行 = 一个标签，含金标准。
   - 队列：统计页「导出队列长表 CSV」，或直接请求 `/api/export/cases.csv`。
   - 统计表：统计页「导出统计表」，或 `/api/export/stats.csv`——逐标签的 AUC、95% CI、最优阈值与敏感性/特异性，可直接作论文附表。

**关于 AUC 的置信区间**：小样本下点估计极不稳定，本篇的演示数据里 5 阳 7 阴时 AUC 0.743 的 95% CI 是 **0.429–1.000**——只看 0.743 会严重高估把握度。区间用 Bootstrap 法（1000 次重采样，固定随机种子保证可复现）估计，样本极度不均衡算不出来时返回空而不是给一个假区间。

**重新推理**：失败或取消的病例，体数据还在时可在病例条上一键重跑，后端会跳过 DICOM 转换直接进入推理。

导出的 CSV 都带 UTF-8 BOM，Excel 直接打开中文不乱码。

### 界面能力

**应用外壳**：固定顶栏（品牌 / 视图导航 / 服务状态 / 队列位次 / 历史病例 / 关于 / 主题切换）；病例上下文条给出文件名、状态、短 ID，并提供「复制链接」（链接里带 `?case=`，可直接分享某一例）。

**关于**：顶栏 `?` 打开，含项目定位、使用边界（免责声明）与公众号二维码。二维码图片位于 `webapp/frontend/public/wechat-qr.jpg`，对外发布前可自行替换；它在暗色主题下始终保留白底，避免反色导致识别率下降。

**历史病例默认隐藏**，从顶栏按需唤出右侧抽屉：列表本来就只是「偶尔回看一下」，常驻侧栏会白白吃掉影像区四分之一的宽度。抽屉支持点遮罩、`Esc` 或选中病例后自动收起（选中即离开列表）。

**工作台布局**：出结果后自动分栏——左侧 MPR 三联视图，右侧结果栏（概览 / 标签 / 判读记录三个 Tab，内部滚动），影像始终留在视野里。

- **MPR**：轴位 + 冠状 + 矢状，按体素间距**等比**缩放（各向异性数据不会变形）；三个视图之间用**交叉参考线**标出当前层的位置，点击冠状或矢状面可交叉定位到对应层。
- **阅片交互**：拖动调窗（水平改窗宽、垂直改窗位）、自定义 WW/WL 输入、滚轮与方向键翻层、图像比例尺。

**暗色阅片模式**：顶栏一键切换并记住选择（也跟随系统的 `prefers-color-scheme`）；影像区在任何主题下都保持深色底，避免界面亮度干扰对灰度对比的判断。

**状态可见**：上传进度条（传输完成后提示「服务端解析中」，避免停在 100% 让人误解）、排队位次（「前面还有 N 例」）、任务取消、失败一键重试、右下角轻量通知。

**上传前预校验**：单文件按扩展名白名单（`.nii` / `.nii.gz` / `.zip` / DICOM）、多文件按黑名单（图片、视频、文档等一眼不是影像的）与总大小拦截，避免传几分钟才报错。真正的格式判定仍在服务端。

**渲染异常兜底**：结果栏、影像浏览、队列统计各自包在 ErrorBoundary 里。React 18 下一个组件抛异常会卸载整棵树，正在录入的判读会全部丢失——现在崩溃被限制在单个区域内，其余部分照常可用，并给出「重试 / 刷新」。

**快捷键**：`g` 切换单例判读 / 队列统计，`Esc` 关闭当前提示；影像区内 `↑↓ / ←→` 翻层（Shift 加速 5 层）、`PageUp / PageDown` 翻 10 层、`Home / End` 跳到首末层。

## 9. 重要说明

**输出的是阳性概率，不是准确率。** AUC 是模型在某个病种上的整体判别能力（来自外部 MERLIN 测试集），单个病例没有"准确率"这个概念。页面上有 16 个标签能显示参考 AUC，其余标签暂无公开指标。

风险分级阈值（0.5 / 0.25）是保守的展示分级，论文未给出官方阈值，应在自有验证集上用 Youden 指数重新标定——界面上的阈值滑块与 `/api/stats/labels` 返回的最优切点就是为此准备的。

## 10. 已知限制

- 单 worker 串行推理，并发上传会排队；需要横向扩展时请改造成 Celery + Redis 并给每张 GPU 一个 worker。
- 结果存本地 JSON 文件，无鉴权、无用户体系，默认只适合内网使用。
- `torch.load(..., weights_only=False)` 会执行 pickle，务必只加载官方权重。
- Windows 上原项目用 `path.split('/')` 取文件名会失效，本服务全程使用 `pathlib`，但**建议部署在 Linux**。
- **数据目录不能含非 ASCII 字符**（Windows 上尤甚）：SimpleITK 打不开中文绝对路径，预处理与切片渲染会整体失败。若检测到这种情况，`config.py` 会把运行时数据自动回退到 `%LOCALAPPDATA%\radar-runtime` 并打印提示；也可以用 `RADAR_DATA_DIR` 显式指定。
- 模型内部算出了 37 类器官的分割结果，但目前只用于推理，**未落盘也未在界面上展示**——器官体积、分割质量核查这类科研需求还拿不到。
- **取消任务只在阶段边界生效**：`SimpleITK` 的 DICOM 读写与 `torch` 的权重加载都是原子的 C++ 调用，打断不了。所以点取消后要等当前步骤跑完才会停下——能覆盖排队、等 GPU 锁、预处理刚结束这些间隙，但大型 DICOM 序列的转换本身要等它自己结束。
- 队列统计只在内存里遍历已加载的病例，样本量上万后 `/api/stats/*` 的响应会变慢，届时需要改成落库统计。
