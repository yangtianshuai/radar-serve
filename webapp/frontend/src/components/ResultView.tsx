import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api } from '../api'
import { downloadCsv, safeFilename, type CsvValue } from '../csv'
import { loadThresholds, riskOf, saveThresholds, type Thresholds } from '../risk'
import { useToast } from '../toast'
import type {
  AnnotationLabel,
  CaseAnnotation,
  CaseStatus,
  Finding,
  LabelStat,
  RiskLevel,
} from '../types'

const RISK_TEXT: Record<RiskLevel, string> = {
  high: '高风险',
  medium: '中风险',
  low: '低风险',
  na: '未评估',
}

const LABEL_TEXT: Record<AnnotationLabel, string> = {
  positive: '阳性',
  negative: '阴性',
  uncertain: '不确定',
}

/** 三态判读按钮的显示顺序 */
const LABEL_ORDER: AnnotationLabel[] = ['positive', 'negative', 'uncertain']

type Filter = 'all' | 'high' | 'medium' | 'evaluated' | 'labeled' | 'unlabeled'
type SortMode = 'organ' | 'probability'
type Tab = 'overview' | 'findings' | 'record'

function pct(value: number) {
  return `${(value * 100).toFixed(1)}%`
}

function ProbBar({ value, risk }: { value: number | null; risk: RiskLevel }) {
  if (value === null) {
    return <span className="prob-na">该器官未检出，未参与评估</span>
  }
  return (
    <div className="prob">
      <div className="prob-track">
        <div className={`prob-fill risk-${risk}`} style={{ width: `${Math.max(value * 100, 1.5)}%` }} />
      </div>
      <span className={`prob-value risk-text-${risk}`}>{pct(value)}</span>
    </div>
  )
}

/** 三态判读：再点一次同一档即取消该标签的标注 */
function LabelToggle({
  value,
  onToggle,
}: {
  value: string | undefined
  onToggle: (label: AnnotationLabel) => void
}) {
  return (
    <div className="label-toggle">
      {LABEL_ORDER.map((label) => (
        <button
          key={label}
          type="button"
          className={`label-btn label-${label} ${value === label ? 'active' : ''}`}
          title={
            value === label ? `已判读为${LABEL_TEXT[label]}，再次点击取消` : `判读为${LABEL_TEXT[label]}`
          }
          onClick={(e) => {
            // 行本身可点开详情卡，判读按钮不能把它一起触发掉
            e.stopPropagation()
            onToggle(label)
          }}
        >
          {label === 'positive' ? '阳' : label === 'negative' ? '阴' : '?'}
        </button>
      ))}
    </div>
  )
}

