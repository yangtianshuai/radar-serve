import { useCallback, useEffect, useState, type MouseEvent } from 'react'
import { api } from '../api'
import { useToast } from '../toast'
import type { CaseBrief } from '../types'

const PAGE_SIZE = 15

const STATUS_TEXT: Record<string, string> = {
  pending: '待选序列',
  queued: '排队中',
  preprocessing: '预处理',
  inferencing: '推理中',
  done: '完成',
  failed: '失败',
}

const FILTERS: { key: string; label: string; status?: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'done', label: '完成', status: 'done' },
  { key: 'failed', label: '失败', status: 'failed' },
  { key: 'running', label: '进行中', status: 'pending,queued,preprocessing,inferencing' },
]

function fmtTime(iso: string) {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const p = (n: number) => String(n).padStart(2, '0')
  // 侧边栏窄，省略年份；完整时间放在 title 里
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

function fmtFull(iso: string) {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleString()
}

function pct(v: number) {
  return `${(v * 100).toFixed(1)}%`
}

export default function HistoryPanel({
  activeId,
  onOpen,
  onDeleted,
  refreshKey = 0,
  hideTitle = false,
}: {
  activeId: string | null
  onOpen: (id: string) => void
  onDeleted?: (id: string) => void
  refreshKey?: number
  /** 放在抽屉里使用时，标题由外层提供，面板不再重复 */
  hideTitle?: boolean
}) {
  const toast = useToast()
  const [items, setItems] = useState<CaseBrief[]>([])
  const [total, setTotal] = useState(0)
  const [keyword, setKeyword] = useState('')
  const [debounced, setDebounced] = useState('')
  const [filter, setFilter] = useState('all')
  const [loading, setLoading] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // 输入停顿 300ms 再查，避免每敲一个字都打一次接口
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(keyword), 300)
    return () => window.clearTimeout(timer)
  }, [keyword])

  const statusParam = FILTERS.find((f) => f.key === filter)?.status

  const fetchPage = useCallback(
    async (offset: number, append: boolean) => {
      if (append) setLoadingMore(true)
      else setLoading(true)
      setError(null)
      try {
        const res = await api.listCases({
          limit: PAGE_SIZE,
          offset,
          keyword: debounced,
          status: statusParam,
        })
        setTotal(res.total)
        setItems((prev) => (append ? [...prev, ...res.items] : res.items))
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
        if (!append) setItems([])
      } finally {
        setLoading(false)
        setLoadingMore(false)
      }
    },
    [debounced, statusParam],
  )

  // 搜索词 / 筛选变化，或外部通知有新结果时，回到第一页重查
  useEffect(() => {
    fetchPage(0, false)
  }, [fetchPage, refreshKey])

  const handleDelete = async (e: MouseEvent, id: string) => {
    e.stopPropagation()
    if (!window.confirm('确定删除该病例？上传数据与结果文件都会被清除，且不可恢复。')) return
    try {
      await api.deleteCase(id)
      onDeleted?.(id)
      fetchPage(0, false)
      toast.success('已删除病例及其文件')
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      setError(message)
      toast.error(`删除失败：${message}`)
    }
  }

  const filtered = Boolean(debounced) || filter !== 'all'

  return (
    <section className="panel history-panel">
      <div className="history-head">
        {!hideTitle && <h2>历史病例</h2>}
        <span className="muted">{total} 例</span>
      </div>

      <input
        className="history-search"
        type="search"
        placeholder="搜索文件名…"
        value={keyword}
        onChange={(e) => setKeyword(e.target.value)}
      />

      <div className="segmented small history-filters">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            className={filter === f.key ? 'active' : ''}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
      </div>

      {error && <p className="error-text">{error}</p>}

      {loading && <p className="muted">加载中…</p>}

      {!loading && items.length === 0 && (
        <p className="muted">{filtered ? '没有匹配的病例' : '暂无记录'}</p>
      )}

      <ul className="history">
        {items.map((h) => (
          <li key={h.case_id} className="history-item">
            <button
              className={h.case_id === activeId ? 'active' : ''}
              onClick={() => onOpen(h.case_id)}
            >
              <span className="history-name" title={h.filename}>
                {h.filename}
              </span>

              <span className="history-meta">
                <span className={`history-status status-${h.status}`}>
                  {STATUS_TEXT[h.status] ?? h.status}
                </span>
                {h.labeled_count > 0 && (
                  <span className="history-labeled" title="已录入金标准的标签数">
                    金标 {h.labeled_count}
                  </span>
                )}
                <span className="muted" title={fmtFull(h.created_at)}>
                  {fmtTime(h.created_at)}
                </span>
              </span>

              {h.status === 'done' && (
                <span className="history-summary">
                  {h.top_finding ? (
                    <>
                      <span className="history-top" title={h.top_finding}>
                        {h.top_finding}
                      </span>
                      {h.top_probability !== null && (
                        <span className="history-prob">{pct(h.top_probability)}</span>
                      )}
                    </>
                  ) : (
                    <span className="muted">未检出阳性</span>
                  )}
                  {h.high_risk_count > 0 && (
                    <span className="history-risk">高风险 {h.high_risk_count}</span>
                  )}
                </span>
              )}

              {h.status === 'failed' && h.error && (
                <span className="history-error" title={h.error}>
                  {h.error}
                </span>
              )}

              {h.status !== 'done' && h.status !== 'failed' && (
                <span className="history-progress">
                  {STATUS_TEXT[h.status] ?? h.status} · {Math.round(h.progress * 100)}%
                </span>
              )}
            </button>

            <button
              className="history-del"
              title="删除该病例"
              onClick={(e) => handleDelete(e, h.case_id)}
            >
              ×
            </button>
          </li>
        ))}
      </ul>

      {items.length < total && (
        <button
          className="history-more"
          disabled={loadingMore}
          onClick={() => fetchPage(items.length, true)}
        >
          {loadingMore ? '加载中…' : `加载更多（还剩 ${total - items.length}）`}
        </button>
      )}
    </section>
  )
}
