"""API 数据结构。"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SeriesOption(BaseModel):
    series_id: str
    series_number: int = 0
    series_description: str = ""
    num_slices: int = 0


class CaseCreateResponse(BaseModel):
    case_id: str
    status: str
    message: str = ""
    available_series: list[SeriesOption] = Field(default_factory=list)


class VolumeMeta(BaseModel):
    source_kind: str
    shape_zyx: list[int]
    spacing_zyx: list[float]
    hu_range: list[float]
    series_id: str | None = None
    series_description: str | None = None
    rescale_slope: float = 1.0
    rescale_intercept: float = 0.0
    warnings: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    item: str
    organ: str
    finding: str
    english: str
    probability: float | None = None
    risk: str = "na"
    reference_auc: float | None = None


class InferenceStats(BaseModel):
    """单次推理的资源开销，用于容量规划。"""

    elapsed_sec: float = 0.0
    num_windows: int = 0
    refine_count: int = 0
    peak_gpu_mem_mb: float | None = None
    device_name: str = ""


class CaseResult(BaseModel):
    case_id: str
    findings: list[Finding]
    evaluated_count: int
    top_findings: list[Finding]
    volume: VolumeMeta | None = None
    stats: InferenceStats | None = None


class CaseAnnotation(BaseModel):
    """科研用的参考标准（金标准）。

    `labels` 是「标签 -> 判读结论」的稀疏映射，只记录已判读的标签：
    键不存在表示**尚未判读**，与「判读为阴性」是两回事——计算 AUC 时
    只有已判读的标签才构成有效样本。
    """

    labels: dict[str, str] = Field(default_factory=dict)
    reader: str = ""
    remark: str = ""
    updated_at: str = ""


class CaseStatus(BaseModel):
    case_id: str
    status: str
    stage: str = ""
    progress: float = 0.0
    filename: str = ""
    created_at: str = ""
    updated_at: str = ""
    message: str = ""
    error: str | None = None
    available_series: list[SeriesOption] = Field(default_factory=list)
    volume: VolumeMeta | None = None
    preview_count: int = 0
    result: CaseResult | None = None
    annotation: CaseAnnotation | None = None
    #: 排队位次（1 起算）；不在队列中为 None。前端据此显示「前面还有几例」
    queue_position: int | None = None
    #: 已收到取消请求但尚未落到终态，前端据此显示「取消中」
    cancel_requested: bool = False
    #: 任务已耗时（秒），从上传完成算起。运行中随轮询增长，终态后定格
    elapsed_sec: float = 0.0


class CaseBrief(BaseModel):
    case_id: str
    status: str
    filename: str
    created_at: str
    updated_at: str = ""
    progress: float = 0.0
    # ---- 结果摘要：供历史列表直接展示，避免为每条记录再请求一次详情 ----
    evaluated_count: int = 0
    high_risk_count: int = 0
    top_finding: str = ""
    top_probability: float | None = None
    error: str | None = None
    # ---- 金标准：列表页据此显示判读进度，也是批量导出/统计的筛选依据 ----
    labeled_count: int = 0
    reader: str = ""


class CaseListResponse(BaseModel):
    items: list[CaseBrief]
    total: int = 0
    offset: int = 0
    limit: int = 50


class FindingsMeta(BaseModel):
    total: int
    organs: list[str]
    items: list[dict[str, Any]]
    reference_auc_note: str
    disclaimer: str


class HealthResponse(BaseModel):
    # 字段 model_loaded 会命中 pydantic 的 model_ 保护命名空间，这里显式放开
    model_config = ConfigDict(protected_namespaces=())

    status: str
    device: str
    cuda_available: bool
    model_loaded: bool
    queue_size: int = 0
    missing_files: list[str] = Field(default_factory=list)
    detail: str = ""
    #: MONAI 实际注册到的图像 reader；缺 NibabelReader 时读不了 NIfTI
    image_readers: list[str] = Field(default_factory=list)
    image_reader_ok: bool = True
