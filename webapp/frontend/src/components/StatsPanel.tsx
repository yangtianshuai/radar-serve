import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { loadThresholds, type Thresholds } from '../risk'
import type { LabelStat, RocResponse, StatsResponse } from '../types'

function fixed(value: number | null, digits = 3) {
  return value === null || value === undefined ? '—' : value.toFixed(digits)
}

function percent(value: number | null) {
  return value === null || value === undefined ? '—' : `${(value * 100).toFixed(1)}%`
}

/** ROC 曲线：纯 SVG 手绘，不引绘图库 */
function RocChart({ data, threshold }: { data: RocResponse; threshold: Thresholds }) {
  const size = 280
  const pad = 36
  const plot = size - pad * 2

  const xy = (fpr: number, tpr: number) => [pad + fpr * plot, pad + (1 - tpr) * plot] as const
  const path = data.points
    .map((p, i) => {
      const [x, y] = xy(p.fpr, p.tpr)
      return `${i ? 'L' : 'M'}${x.toFixed(1)},${y.toFixed(1)}`
    })
    .join(' ')

  // 当前标定阈值在曲线上对应的点（找阈值最接近的那个点）
  const current = data.points.reduce<null | (typeof data.points)[number]>((best, p) => {
    if (best === null) return p
    return Math.abs(p.threshold - threshold.high) < Math.abs(best.threshold - threshold.high)
      ? p
      : best
  }, null)
  const currentXY = current ? xy(current.fpr, current.tpr) : null

  return (
    <svg className="roc-chart" viewBox={`0 0 ${size} ${size}`} role="img" aria-label="ROC 曲线">
      <rect x={pad} y={pad} width={plot} height={plot} className="roc-bg" />
      {/* 随机分类器的对角线 */}
      <line x1={pad} y1={pad + plot} x2={pad + plot} y2={pad} className="roc-diagonal" />
      <path d={path} className="roc-line" />
      {currentXY && (
        <circle cx={currentXY[0]} cy={currentXY[1]} r={4} className="roc-point">
          <title>
            当前阈值 {(threshold.high * 100).toFixed(0)}%：敏感性 {percent(current!.tpr)}、
            特异性 {percent(1 - current!.fpr)}
          </title>
        </circle>
      )}
      <text x={size / 2} y={size - 6} className="roc-axis">
        1 - 特异性（假阳性率）
      </text>
      <text
        x={12}
        y={size / 2}
        className="roc-axis"
        transform={`rotate(-90 12 ${size / 2})`}
        textAnchor="middle"
      >
        敏感性（真阳性率）
      </text>
    </svg>
  )
}

