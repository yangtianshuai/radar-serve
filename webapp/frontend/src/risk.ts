import type { RiskLevel } from './types'

/**
 * 风险分级阈值。
 *
 * 论文未给出官方判定阈值，后端内置的 0.5 / 0.25 只是保守的展示分级。
 * 科研上应在自有验证集上用 Youden 指数等方法重新标定，所以这里把阈值
 * 做成前端可调并持久化——不同研究会用不同切点，硬编码在代码里没法做。
 */
export const DEFAULT_HIGH = 0.5
export const DEFAULT_MEDIUM = 0.25

export interface Thresholds {
  high: number
  medium: number
}

export const DEFAULT_THRESHOLDS: Thresholds = {
  high: DEFAULT_HIGH,
  medium: DEFAULT_MEDIUM,
}

/** 按给定阈值判定风险等级；概率为 null（器官未检出、未参与评估）返回 na */
export function riskOf(probability: number | null, { high, medium }: Thresholds): RiskLevel {
  if (probability === null) return 'na'
  if (probability >= high) return 'high'
  if (probability >= medium) return 'medium'
  return 'low'
}

const STORAGE_KEY = 'radar.thresholds'

/** 读取上次标定的阈值；读不到或数值不合法时回退默认值 */
export function loadThresholds(): Thresholds {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return DEFAULT_THRESHOLDS
    const parsed = JSON.parse(raw) as Partial<Thresholds>
    const high = Number(parsed.high)
    const medium = Number(parsed.medium)
    if (!Number.isFinite(high) || !Number.isFinite(medium)) return DEFAULT_THRESHOLDS
    // medium 必须严格小于 high，否则分级会退化成一档
    if (high <= 0 || high > 1 || medium < 0 || medium >= high) return DEFAULT_THRESHOLDS
    return { high, medium }
  } catch {
    return DEFAULT_THRESHOLDS
  }
}

export function saveThresholds(thresholds: Thresholds): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(thresholds))
  } catch {
    // 隐私模式下 localStorage 可能不可写，忽略即可
  }
}
