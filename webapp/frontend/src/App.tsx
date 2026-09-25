import { useCallback, useEffect, useRef, useState } from 'react'
import { api, uploadCase } from './api'
import ErrorBoundary from './ErrorBoundary'
import { useToast } from './toast'
import AboutDialog from './components/AboutDialog'
import HistoryPanel from './components/HistoryPanel'
import ImageBrowser from './components/ImageBrowser'
import ProgressPanel from './components/ProgressPanel'
import ResultView from './components/ResultView'
import SeriesSelector from './components/SeriesSelector'
import StatsPanel from './components/StatsPanel'
import UploadPanel from './components/UploadPanel'
import type { CaseStatus, Health } from './types'

const TERMINAL = new Set(['done', 'failed'])

const HEALTH_TEXT: Record<string, string> = {
  ready: '模型就绪',
  loading: '模型加载中',
  ckpt_missing: '权重缺失',
}

const STATUS_TEXT: Record<string, string> = {
  pending: '待选序列',
  queued: '排队中',
  preprocessing: '预处理',
  inferencing: '推理中',
  done: '完成',
  failed: '失败',
}

/** 当前病例写进 URL，刷新页面或分享链接后能直接回到同一例 */
const CASE_PARAM = 'case'

/** 轮询间隔：推理动辄几十秒，1.5s 足够跟手，也不至于把后端打满 */
const POLL_INTERVAL_MS = 1500

type Theme = 'light' | 'dark'

const THEME_KEY = 'radar.theme'

/** 主题由 index.html 的内联脚本先行设定，这里只读取，避免首屏闪白 */
function readTheme(): Theme {
  return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light'
}

function readCaseFromUrl(): string | null {
  return new URLSearchParams(window.location.search).get(CASE_PARAM)
}

