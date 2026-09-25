# RadarServe 文档索引

## 服务化实现（本项目新增）

| 文档 | 内容 |
| --- | --- |
| [DEPLOYMENT.md](DEPLOYMENT.md) | **部署总指南**：环境要求、权重准备、三种部署方式、验证与上线检查清单 |
| [OPS.md](OPS.md) | **运维手册**：容量规划、监控、扩容路径、排障表 |
| [../README.md](../README.md) | 服务架构、接口表、DICOM 处理说明、已知限制 |

## 项目与官方文档

| 文档 | 内容 |
| --- | --- |
| [../../README.md](../../README.md) | 项目总览、仓库结构、快速开始 |
| [../../DAMO-RADAR/README.md](../../DAMO-RADAR/README.md) | 官方项目说明、论文引用 |
| [../../DAMO-RADAR/docs/INFERENCE.md](../../DAMO-RADAR/docs/INFERENCE.md) | 官方推理 demo 与 MERLIN 测试集评测 |
| [../../DAMO-RADAR/docs/TRAINING.md](../../DAMO-RADAR/docs/TRAINING.md) | 训练与微调 |
| [../../DAMO-RADAR/docs/PREPROCESS.md](../../DAMO-RADAR/docs/PREPROCESS.md) | 影像与报告预处理 |

## 阅读顺序建议

**第一次部署**：从 [DEPLOYMENT.md](DEPLOYMENT.md) 开始，它覆盖了从零到上线的完整流程。

**上线后运维**：查 [OPS.md](OPS.md) 的容量规划与排障表。

**要改代码**：先看 [../README.md](../README.md) 的架构与接口说明。