export default function ResultView({
  status,
  onAnnotationChange,
}: {
  status: CaseStatus
  onAnnotationChange?: (annotation: CaseAnnotation) => void
}) {
  const result = status.result
  const toast = useToast()
  const [tab, setTab] = useState<Tab>('overview')
  const [thresholds, setThresholds] = useState<Thresholds>(loadThresholds)
  const [filter, setFilter] = useState<Filter>('all')
  const [organ, setOrgan] = useState('all')
  const [keyword, setKeyword] = useState('')
  const [sortMode, setSortMode] = useState<SortMode>('organ')
  const [labels, setLabels] = useState<Record<string, string>>(
    status.annotation?.labels ?? {},
  )
  const [reader, setReader] = useState(status.annotation?.reader ?? '')
  const [remark, setRemark] = useState(status.annotation?.remark ?? '')
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)
  /** 展开详情的标签；跨面板把「单例概率」与「队列统计」串起来 */
  const [selectedItem, setSelectedItem] = useState<string | null>(null)
  const [tagStats, setTagStats] = useState<Record<string, LabelStat>>({})
  const statsLoadedRef = useRef(false)

  // 切换病例时以服务端为准，避免上一例的标注残留在界面上
  // （只依赖 case_id：轮询返回的新对象不该覆盖正在进行的本地编辑）
  useEffect(() => {
    setLabels(status.annotation?.labels ?? {})
    setReader(status.annotation?.reader ?? '')
    setRemark(status.annotation?.remark ?? '')
    setSaveError(null)
    setTab('overview')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status.case_id])

  useEffect(() => {
    saveThresholds(thresholds)
  }, [thresholds])

  const patchAnnotation = useCallback(
    async (patch: {
      labels?: Record<string, string>
      reader?: string
      remark?: string
    }) => {
      setSaving(true)
      setSaveError(null)
      try {
        const annotation = await api.setAnnotation(status.case_id, patch)
        setLabels(annotation.labels)
        setReader(annotation.reader)
        setRemark(annotation.remark)
        onAnnotationChange?.(annotation)
        // 逐标签录入时按钮本身就是反馈；判读者/备注是盲操作，需要明确提示
        if (patch.reader !== undefined || patch.remark !== undefined) {
          toast.success('判读信息已保存')
        }
      } catch (e) {
        const message = e instanceof Error ? e.message : String(e)
        setSaveError(message)
        toast.error(`保存失败：${message}`)
      } finally {
        setSaving(false)
      }
    },
    [status.case_id, onAnnotationChange],
  )

  const toggleLabel = (item: string, label: AnnotationLabel) => {
    const next = labels[item] === label ? '' : label
    // 乐观更新：点一下立刻反馈，失败时以服务端返回覆盖
    const optimistic = { ...labels }
    if (next) optimistic[item] = next
    else delete optimistic[item]
    setLabels(optimistic)
    void patchAnnotation({ labels: { [item]: next } })
  }

  /** 按当前阈值重新分级；概率为空的标签保持 na */
  const decorated = useMemo<Finding[]>(() => {
    if (!result) return []
    return result.findings.map((f) => ({ ...f, risk: riskOf(f.probability, thresholds) }))
  }, [result, thresholds])

  const topDecorated = useMemo<Finding[]>(() => {
    if (!result) return []
    return result.top_findings.map((f) => ({ ...f, risk: riskOf(f.probability, thresholds) }))
  }, [result, thresholds])

  const counts = useMemo(() => {
    let high = 0
    let medium = 0
    let labeled = 0
    const byLabel: Record<AnnotationLabel, number> = { positive: 0, negative: 0, uncertain: 0 }
    for (const f of decorated) {
      if (f.risk === 'high') high += 1
      if (f.risk === 'medium') medium += 1
      const value = labels[f.item] as AnnotationLabel | undefined
      if (value && value in byLabel) {
        labeled += 1
        byLabel[value] += 1
      }
    }
    return { high, medium, labeled, byLabel }
  }, [decorated, labels])

  /** 队列统计按需拉一次并缓存：详情卡里要显示该标签的自有 AUC */
  const loadTagStats = useCallback(async () => {
    if (statsLoadedRef.current) return
    statsLoadedRef.current = true
    try {
      const res = await api.statsLabels()
      setTagStats(Object.fromEntries(res.items.map((i) => [i.item, i])))
    } catch {
      // 统计接口不可用不影响详情里的单例信息，下次再试
      statsLoadedRef.current = false
    }
  }, [])

  /** 点标签行：展开/收起详情 */
  const openTag = useCallback(
    (item: string) => {
      setSelectedItem((current) => (current === item ? null : item))
      void loadTagStats()
    },
    [loadTagStats],
  )

  /** 从概览的 top10 跳过来：直接切到标签页并展开，不做 toggle */
  const jumpToTag = (item: string) => {
    setTab('findings')
    setSelectedItem(item)
    void loadTagStats()
  }

  const selectedFinding = useMemo(
    () => decorated.find((f) => f.item === selectedItem) ?? null,
    [decorated, selectedItem],
  )

  const organs = useMemo(() => {
    if (!result) return []
    return [...new Set(result.findings.map((f) => f.organ))]
  }, [result])

  const groups = useMemo<[string, Finding[]][]>(() => {
    if (!result) return []
    const kw = keyword.trim().toLowerCase()

    const items = decorated.filter((f) => {
      if (filter === 'high' && f.risk !== 'high') return false
      if (filter === 'medium' && f.risk !== 'medium') return false
      if (filter === 'evaluated' && f.probability === null) return false
      if (filter === 'labeled' && !labels[f.item]) return false
      if (filter === 'unlabeled' && labels[f.item]) return false
      if (organ !== 'all' && f.organ !== organ) return false
      if (
        kw &&
        !f.finding.toLowerCase().includes(kw) &&
        !f.english.toLowerCase().includes(kw) &&
        !f.organ.includes(keyword.trim())
      )
        return false
      return true
    })

    // 按概率平铺：decorated 本身已是概率降序（未评估的排在最后）
    if (sortMode === 'probability') return [['', items]]

    const map = new Map<string, Finding[]>()
    for (const f of items) {
      const arr = map.get(f.organ) ?? []
      arr.push(f)
      map.set(f.organ, arr)
    }
    return [...map.entries()]
  }, [result, decorated, filter, organ, keyword, labels, sortMode])

  const handleExport = () => {
    if (!result) return
    const header = [
      'case_id',
      'filename',
      'created_at',
      'reader',
      'item',
      'organ',
      'finding',
      'english',
      'probability',
      'risk',
      'threshold_high',
      'threshold_medium',
      'label',
      'reference_auc',
      'remark',
    ]
    const rows: CsvValue[][] = [
      header,
      ...decorated.map((f) => [
        status.case_id,
        status.filename,
        status.created_at,
        reader,
        f.item,
        f.organ,
        f.finding,
        f.english,
        f.probability,
        f.risk,
        thresholds.high,
        thresholds.medium,
        labels[f.item] ?? '',
        f.reference_auc,
        remark,
      ]),
    ]
    downloadCsv(`radar_${status.case_id}_${safeFilename(status.filename)}.csv`, rows)
  }

  if (!result) return null
  const volume = result.volume
  const stats = result.stats

  return (
    <section className="panel result-panel">
      <header className="result-panel-head">
        <h2>分析结果</h2>
        <div className="result-head-actions">
          {saving && <span className="muted">保存中…</span>}
          <button type="button" className="ghost small" onClick={handleExport}>
            导出 CSV
          </button>
        </div>
      </header>

      <nav className="result-tabs">
        {(
          [
            ['overview', '概览'],
            ['findings', `标签 ${counts.labeled}/${result.findings.length}`],
            ['record', '判读记录'],
          ] as [Tab, string][]
        ).map(([key, text]) => (
          <button
            key={key}
            className={tab === key ? 'active' : ''}
            onClick={() => setTab(key)}
          >
            {text}
          </button>
        ))}
      </nav>

      {saveError && <p className="error-text">{saveError}</p>}

      <div className="result-body">
        {tab === 'overview' && (
          <>
            <div className="result-section">
              <h3>病例概览</h3>
              <div className="overview-grid">
                <div>
                  <span className="label">来源</span>
                  <span>
                    {volume ? (volume.source_kind === 'dicom' ? 'DICOM 序列' : 'NIfTI') : '—'}
                  </span>
                </div>
                <div>
                  <span className="label" title="体数据尺寸，Z × Y × X">
                    尺寸
                  </span>
                  <span>{volume ? volume.shape_zyx.join(' × ') : '—'}</span>
                </div>
                <div>
                  <span className="label" title="体素间距，单位 mm">
                    体素间距
                  </span>
                  <span>
                    {volume ? volume.spacing_zyx.map((v) => v.toFixed(2)).join(' × ') : '—'}
                  </span>
                </div>
                <div>
                  <span className="label" title="CT 值范围，单位 HU">
                    CT 值
                  </span>
                  <span>
                    {volume
                      ? `${volume.hu_range[0].toFixed(0)} ~ ${volume.hu_range[1].toFixed(0)} HU`
                      : '—'}
                  </span>
                </div>
                <div>
                  <span className="label">已评估标签</span>
                  <span>
                    {result.evaluated_count} / {result.findings.length}
                  </span>
                </div>
                <div>
                  <span className="label">推理耗时</span>
                  <span title="仅模型推理耗时，不含上传与预处理">
                    {stats ? `${stats.elapsed_sec.toFixed(1)} s` : '—'}
                  </span>
                </div>
              </div>

              {/* 工程指标默认收起：它们是容量规划与排障用的，判读时并不关心 */}
              <details className="tech-details">
                <summary>技术细节（滑窗数、显存峰值、设备）</summary>
                <div className="overview-grid">
                  <div>
                    <span className="label">滑窗 / 补推</span>
                    <span title="分割滑窗数与未覆盖器官的补充推理次数，是耗时的主要来源">
                      {stats ? `${stats.num_windows} / ${stats.refine_count}` : '—'}
                    </span>
                  </div>
                  <div>
                    <span className="label">显存峰值</span>
                    <span title="本次推理的峰值显存分配量，不含 CUDA context 与碎片">
                      {stats
                        ? stats.peak_gpu_mem_mb !== null
                          ? `${stats.peak_gpu_mem_mb.toFixed(0)} MB`
                          : 'CPU 推理'
                        : '—'}
                    </span>
                  </div>
                  <div>
                    <span className="label">推理设备</span>
                    <span>{stats?.device_name ?? '—'}</span>
                  </div>
                </div>
              </details>

              {volume?.series_description && (
                <p className="muted">使用序列：{volume.series_description}</p>
              )}

              {volume && volume.warnings.length > 0 && (
                <ul className="warn-list">
                  {volume.warnings.map((w) => (
                    <li key={w}>{w}</li>
                  ))}
                </ul>
              )}
            </div>

            <div className="result-section">
              <div className="result-section-head">
                <h3>判读阈值</h3>
                <span className="muted">
                  高风险 {counts.high} · 中风险 {counts.medium}
                </span>
              </div>
              <p className="muted">
                论文未给出官方阈值，默认 0.5 / 0.25 仅为保守展示，应由自有数据标定；
                调整只影响展示与导出的 <code>risk</code> 列。
              </p>

              <div className="threshold-grid">
                <label className="threshold-item">
                  <span>
                    高风险 ≥ <strong>{(thresholds.high * 100).toFixed(0)}%</strong>
                  </span>
                  <input
                    type="range"
                    min={0.05}
                    max={0.95}
                    step={0.01}
                    value={thresholds.high}
                    onChange={(e) => {
                      const high = Number(e.target.value)
                      setThresholds((t) => ({ high, medium: Math.min(t.medium, high - 0.01) }))
                    }}
                  />
                </label>

                <label className="threshold-item">
                  <span>
                    中风险 ≥ <strong>{(thresholds.medium * 100).toFixed(0)}%</strong>
                  </span>
                  <input
                    type="range"
                    min={0}
                    max={0.9}
                    step={0.01}
                    value={thresholds.medium}
                    onChange={(e) => {
                      const medium = Number(e.target.value)
                      setThresholds((t) => ({ ...t, medium: Math.min(medium, t.high - 0.01) }))
                    }}
                  />
                </label>

                <button
                  type="button"
                  className="ghost small"
                  onClick={() => setThresholds({ high: 0.5, medium: 0.25 })}
                >
                  恢复默认
                </button>
              </div>
            </div>

            <div className="result-section">
              <h3>概率最高的 10 项</h3>
              <p className="muted">
                表中数值是模型判断该病灶存在的<strong>阳性概率</strong>，不是准确率。
              </p>
              <div className="top-grid">
                {topDecorated.map((f) => (
                  <button
                    key={f.item}
                    type="button"
                    className={`top-card risk-border-${f.risk}`}
                    title={`${f.english}（点击查看该标签详情）`}
                    onClick={() => jumpToTag(f.item)}
                  >
                    <span className="top-organ">{f.organ}</span>
                    <span className="top-name">{f.finding}</span>
                    <span className={`risk-tag risk-${f.risk}`}>{RISK_TEXT[f.risk]}</span>
                    {labels[f.item] && (
                      <span className={`top-label label-text-${labels[f.item]}`}>
                        金标 {LABEL_TEXT[labels[f.item] as AnnotationLabel] ?? labels[f.item]}
                      </span>
                    )}
                    <span className="top-prob">
                      {f.probability !== null ? pct(f.probability) : '—'}
                    </span>
                  </button>
                ))}
              </div>
            </div>

            {status.preview_count > 0 && (
              <div className="result-section">
                <h3>轴位快照</h3>
                <div className="preview-row">
                  {Array.from({ length: status.preview_count }).map((_, i) => (
                    <figure key={i}>
                      <img src={api.previewUrl(status.case_id, i)} alt={`轴位切片 ${i + 1}`} />
                      <figcaption>切片 {i + 1}</figcaption>
                    </figure>
                  ))}
                </div>
              </div>
            )}
          </>
        )}

        {tab === 'findings' && (
          <div className="result-section">
            <p className="muted">
              右侧「阳 / 阴 / ?」录入参考标准；未判读与判为阴性是两回事，只有已判读的才进 AUC 统计。
            </p>

            <div className="filters">
              <select value={organ} onChange={(e) => setOrgan(e.target.value)}>
                <option value="all">全部器官</option>
                {organs.map((o) => (
                  <option key={o} value={o}>
                    {o}
                  </option>
                ))}
              </select>
              <input
                type="search"
                placeholder="搜索中文/英文名"
                value={keyword}
                onChange={(e) => setKeyword(e.target.value)}
              />
              <div className="segmented compact">
                {(
                  [
                    ['all', '全部'],
                    ['high', '高风险'],
                    ['medium', '中风险'],
                    ['evaluated', '已评估'],
                    ['labeled', '已标注'],
                    ['unlabeled', '未标注'],
                  ] as [Filter, string][]
                ).map(([key, text]) => (
                  <button
                    key={key}
                    className={filter === key ? 'active' : ''}
                    onClick={() => setFilter(key)}
                  >
                    {text}
                  </button>
                ))}
              </div>
              <div className="segmented compact">
                {(
                  [
                    ['organ', '按器官'],
                    ['probability', '按概率'],
                  ] as [SortMode, string][]
                ).map(([key, text]) => (
                  <button
                    key={key}
                    className={sortMode === key ? 'active' : ''}
                    onClick={() => setSortMode(key)}
                  >
                    {text}
                  </button>
                ))}
              </div>
            </div>

            {selectedFinding &&
              (() => {
                const stat = tagStats[selectedFinding.item]
                return (
                  <div className="tag-detail">
                    <div className="tag-detail-head">
                      <strong>{selectedFinding.finding}</strong>
                      <span className="muted">{selectedFinding.english}</span>
                      <span className={`risk-tag risk-${selectedFinding.risk}`}>
                        {RISK_TEXT[selectedFinding.risk]}
                      </span>
                    </div>

                    <div className="tag-detail-grid">
                      <div>
                        <span className="label">器官</span>
                        <span>{selectedFinding.organ}</span>
                      </div>
                      <div>
                        <span className="label">本例阳性概率</span>
                        <span>
                          {selectedFinding.probability !== null
                            ? pct(selectedFinding.probability)
                            : '未评估'}
                        </span>
                      </div>
                      <div>
                        <span className="label">参考 AUC（文献）</span>
                        <span>
                          {selectedFinding.reference_auc !== null
                            ? selectedFinding.reference_auc.toFixed(3)
                            : '—'}
                        </span>
                      </div>
                      <div>
                        <span className="label">自有数据 AUC</span>
                        <span>
                          {stat
                            ? stat.auc !== null
                              ? stat.auc.toFixed(3)
                              : '样本不足'
                            : '加载中…'}
                        </span>
                      </div>
                      <div>
                        <span className="label">自有样本（阳 / 阴）</span>
                        <span>{stat ? `${stat.positives} / ${stat.negatives}` : '—'}</span>
                      </div>
                      <div>
                        <span className="label">Youden 最优阈值</span>
                        <span>
                          {stat?.best_threshold != null ? pct(stat.best_threshold) : '—'}
                        </span>
                      </div>
                    </div>

                    <div className="tag-detail-actions">
                      <span className="muted">录入金标准：</span>
                      <LabelToggle
                        value={labels[selectedFinding.item]}
                        onToggle={(label) => toggleLabel(selectedFinding.item, label)}
                      />
                      <button
                        type="button"
                        className="ghost small"
                        onClick={() => setSelectedItem(null)}
                      >
                        收起
                      </button>
                    </div>
                  </div>
                )
              })()}

            {groups.length === 0 && <p className="muted">没有符合筛选条件的标签。</p>}

            {groups.map(([organName, items]) => (
              <div key={organName || 'all'} className="organ-group">
                {organName && (
                  <h4>
                    {organName}
                    <span className="count">{items.length}</span>
                  </h4>
                )}
                {items.map((f) => (
                  <div
                    key={f.item}
                    className={`finding-row selectable ${selectedItem === f.item ? 'selected' : ''}`}
                    onClick={() => void openTag(f.item)}
                    title="点击查看该标签的详细信息"
                  >
                    <div className="finding-main">
                      <div className="finding-name">
                        {f.finding}
                        <span className={`risk-tag risk-${f.risk}`}>{RISK_TEXT[f.risk]}</span>
                      </div>
                      <div className="finding-english">{f.english}</div>
                    </div>

                    <ProbBar value={f.probability} risk={f.risk} />

                    <div className="finding-auc">
                      {f.reference_auc !== null ? (
                        <span title="模型在该病种外部验证集上的 ROC-AUC，非单例准确率">
                          AUC {f.reference_auc.toFixed(3)}
                        </span>
                      ) : (
                        <span className="muted">—</span>
                      )}
                    </div>

                    <LabelToggle
                      value={labels[f.item]}
                      onToggle={(label) => toggleLabel(f.item, label)}
                    />
                  </div>
                ))}
              </div>
            ))}
          </div>
        )}

        {tab === 'record' && (
          <div className="result-section">
            <h3>判读记录</h3>
            <p className="muted">
              判读者与备注会一并写进导出的 CSV，用于事后追溯「这一例是谁在什么条件下判的」。
            </p>

            <div className="reader-grid">
              <label>
                判读者
                <input
                  type="text"
                  placeholder="姓名 / 工号"
                  value={reader}
                  onChange={(e) => setReader(e.target.value)}
                  onBlur={() => patchAnnotation({ reader })}
                />
              </label>
              <label>
                备注
                <input
                  type="text"
                  placeholder="例如：图像质量差、增强期相不标准"
                  value={remark}
                  onChange={(e) => setRemark(e.target.value)}
                  onBlur={() => patchAnnotation({ remark })}
                />
              </label>
            </div>

            <div className="record-stats">
              {LABEL_ORDER.map((label) => (
                <div key={label} className={`record-stat label-text-${label}`}>
                  <span className="record-stat-value">{counts.byLabel[label]}</span>
                  <span className="record-stat-label">{LABEL_TEXT[label]}</span>
                </div>
              ))}
              <div className="record-stat">
                <span className="record-stat-value">
                  {result.findings.length - counts.labeled}
                </span>
                <span className="record-stat-label">未判读</span>
              </div>
            </div>

            {status.annotation?.updated_at && (
              <p className="muted">
                最后更新：{new Date(status.annotation.updated_at).toLocaleString()}
              </p>
            )}
          </div>
        )}
      </div>
    </section>
  )
}