function writeCaseToUrl(id: string | null) {
  const params = new URLSearchParams(window.location.search)
  if (id) params.set(CASE_PARAM, id)
  else params.delete(CASE_PARAM)
  const query = params.toString()
  window.history.replaceState(null, '', query ? `?${query}` : window.location.pathname)
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null)
  const [caseId, setCaseId] = useState<string | null>(readCaseFromUrl)
  const [status, setStatus] = useState<CaseStatus | null>(null)
  // 递增即触发历史面板重查，面板自身持有列表与分页状态
  const [historyKey, setHistoryKey] = useState(0)
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState<number | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const [rerunning, setRerunning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [view, setView] = useState<'case' | 'stats'>('case')
  const [theme, setThemeState] = useState<Theme>(readTheme)
  /** 历史病例按需查看：默认收起，把宽度留给影像区 */
  const [historyOpen, setHistoryOpen] = useState(false)
  const [aboutOpen, setAboutOpen] = useState(false)
  /** 失败操作的补偿动作；有值时才显示「重试」，用 state 是为了能触发重渲染 */
  const [retryAction, setRetryAction] = useState<(() => void) | null>(null)
  /** 递增即重新发起一次病例轮询，用于错误后的手动重试 */
  const [retryTick, setRetryTick] = useState(0)
  const uploadAbortRef = useRef<(() => void) | null>(null)
  const toast = useToast()
  /** 已经打开病例后，上传区折叠成一行，把首屏让给影像和结果 */
  const [uploadOpen, setUploadOpen] = useState(() => !readCaseFromUrl())

  const refreshHistory = useCallback(() => setHistoryKey((k) => k + 1), [])

  useEffect(() => {
    api
      .health()
      .then(setHealth)
      .catch(() => setHealth(null))
    refreshHistory()
  }, [refreshHistory])

  // 抽屉打开时锁住页面滚动，否则右侧会同时出现页面滚动条和抽屉滚动条
  useEffect(() => {
    if (!historyOpen) return undefined
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = previous
    }
  }, [historyOpen])

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    try {
      window.localStorage.setItem(THEME_KEY, theme)
    } catch {
      // 隐私模式下写不了，主题仅本次会话有效
    }
  }, [theme])

  // 开多个病例标签页时，靠标题就能区分
  useEffect(() => {
    document.title = status?.filename
      ? `${status.filename} · RADAR`
      : 'RADAR · 腹部 CT 智能分析'
  }, [status?.filename])

  // 全局快捷键：g 切换视图，Esc 关掉当前提示
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable)
      ) {
        return
      }
      if (e.key === 'g' || e.key === 'G') {
        setView((v) => (v === 'case' ? 'stats' : 'case'))
      } else if (e.key === 'Escape') {
        setHistoryOpen(false)
        setAboutOpen(false)
        setRetryAction(null)
        setError(null)
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [])

  // 轮询当前病例，直到完成或失败
  useEffect(() => {
    if (!caseId) return
    let cancelled = false
    let timer = 0

    const tick = async () => {
      try {
        const s = await api.getCase(caseId)
        if (cancelled) return
        setStatus(s)
        if (TERMINAL.has(s.status)) {
          refreshHistory()
          return
        }
      } catch (e) {
        if (cancelled) return
        const message = e instanceof Error ? e.message : String(e)
        setError(message)
        setRetryAction(() => () => setRetryTick((t) => t + 1))
        // URL 里的病例可能已被删除或按 TTL 清理，清掉参数避免一直报错
        if (message.includes('不存在')) {
          writeCaseToUrl(null)
          setCaseId(null)
        }
        return
      }
      if (!cancelled) timer = window.setTimeout(tick, POLL_INTERVAL_MS)
    }
    tick()

    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [caseId, refreshHistory, retryTick])

  const handleUpload = async (input: File | File[]) => {
    setUploading(true)
    setUploadProgress(null)
    setError(null)
    setRetryAction(null)
    setStatus(null)

    const handle = uploadCase(input, (fraction) => setUploadProgress(fraction))
    uploadAbortRef.current = handle.abort

    try {
      const res = await handle.promise
      setCaseId(res.case_id)
      writeCaseToUrl(res.case_id)
      setStatus(await api.getCase(res.case_id))
      setUploadOpen(false)
      setView('case')
      refreshHistory()
      toast.success('上传完成，已进入队列')
    } catch (e) {
      // 主动取消不算错误，不该弹红色提示
      if (e instanceof DOMException && e.name === 'AbortError') {
        toast.info('已取消上传')
      } else {
        setError(e instanceof Error ? e.message : String(e))
        setRetryAction(() => () => void handleUpload(input))
        toast.error('上传失败')
      }
    } finally {
      uploadAbortRef.current = null
      setUploadProgress(null)
      setUploading(false)
    }
  }

  const handleCancelUpload = () => {
    uploadAbortRef.current?.()
  }

  const handleRerun = async () => {
    if (!caseId) return
    setRerunning(true)
    try {
      await api.rerunCase(caseId)
      toast.success('已重新排队')
      // 轮询在终态就停了，这里递增一次让它重新跑起来
      setRetryTick((t) => t + 1)
      setStatus(await api.getCase(caseId))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setRerunning(false)
    }
  }

  const handleCancelTask = async () => {
    if (!caseId) return
    setCancelling(true)
    try {
      await api.cancelCase(caseId)
      toast.info('已请求取消，等待当前步骤结束')
      setStatus(await api.getCase(caseId))
    } catch (e) {
      toast.error(e instanceof Error ? e.message : String(e))
    } finally {
      setCancelling(false)
    }
  }

  const handleConfirmSeries = async (seriesId: string) => {
    if (!caseId) return
    setError(null)
    try {
      await api.selectSeries(caseId, seriesId)
      setStatus(await api.getCase(caseId))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const openCase = async (id: string) => {
    setError(null)
    setCaseId(id)
    writeCaseToUrl(id)
    setUploadOpen(false)
    setView('case')
    setHistoryOpen(false)
    try {
      setStatus(await api.getCase(id))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  const reset = () => {
    setCaseId(null)
    setStatus(null)
    setError(null)
    setRetryAction(null)
    setUploadOpen(true)
    writeCaseToUrl(null)
  }

  const copyLink = async () => {
    if (!caseId) return
    try {
      await navigator.clipboard.writeText(window.location.href)
      toast.success('已复制本病例链接')
    } catch {
      toast.error('复制失败，请手动复制地址栏')
    }
  }

  const toggleTheme = () => {
    setThemeState((t) => (t === 'dark' ? 'light' : 'dark'))
  }

  const needSeries = status?.status === 'pending' && status.available_series.length > 0
  const hasResult = Boolean(status?.status === 'done' && status.result)
  const queueSize = health?.queue_size ?? 0

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">R</span>
          <span className="brand-name">RADAR</span>
          <span className="brand-sub">腹部 CT 智能分析</span>
        </div>

        <nav className="topnav">
          <button
            className={view === 'case' ? 'active' : ''}
            onClick={() => setView('case')}
            title="单例判读：上传、阅片、录入金标准（快捷键 g 切换视图）"
          >
            单例判读
          </button>
          <button
            className={view === 'stats' ? 'active' : ''}
            onClick={() => setView('stats')}
            title="队列统计：AUC、阈值标定与批量导出"
          >
            队列统计
          </button>
        </nav>

        <div className="topbar-right">
          <button
            type="button"
            className="ghost small"
            onClick={() => setHistoryOpen(true)}
            title="查看历史病例（默认隐藏，Esc 关闭）"
          >
            历史病例
          </button>
          {queueSize > 0 && (
            <span className="queue-chip" title="当前排队等待推理的病例数">
              队列 {queueSize}
            </span>
          )}
          <span className="health-chip" title={health?.detail || undefined}>
            {health ? (
              <>
                <span className={`dot ${health.status}`} />
                <span>{HEALTH_TEXT[health.status] ?? health.status}</span>
                <span className="muted">
                  {health.cuda_available ? 'CUDA' : 'CPU'}
                </span>
              </>
            ) : (
              <>
                <span className="dot" />
                <span>服务未连接</span>
              </>
            )}
          </span>
          <button
            type="button"
            className="icon-btn"
            onClick={() => setAboutOpen(true)}
            title="关于本系统"
            aria-label="关于"
          >
            ?
          </button>
          <button
            type="button"
            className="icon-btn"
            onClick={toggleTheme}
            title={theme === 'dark' ? '切换到亮色界面' : '切换到暗色阅片模式'}
            aria-label="切换界面主题"
          >
            {theme === 'dark' ? '☀' : '☾'}
          </button>
        </div>
      </header>

      <div className="app-body">
        {health?.status === 'ckpt_missing' && (
          // 默认折成一行：这条提示会长期存在，展开着的两个长路径太占首屏
          <details className="banner">
            <summary>模型权重缺失，推理暂不可用（点击查看处理方式）</summary>
            <p>
              请先执行
              <code>python DAMO-RADAR/download_scripts/download_checkpoints.py</code>
            </p>
            {health.missing_files.length > 0 && (
              <ul>
                {health.missing_files.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            )}
          </details>
        )}

        {error && (
          <div className="banner error">
            <span>{error}</span>
            {retryAction && (
              <button
                type="button"
                className="banner-close"
                onClick={() => {
                  const action = retryAction
                  setRetryAction(null)
                  setError(null)
                  action()
                }}
              >
                重试
              </button>
            )}
            <button
              type="button"
              className="banner-close"
              onClick={() => {
                setRetryAction(null)
                setError(null)
              }}
            >
              关闭
            </button>
          </div>
        )}

        <main className="layout">
          <div className="content">
            {view === 'stats' ? (
              <ErrorBoundary label="队列统计">
                <StatsPanel />
              </ErrorBoundary>
            ) : (
              <>
                {uploadOpen ? (
                  <UploadPanel
                    onUpload={handleUpload}
                    busy={uploading}
                    progress={uploadProgress}
                    onCancel={handleCancelUpload}
                  />
                ) : (
                  <button
                    type="button"
                    className="upload-collapsed"
                    onClick={() => setUploadOpen(true)}
                  >
                    <span className="plus">＋</span> 上传新病例
                    <span className="muted">（.nii / .nii.gz / zip / DICOM 文件夹）</span>
                  </button>
                )}

                {needSeries && (
                  <SeriesSelector
                    series={status!.available_series}
                    onConfirm={handleConfirmSeries}
                  />
                )}

                <div className={`workspace ${hasResult ? 'with-side' : ''}`}>
                  <div className="workspace-main">
                    {status && (
                      <div className="case-bar">
                        <span className="case-name" title={status.filename}>
                          {status.filename}
                        </span>
                        <span className={`status-chip status-${status.status}`}>
                          {STATUS_TEXT[status.status] ?? status.status}
                        </span>
                        <span className="muted case-id">{status.case_id.slice(0, 8)}</span>
                        <span className="case-bar-actions">
                          {(status.status === 'failed' || status.status === 'cancelled') && (
                            <button
                              type="button"
                              className="primary small"
                              onClick={handleRerun}
                              disabled={rerunning}
                              title="体数据还在时后端会跳过预处理，直接重新推理"
                            >
                              {rerunning ? '提交中…' : '重新推理'}
                            </button>
                          )}
                          <button type="button" className="ghost small" onClick={copyLink}>
                            复制链接
                          </button>
                          <button type="button" className="ghost small" onClick={reset}>
                            关闭
                          </button>
                        </span>
                      </div>
                    )}

                    {/* 完成后不再占一块：状态在病例条上已有，耗时在概览里 */}
                    {status && !needSeries && status.status !== 'done' && (
                      <ProgressPanel
                        status={status}
                        onCancel={handleCancelTask}
                        cancelling={cancelling}
                      />
                    )}

                    {/* 体数据一旦就绪即可浏览，不必等推理结束 */}
                    {status?.volume && (
                      <ErrorBoundary label="影像浏览">
                        <ImageBrowser
                          caseId={status.case_id}
                          depth={status.volume.shape_zyx[0]}
                          spacing={status.volume.spacing_zyx}
                          shape={status.volume.shape_zyx}
                        />
                      </ErrorBoundary>
                    )}

                    {status?.status === 'failed' && (
                      <button className="primary" onClick={reset}>
                        重新上传
                      </button>
                    )}
                  </div>

                  {hasResult && (
                    <aside className="workspace-side">
                      <ErrorBoundary label="分析结果">
                        <ResultView
                          status={status!}
                          onAnnotationChange={(annotation) =>
                            setStatus((prev) => (prev ? { ...prev, annotation } : prev))
                          }
                        />
                      </ErrorBoundary>
                    </aside>
                  )}
                </div>
              </>
            )}
          </div>
        </main>

        {historyOpen && (
          <>
            <div
              className="drawer-mask"
              onClick={() => setHistoryOpen(false)}
              aria-hidden="true"
            />
            <aside className="drawer" role="dialog" aria-label="历史病例">
              <div className="drawer-head">
                <h2>历史病例</h2>
                <button
                  type="button"
                  className="icon-btn"
                  onClick={() => setHistoryOpen(false)}
                  aria-label="关闭历史病例"
                >
                  ×
                </button>
              </div>
              <HistoryPanel
                hideTitle
                activeId={caseId}
                onOpen={openCase}
                onDeleted={(id) => {
                  if (id === caseId) reset()
                }}
                refreshKey={historyKey}
              />
            </aside>
          </>
        )}
      </div>

      {aboutOpen && (
        <AboutDialog health={health} onClose={() => setAboutOpen(false)} />
      )}
    </div>
  )
}