export default function StatsPanel() {
  const [stats, setStats] = useState<StatsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [roc, setRoc] = useState<RocResponse | null>(null)
  const [rocError, setRocError] = useState<string | null>(null)
  const [thresholds] = useState<Thresholds>(loadThresholds)

  const reload = useCallback(() => {
    setLoading(true)
    setError(null)
    api
      .statsLabels()
      .then(setStats)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [])

  useEffect(() => {
    reload()
  }, [reload])

  const openRoc = async (item: string) => {
    setSelected(item)
    setRoc(null)
    setRocError(null)
    try {
      setRoc(await api.statsRoc(item))
    } catch (e) {
      setRocError(e instanceof Error ? e.message : String(e))
    }
  }

  const exportUrl = api.exportCasesUrl({
    status: 'done',
    high: thresholds.high,
    medium: thresholds.medium,
  })

  const counts = stats?.counts
  const usable = stats?.items.filter((i) => i.auc !== null) ?? []

  return (
    <>
      <section className="panel">
        <div className="result-head">
          <h2>队列统计</h2>
          <div className="stats-actions">
            <span className="muted">
              {counts ? `总 ${counts.total} · 完成 ${counts.done} · 已标注 ${counts.labeled}` : '—'}
            </span>
            <button type="button" className="ghost" onClick={reload} disabled={loading}>
              {loading ? '刷新中…' : '刷新'}
            </button>
            <button
              type="button"
              className="ghost"
              onClick={() => {
                window.location.href = api.exportStatsUrl()
              }}
              title="导出逐标签的 AUC、置信区间与最优切点，可直接放进论文附表"
            >
              导出统计表
            </button>
            <button
              type="button"
              className="ghost"
              onClick={() => {
                window.location.href = exportUrl
              }}
            >
              导出队列长表 CSV
            </button>
          </div>
        </div>

        <p className="muted">
          只有同时存在阳性与阴性样本的标签才能算出 AUC。导出的长表包含每个病例的 146 项概率、
          当前阈值下的分级以及金标准，可直接用于统计软件。
          <br />
          导出的 <code>risk</code> 列按当前标定阈值（高风险 ≥ {(thresholds.high * 100).toFixed(0)}%）重算。
        </p>

        {error && <p className="error-text">{error}</p>}

        {!loading && stats && stats.items.length === 0 && (
          <p className="muted">
            还没有任何判读记录。请在「单例判读」里用每行右侧的「阳 / 阴 / ?」录入金标准，
            至少要有阳性与阴性样本各一例才能计算 AUC。
          </p>
        )}

        {stats && stats.items.length > 0 && (
          <table className="stats-table">
            <thead>
              <tr>
                <th>标签</th>
                <th>器官</th>
                <th>阳性</th>
                <th>阴性</th>
                <th>AUC（95% CI）</th>
                <th>最优阈值</th>
                <th>敏感性</th>
                <th>特异性</th>
                <th>参考 AUC</th>
              </tr>
            </thead>
            <tbody>
              {stats.items.map((row: LabelStat) => (
                <tr
                  key={row.item}
                  className={selected === row.item ? 'active' : ''}
                  onClick={() => openRoc(row.item)}
                >
                  <td>{row.finding}</td>
                  <td className="muted">{row.organ}</td>
                  <td>{row.positives}</td>
                  <td>{row.negatives}</td>
                  <td className="stats-auc">
                    {fixed(row.auc)}
                    {row.auc_ci_low !== null && row.auc_ci_high !== null && (
                      <span className="stats-ci">
                        {` (${fixed(row.auc_ci_low, 2)}–${fixed(row.auc_ci_high, 2)})`}
                      </span>
                    )}
                  </td>
                  <td>{percent(row.best_threshold)}</td>
                  <td>{percent(row.sensitivity)}</td>
                  <td>{percent(row.specificity)}</td>
                  <td className="muted">{fixed(row.reference_auc)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {selected && (
        <section className="panel">
          <div className="result-head">
            <h2>ROC · {roc ? `${roc.organ} · ${roc.finding}` : selected}</h2>
            {roc && <span className="muted">点击其他标签可切换</span>}
          </div>

          {rocError && <p className="error-text">{rocError}</p>}
          {!roc && !rocError && <p className="muted">加载中…</p>}

          {roc && (
            <div className="roc-layout">
              <RocChart data={roc} threshold={thresholds} />
              <div className="roc-metrics">
                <div>
                  <span className="label">样本</span>
                  <span>
                    阳性 {roc.positives} / 阴性 {roc.negatives}
                  </span>
                </div>
                <div>
                  <span className="label">AUC（自有数据）</span>
                  <span className="stats-auc">
                    {fixed(roc.auc)}
                    {roc.auc_ci_low !== null && roc.auc_ci_high !== null && (
                      <span className="stats-ci">
                        {` 95% CI ${fixed(roc.auc_ci_low, 2)}–${fixed(roc.auc_ci_high, 2)}`}
                      </span>
                    )}
                  </span>
                </div>
                <div>
                  <span className="label">参考 AUC（MERLIN）</span>
                  <span>{fixed(roc.reference_auc)}</span>
                </div>
                <div>
                  <span className="label">Youden 最优阈值</span>
                  <span>{percent(roc.best_threshold)}</span>
                </div>
                <div>
                  <span className="label">该阈值下敏感性 / 特异性</span>
                  <span>
                    {percent(roc.sensitivity)} / {percent(roc.specificity)}
                  </span>
                </div>
                <p className="muted roc-note">
                  曲线上的点按分数降序排列，每个不同的概率都是一个候选切点。
                  点越小样本量越少，AUC 的置信区间会非常宽——报结果时建议附样本量
                  {usable.length > 0 && `（当前队列共 ${usable.length} 个标签可算 AUC）`}。
                </p>
              </div>
            </div>
          )}
        </section>
      )}
    </>
  )
}
