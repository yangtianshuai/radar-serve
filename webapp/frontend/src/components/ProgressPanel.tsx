import { useEffect, useRef, useState } from 'react'

import type { CaseStatus } from '../types'

const STATUS_TEXT: Record<string, string> = {
  pending: '等待选择序列',
  queued: '排队中',
  preprocessing: '预处理',
  inferencing: '模型推理',
  done: '完成',
  failed: '失败',
  cancelled: '已取消',
}

/** 这些状态下任务还在跑，允许取消 */
const CANCELLABLE = new Set(['queued', 'preprocessing', 'inferencing'])

/** 终态：耗时定格，不再按秒增长 */
const TERMINAL = new Set(['done', 'failed', 'cancelled'])

/** 把秒数写成「2 分 13 秒」这种一眼能读的量 */
function formatDuration(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds))
  if (total < 60) return `${total} 秒`
  const minutes = Math.floor(total / 60)
  if (minutes < 60) return `${minutes} 分 ${total % 60} 秒`
  return `${Math.floor(minutes / 60)} 时 ${minutes % 60} 分`
}

export default function ProgressPanel({
  status,
  onCancel,
  cancelling = false,
}: {
  status: CaseStatus
  onCancel?: () => void
  cancelling?: boolean
}) {
  // 耗时：服务端给的是采样那一刻的值，两次轮询之间本地按秒补差值，
  // 否则数字会一跳一跳的；到了终态就定格在服务端的值上，不再增长
  const elapsedSample = useRef({ at: Date.now(), sec: status.elapsed_sec ?? 0 })
  const [nowTick, setNowTick] = useState(() => Date.now())
  const finished = TERMINAL.has(status.status)

  useEffect(() => {
    elapsedSample.current = { at: Date.now(), sec: status.elapsed_sec ?? 0 }
    setNowTick(Date.now())
  }, [status.elapsed_sec])

  useEffect(() => {
    if (finished) return
    const timer = setInterval(() => setNowTick(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [finished])

  const elapsed = finished
    ? elapsedSample.current.sec
    : elapsedSample.current.sec + (nowTick - elapsedSample.current.at) / 1000

  const percent = Math.round(Math.min(Math.max(status.progress, 0), 1) * 100)
  const failed = status.status === 'failed'
  const cancelled = status.status === 'cancelled'
  const canCancel = CANCELLABLE.has(status.status) && Boolean(onCancel)
  const waiting = status.queue_position !== null ? status.queue_position - 1 : 0
  /** 本地刚发出取消请求，或服务端已收到但任务还没停下 */
  const cancellingUp = cancelling || status.cancel_requested

  return (
    <section className="panel">
      <div className="progress-head">
        <h2>{STATUS_TEXT[status.status] ?? status.status}</h2>
        <div className="progress-head-right">
          {status.status === 'queued' && waiting > 0 && (
            <span className="queue-hint" title="当前排在你前面的病例数">
              前面还有 {waiting} 例
            </span>
          )}
          <span className={`status-chip status-${status.status}`}>
            {failed ? '失败' : cancelled ? '已取消' : `${percent}%`}
          </span>
          {canCancel && (
            <button
              type="button"
              className="ghost small"
              onClick={onCancel}
              disabled={cancellingUp}
            >
              {cancellingUp ? '取消中…' : '取消任务'}
            </button>
          )}
        </div>
      </div>

      <div className="progress-track">
        <div
          className={`progress-bar ${failed ? 'failed' : ''} ${cancelled ? 'cancelled' : ''}`}
          style={{ width: `${failed || cancelled ? 100 : percent}%` }}
        />
      </div>

      <p className="progress-stage">
        {cancellingUp ? '正在取消，等待当前步骤结束…' : status.stage || status.message}
        {status.volume && !failed && !cancelled && !cancellingUp && ' · 体数据已就绪'}
        {elapsed > 0 && ` · 已用时 ${formatDuration(elapsed)}`}
      </p>

      {failed && status.error && <p className="error-text">{status.error}</p>}
    </section>
  )
}
