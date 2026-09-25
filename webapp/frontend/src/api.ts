import type {
  CaseAnnotation,
  CaseListResponse,
  CaseStatus,
  Health,
  RocResponse,
  SeriesOption,
  StatsResponse,
  WindowPreset,
} from './types'

const BASE = '/api'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, init)
  const text = await res.text()
  let payload: unknown = null
  try {
    payload = text ? JSON.parse(text) : null
  } catch {
    payload = null
  }
  if (!res.ok) {
    const detail =
      payload && typeof payload === 'object' && 'detail' in payload
        ? String((payload as { detail: unknown }).detail)
        : `HTTP ${res.status}`
    throw new Error(detail)
  }
  return payload as T
}

export interface CreateCaseResponse {
  case_id: string
  status: string
  message: string
  available_series: SeriesOption[]
}

export interface CaseListParams {
  limit?: number
  offset?: number
  keyword?: string
  status?: string
}

export interface UploadHandle {
  /** 上传结束（含服务端解析）后 resolve */
  promise: Promise<CreateCaseResponse>
  abort: () => void
}

/**
 * 上传病例，带进度回调与取消能力。
 *
 * 用 XMLHttpRequest 而不是 fetch：fetch 拿不到上传进度事件，
 * 而 2 GB 的 DICOM 可能要传几分钟，没有进度条用户会以为界面卡死了。
 *
 * 注意 `onProgress` 只反映**传输**进度：到达 1 后服务端还要做 DICOM 解析，
 * 那段时间前端应显示「解析中」而不是停在 100%。
 */
export function uploadCase(
  input: File | File[],
  onProgress?: (fraction: number) => void,
): UploadHandle {
  const form = new FormData()
  const list = Array.isArray(input) ? input : [input]
  if (list.length === 1) {
    form.append('file', list[0])
  } else {
    for (const file of list) {
      // webkitRelativePath 在文件夹选择时才有值，用它保留相对路径信息
      form.append('files', file, file.webkitRelativePath || file.name)
    }
  }

  const xhr = new XMLHttpRequest()
  const promise = new Promise<CreateCaseResponse>((resolve, reject) => {
    xhr.open('POST', `${BASE}/cases`)

    xhr.upload.onprogress = (e) => {
      if (onProgress && e.lengthComputable && e.total > 0) {
        onProgress(e.loaded / e.total)
      }
    }

    xhr.onload = () => {
      let payload: unknown = null
      try {
        payload = xhr.responseText ? JSON.parse(xhr.responseText) : null
      } catch {
        payload = null
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(payload as CreateCaseResponse)
        return
      }
      const detail =
        payload && typeof payload === 'object' && 'detail' in payload
          ? String((payload as { detail: unknown }).detail)
          : `HTTP ${xhr.status}`
      reject(new Error(detail))
    }

    xhr.onerror = () => reject(new Error('网络中断，上传失败'))
    xhr.ontimeout = () => reject(new Error('上传超时'))
    xhr.onabort = () => reject(new DOMException('已取消上传', 'AbortError'))

    xhr.send(form)
  })

  return { promise, abort: () => xhr.abort() }
}

export const api = {
  health: () => request<Health>('/health'),

  /** 请求取消排队中或推理中的任务（已结束的任务会返回 409） */
  cancelCase: (caseId: string) =>
    request<{ ok: boolean }>(`/cases/${caseId}/cancel`, { method: 'POST' }),

  /** 重新推理失败/取消的病例；体数据还在时后端会跳过预处理 */
  rerunCase: (caseId: string) =>
    request<{ ok: boolean; case_id: string }>(`/cases/${caseId}/rerun`, {
      method: 'POST',
    }),

  selectSeries: (caseId: string, seriesId: string) =>
    request<CreateCaseResponse>(`/cases/${caseId}/series`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ series_id: seriesId }),
    }),

  getCase: (caseId: string) => request<CaseStatus>(`/cases/${caseId}`),

  listCases: (params: CaseListParams = {}) => {
    const q = new URLSearchParams()
    q.set('limit', String(params.limit ?? 30))
    q.set('offset', String(params.offset ?? 0))
    const kw = params.keyword?.trim()
    if (kw) q.set('keyword', kw)
    if (params.status) q.set('status', params.status)
    return request<CaseListResponse>(`/cases?${q.toString()}`)
  },

  deleteCase: (caseId: string) =>
    request<{ ok: boolean }>(`/cases/${caseId}`, { method: 'DELETE' }),

  /**
   * 增量更新金标准：只提交变化的那几条标签，值为空串表示删除该条标注。
   * 后端返回合并后的全量 annotation。
   */
  setAnnotation: (
    caseId: string,
    patch: { labels?: Record<string, string>; reader?: string; remark?: string },
  ) =>
    request<CaseAnnotation>(`/cases/${caseId}/annotation`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    }),

  previewUrl: (caseId: string, index: number) =>
    `${BASE}/cases/${caseId}/preview/${index}`,

  /**
   * 按需渲染的单层切片。
   * `ww` / `wl` 显式给出时覆盖 `window` 预设（后端同样以显式值为准）；
   * `plane` 为 axial / coronal / sagittal，供 MPR 使用。
   */
  sliceUrl: (
    caseId: string,
    index: number,
    opts: { window?: string; ww?: number; wl?: number; plane?: string } = {},
  ) => {
    const q = new URLSearchParams()
    if (opts.window) q.set('window', opts.window)
    if (opts.ww !== undefined && opts.wl !== undefined) {
      q.set('ww', String(opts.ww))
      q.set('wl', String(opts.wl))
    }
    if (opts.plane) q.set('plane', opts.plane)
    const suffix = q.toString()
    return `${BASE}/cases/${caseId}/slice/${index}${suffix ? `?${suffix}` : ''}`
  },

  windows: () =>
    request<{ items: WindowPreset[] }>('/meta/windows').then((r) => r.items),

  /** 各标签的自有数据 AUC / 最优切点 */
  statsLabels: () => request<StatsResponse>('/stats/labels'),

  /** 单个标签的 ROC 曲线点 */
  statsRoc: (item: string) =>
    request<RocResponse>(`/stats/roc?item=${encodeURIComponent(item)}`),

  /**
   * 队列长表导出的下载地址。
   * 阈值由前端传入，保证导出的 risk 列与界面上标定的一致。
   */
  /** 逐标签统计表（AUC + 置信区间 + 最优切点）的下载地址 */
  exportStatsUrl: () => `${BASE}/export/stats.csv`,

  exportCasesUrl: (params: { keyword?: string; status?: string; high: number; medium: number }) => {
    const q = new URLSearchParams()
    if (params.keyword?.trim()) q.set('keyword', params.keyword.trim())
    if (params.status) q.set('status', params.status)
    q.set('high', String(params.high))
    q.set('medium', String(params.medium))
    return `${BASE}/export/cases.csv?${q.toString()}`
  },
}
