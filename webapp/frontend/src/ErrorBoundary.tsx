import { Component, Fragment, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
  /** 出错区域的名称，写在兜底界面里，便于用户判断是哪块坏了 */
  label?: string
}

interface State {
  error: Error | null
  /** 递增即强制重新挂载子树，让「重试」真的重新渲染而不是复用坏掉的状态 */
  retryKey: number
}

/**
 * 渲染异常兜底。
 *
 * React 18 下一个组件抛异常会卸载整棵树——用户可能正在录入第 80 个标签，
 * 一次渲染异常就让整页变白、未提交的内容全丢。用它把崩溃限制在一个区域内。
 *
 * 注意：只能捕获**渲染期**的异常，事件处理与异步代码里的错误不走这里，
 * 那些依旧由各自的 try/catch 与提示条负责。
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, retryKey: 0 }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 控制台留一份完整栈，现场排障时不必让用户复现
    console.error('[radar] 渲染异常：', error, info.componentStack)
  }

  private handleRetry = () => {
    this.setState((prev) => ({ error: null, retryKey: prev.retryKey + 1 }))
  }

  render() {
    const { error, retryKey } = this.state

    if (error) {
      return (
        <div className="crash-panel">
          <h2>{this.props.label ? `${this.props.label}出错了` : '这块内容出错了'}</h2>
          <p className="muted">
            页面其余部分仍可正常使用。已经保存到服务端的判读数据不受影响，刷新后会重新载入。
          </p>
          <p className="error-text">{error.message || error.name}</p>
          <div className="crash-actions">
            <button type="button" className="primary small" onClick={this.handleRetry}>
              重试
            </button>
            <button
              type="button"
              className="ghost small"
              onClick={() => window.location.reload()}
            >
              刷新页面
            </button>
          </div>
        </div>
      )
    }

    return <Fragment key={retryKey}>{this.props.children}</Fragment>
  }
}
