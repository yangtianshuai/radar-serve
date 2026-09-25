import type { Health } from '../types'

const HEALTH_TEXT: Record<string, string> = {
  ready: '模型就绪',
  loading: '模型加载中',
  ckpt_missing: '权重缺失',
}

/**
 * 「关于」对话框：项目定位、免责声明与公众号二维码。
 *
 * 免责声明放在这里是刻意的——页脚的常驻免责声明用户不会细看，
 * 而打开「关于」的人是在主动了解这个系统，是说明使用边界的正确时机。
 */
export default function AboutDialog({
  health,
  onClose,
}: {
  health: Health | null
  onClose: () => void
}) {
  return (
    <>
      <div className="modal-mask" onClick={onClose} aria-hidden="true" />
      <div className="modal about-dialog" role="dialog" aria-modal="true" aria-label="关于">
        <header className="modal-head">
          <h2>关于</h2>
          <button type="button" className="icon-btn" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </header>

        <div className="modal-body">
          <div className="about-brand">
            <span className="brand-mark">R</span>
            <div>
              <strong>RADAR · 腹部 CT 智能分析</strong>
              <p className="muted">面向增强腹部 CT 的智能分析服务</p>
            </div>
          </div>

          <p className="about-text">
            把达摩院开源的 RADAR（*Science* 2026 专家级通用视觉语言模型）服务化：
            上传增强腹部 CT，输出 146 项「器官_病灶」阳性概率，并支持逐标签录入金标准、
            阈值标定与自有数据的 ROC / AUC 统计。
          </p>

          <div className="about-block about-warn">
            <strong>这是科研与辅助阅片工具</strong>
            <ul>
              <li>输出的是 AI 预测的<strong>阳性概率</strong>，不是诊断结论</li>
              <li>不构成临床诊断意见，不能替代执业医师判断</li>
              <li>风险分级阈值尚未在自有数据上标定，仅供研究参考</li>
              <li>模型针对增强腹部 CT 训练，平扫或非腹部数据结果不可靠</li>
            </ul>
          </div>

          <div className="about-block about-qr">
            <img src="/wechat-qr.jpg" alt="微信公众号「宏医AI」二维码" />
            <div className="about-qr-text">
              <strong>宏医AI</strong>
              <p className="muted">扫码关注公众号，获取部署说明与更新</p>
            </div>
          </div>

          <div className="about-meta">
            <div>
              <span className="label">服务状态</span>
              <span>
                {health ? (HEALTH_TEXT[health.status] ?? health.status) : '未连接'}
              </span>
            </div>
            <div>
              <span className="label">推理设备</span>
              <span>{health ? (health.cuda_available ? 'CUDA' : 'CPU') : '—'}</span>
            </div>
            <div>
              <span className="label">模型权重</span>
              <span>radar-generalist/RADAR</span>
            </div>
          </div>

          <p className="muted about-footnote">
            本项目与阿里巴巴达摩院无隶属关系，是独立的第三方服务化实现；
            模型权重与核心推理逻辑归原始作者所有。
          </p>
        </div>
      </div>
    </>
  )
}
