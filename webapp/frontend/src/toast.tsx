import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'

export type ToastKind = 'success' | 'error' | 'info'

interface ToastItem {
  id: number
  kind: ToastKind
  text: string
}

interface ToastApi {
  success: (text: string) => void
  error: (text: string) => void
  info: (text: string) => void
}

const ToastContext = createContext<ToastApi | null>(null)

/** 同时最多堆几条，超出就顶掉最旧的，避免刷屏挡住操作 */
const MAX_TOASTS = 3

/** 成功类几秒后自动消失；错误留久一点，给用户看清的机会 */
const AUTO_DISMISS_MS: Record<ToastKind, number> = {
  success: 3000,
  info: 4000,
  error: 6000,
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([])
  const seq = useRef(0)

  const remove = useCallback((id: number) => {
    setItems((list) => list.filter((item) => item.id !== id))
  }, [])

  const push = useCallback(
    (kind: ToastKind, text: string) => {
      seq.current += 1
      const id = seq.current
      setItems((list) => [...list, { id, kind, text }].slice(-MAX_TOASTS))
      window.setTimeout(() => remove(id), AUTO_DISMISS_MS[kind])
    },
    [remove],
  )

  const api = useMemo<ToastApi>(
    () => ({
      success: (text: string) => push('success', text),
      error: (text: string) => push('error', text),
      info: (text: string) => push('info', text),
    }),
    [push],
  )

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-stack" aria-live="polite">
        {items.map((item) => (
          <div key={item.id} className={`toast toast-${item.kind}`}>
            <span className="toast-text">{item.text}</span>
            <button
              type="button"
              className="toast-close"
              onClick={() => remove(item.id)}
              aria-label="关闭提示"
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}

export function useToast(): ToastApi {
  const ctx = useContext(ToastContext)
  if (!ctx) {
    // Provider 缺失时降级为静默，避免因为一句提示把整个界面搞崩
    return {
      success: () => undefined,
      error: () => undefined,
      info: () => undefined,
    }
  }
  return ctx
}
