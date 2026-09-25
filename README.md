# RadarServe

**面向增强腹部 CT 的智能分析服务**，把阿里达摩院开源的 [DAMO RADAR](https://github.com/alibaba-damo-academy/damo-radar)（*Science* 2026 专家级通用视觉语言模型）包装成可直接部署、可交互使用的完整系统。

> **免责声明**：本项目与阿里巴巴达摩院**无隶属关系**，是独立的第三方服务化实现。模型权重、核心推理逻辑与网络结构均归原始作者所有。用于研究时请引用其原论文（见 [`DAMO-RADAR/README.md`](DAMO-RADAR/README.md) 的 Citation 一节）。

## 快速开始

```bash
# 1. 下载模型权重（约数 GB）
bash webapp/deploy/deploy.sh docker --download-ckpt

# 2. 构建并启动，浏览器访问 http://<host>:8000
bash webapp/deploy/deploy.sh docker
```

Windows 单机：

```powershell
powershell -ExecutionPolicy Bypass -File webapp\deploy\deploy.ps1 -DownloadCkpt
```

## 能力

**影像接入**

- **上传**：`.nii` / `.nii.gz`，或直接拖入 DICOM 文件与文件夹（也可打包 zip）；上传前做格式与体积预校验，带进度条与取消
- **预处理**：服务端自动完成 DICOM 层排序、HU 标定、重采样与标准化
- **任务管理**：异步队列 + 进度轮询，显示排队位次；可取消，失败/取消后可一键重跑（体数据还在时跳过预处理）

**阅片**

- **MPR 三联视图**：轴位 + 冠状 + 矢状，按体素间距等比缩放（各向异性数据不变形）；交叉参考线标出当前层位置，点击可交叉定位
- **调窗**：6 组预设 + 自定义 WW/WL，鼠标拖动直接调窗（水平改窗宽、垂直改窗位）
- **暗色阅片模式**：一键切换并记忆，影像区在任何主题下都保持深底
- **快捷键**：滚轮 / 方向键翻层，`g` 切换视图，`Esc` 关闭提示

**分析结果**

- **146 项**「器官_病灶」阳性概率，按器官分组，附文献参考 AUC
- **阈值可调**：风险分级阈值实时重算并持久化（论文未给官方阈值，应在自有验证集上标定）

**科研工作流**

- **金标准录入**：逐标签三态判读（阳性 / 阴性 / 不确定），记录判读者与备注
- **统计**：逐标签 AUC（Bootstrap 95% 置信区间）、Youden 最优切点、敏感性 / 特异性，并对照文献参考 AUC
- **导出**：单例 CSV、队列长表 CSV、统计表 CSV——均带 UTF-8 BOM，Excel 直接打开中文不乱码

**工程**

- 模型常驻显存 + GPU 串行推理，单 worker 保证显存不被重复加载挤爆
- 渲染异常按区域兜底，单个组件崩溃不会导致整页白屏、丢失正在录入的判读

## 仓库结构

```
RadarServe/
├── DAMO-RADAR/              上游开源项目（保持原样，便于与官方同步）
│   ├── ckpt/                模型资产：主权重需自行下载；文本 embedding 与报告模板随仓库提供（约 0.4 MB）
│   ├── data/                上游示例数据（约 324 MB，不入库，见下）
│   ├── RADAR_inference/     官方推理代码（服务端复用其中的网络定义）
│   ├── RADAR_train/         训练与微调代码
│   ├── download_scripts/    权重下载脚本
│   ├── docs/                官方文档（推理 / 训练 / 预处理）
│   ├── LICENSE              Apache 2.0
│   └── THIRD_PARTY_LICENSES.md
│
└── webapp/                  本仓库新增的服务化实现
    ├── backend/             FastAPI：推理引擎、任务队列、DICOM/NIfTI 预处理、统计
    ├── frontend/            React：上传、MPR 阅片、结果与统计、金标准录入
    │   └── public/          静态资源（「关于」里的公众号二维码）
    ├── demo/                离线演示页（单文件 HTML + 内联数据，无需后端）
    ├── deploy/              部署脚本与容器配置
    │   ├── deploy.sh        Linux / macOS 一键部署（Docker / 裸机 / macos 模式）
    │   ├── deploy.ps1       Windows 一键部署
    │   ├── Dockerfile       多阶段构建（前端构建 + GPU 后端）
    │   ├── docker-compose.yml
    │   └── start.sh         裸机启动
    └── doc/                 项目文档
        ├── README.md        文档索引
        ├── DEPLOYMENT.md    部署总指南（从零到上线）
        └── OPS.md           运维手册（容量、监控、扩容、排障）
```

边界很清晰：**`DAMO-RADAR/` 是上游代码，尽量不动**（便于日后与官方同步）；**`webapp/` 是本项目的全部新增内容**。服务端只依赖 `DAMO-RADAR/RADAR_inference` 的网络定义和 `DAMO-RADAR/ckpt` 的权重。

### 关于示例数据

`DAMO-RADAR/data/` 下约 **324 MB** 的 NIfTI 示例数据**不随本仓库分发**（`.gitignore` 已排除）。原因有两条：它们来自上游项目与 MERLIN 数据集，属第三方数据、重新分发涉及数据许可；另外单文件接近 GitHub 100 MB 的硬限制，整体也会让仓库臃肿到难以 clone。

需要时按 [`DAMO-RADAR/docs/INFERENCE.md`](DAMO-RADAR/docs/INFERENCE.md) 的说明从上游获取，或直接使用自有数据。

想在**没有 GPU、没有权重**的情况下先看界面效果，直接双击 `webapp/demo/index.html` 即可——数据已内联，不依赖任何后端。

## 文档

| 文档 | 内容 |
| --- | --- |
| [`webapp/doc/DEPLOYMENT.md`](webapp/doc/DEPLOYMENT.md) | **部署总指南**：环境要求、权重准备、三种部署方式、验证与上线清单 |
| [`webapp/doc/OPS.md`](webapp/doc/OPS.md) | 运维手册：容量规划、监控、扩容路径、排障 |
| [`webapp/README.md`](webapp/README.md) | 服务架构、接口表、DICOM 处理说明、已知限制 |
| [`DAMO-RADAR/README.md`](DAMO-RADAR/README.md) | 上游项目说明与论文引用 |
| [`DAMO-RADAR/docs/INFERENCE.md`](DAMO-RADAR/docs/INFERENCE.md) | 官方推理 demo 与 MERLIN 测试集评测 |
| [`DAMO-RADAR/docs/TRAINING.md`](DAMO-RADAR/docs/TRAINING.md) | 训练与微调 |

## 重要说明

**输出的是阳性概率，不是准确率。** AUC 反映模型在某病种上的整体判别能力，单个病例没有"准确率"概念（详见 [`webapp/README.md`](webapp/README.md)）。风险分级阈值（0.5 / 0.25）是保守的展示分级，论文未给出官方阈值，上线前应在自有验证集上重新标定。

本系统**不构成临床诊断意见**，不能替代执业医师判断。

## 许可

- **本项目代码**：[Apache License 2.0](LICENSE)
- **上游代码**：Apache 2.0（见 [`DAMO-RADAR/LICENSE`](DAMO-RADAR/LICENSE)）
- **第三方依赖**：LAVIS (BSD-3) / nnU-Net (Apache-2.0) / MONAI (Apache-2.0) / 3D-ResNets (MIT)，全文见 [`DAMO-RADAR/THIRD_PARTY_LICENSES.md`](DAMO-RADAR/THIRD_PARTY_LICENSES.md)
- **模型资产**：**主权重不随本仓库分发**，部署时从 [HuggingFace](https://huggingface.co/radar-generalist) 下载。`DAMO-RADAR/ckpt/` 下随仓库提供的文本 embedding（`infer_text_embedding_*.pt`）与报告模板 JSON 体积很小，属于上游模型资产的一部分，同样适用其自身许可条款（上游模型卡标注为 CC BY-NC-SA 4.0）。**商用前请与原始作者确认。**

详见 [`NOTICE`](NOTICE)。
