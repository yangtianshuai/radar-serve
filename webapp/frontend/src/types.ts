export type RiskLevel = 'high' | 'medium' | 'low' | 'na'

export interface Finding {
  item: string
  organ: string
  finding: string
  english: string
  probability: number | null
  risk: RiskLevel
  reference_auc: number | null
}

export interface VolumeMeta {
  source_kind: string
  shape_zyx: number[]
  spacing_zyx: number[]
  hu_range: number[]
  series_id: string | null
  series_description: string | null
  rescale_slope: number
  rescale_intercept: number
  warnings: string[]
}

export interface InferenceStats {
  elapsed_sec: number
  num_windows: number
  refine_count: number
  peak_gpu_mem_mb: number | null
  device_name: string
}

export interface CaseResult {
  case_id: string
  findings: Finding[]
  evaluated_count: number
  top_findings: Finding[]
  volume: VolumeMeta | null
  stats: InferenceStats | null
}

export interface SeriesOption {
  series_id: string
  series_number: number
  series_description: string
  num_slices: number
}

/** 金标准判读结论。键不存在表示尚未判读，与「阴性」区分开 */
export type AnnotationLabel = 'positive' | 'negative' | 'uncertain'

export interface CaseAnnotation {
  /** 标签 -> 判读结论。只有已判读的标签才构成 AUC 的有效样本 */
  labels: Record<string, string>
  reader: string
  remark: string
  updated_at: string
}

export interface CaseStatus {
  case_id: string
  status: string
  stage: string
  progress: number
  filename: string
  created_at: string
  updated_at: string
  message: string
  error: string | null
  available_series: SeriesOption[]
  volume: VolumeMeta | null
  preview_count: number
  result: CaseResult | null
  /** 科研金标准，未录入过则为 null */
  annotation: CaseAnnotation | null
  /** 排队位次（1 起算），不在队列中为 null */
  queue_position: number | null
  /** 已请求取消、但任务还在跑（等当前步骤结束） */
  cancel_requested: boolean
  /** 任务已耗时（秒），从创建算起含排队；终态后定格 */
  elapsed_sec: number
}

export interface CaseBrief {
  case_id: string
  status: string
  filename: string
  created_at: string
  updated_at: string
  progress: number
  /** 结果摘要：列表页直接展示，无需再请求详情 */
  evaluated_count: number
  high_risk_count: number
  top_finding: string
  top_probability: number | null
  error: string | null
  /** 已录入金标准的标签数 */
  labeled_count: number
  reader: string
}

export interface CaseListResponse {
  items: CaseBrief[]
  total: number
  offset: number
  limit: number
}

export interface WindowPreset {
  key: string
  window_width: number
  window_level: number
}

export interface Health {
  status: string
  device: string
  cuda_available: boolean
  model_loaded: boolean
  queue_size: number
  missing_files: string[]
  detail: string
  /** MONAI 实际注册到的图像 reader；缺 NibabelReader 时读不了 NIfTI */
  image_readers: string[]
  image_reader_ok: boolean
}

/** 单个标签的自有数据评估结果 */
export interface LabelStat {
  item: string
  organ: string
  finding: string
  english: string
  /** 已判读为阳性的样本数 */
  positives: number
  /** 已判读为阴性的样本数 */
  negatives: number
  /** 自有数据 AUC；正负样本不全时为 null */
  auc: number | null
  /** AUC 的 95% 置信区间（Bootstrap，固定种子）；样本不足时为 null */
  auc_ci_low: number | null
  auc_ci_high: number | null
  /** Youden 指数最优切点 */
  best_threshold: number | null
  sensitivity: number | null
  specificity: number | null
  /** 论文在 MERLIN 外部验证集上的 AUC，用于对照 */
  reference_auc: number | null
}

export interface StatsOverview {
  total: number
  done: number
  labeled: number
}

export interface StatsResponse {
  counts: StatsOverview
  items: LabelStat[]
}

export interface RocPoint {
  fpr: number
  tpr: number
  threshold: number
}

export interface RocResponse {
  item: string
  organ: string
  finding: string
  positives: number
  negatives: number
  auc: number | null
  auc_ci_low: number | null
  auc_ci_high: number | null
  best_threshold: number | null
  sensitivity: number | null
  specificity: number | null
  reference_auc: number | null
  points: RocPoint[]
}
