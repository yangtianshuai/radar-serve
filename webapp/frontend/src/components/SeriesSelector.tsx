import { useState } from 'react'
import type { SeriesOption } from '../types'

interface Props {
  series: SeriesOption[]
  onConfirm: (seriesId: string) => void
}

export default function SeriesSelector({ series, onConfirm }: Props) {
  const [selected, setSelected] = useState(series[0]?.series_id ?? '')

  return (
    <section className="panel">
      <h2>选择用于推理的序列</h2>
      <p className="muted">
        该压缩包内包含 {series.length} 个 DICOM 序列，通常对应不同的扫描期相。RADAR
        针对增强腹部 CT 训练，建议选择门静脉期 / 实质期。
      </p>
      <ul className="series-list">
        {series.map((s) => (
          <li key={s.series_id}>
            <label className={selected === s.series_id ? 'selected' : ''}>
              <input
                type="radio"
                name="series"
                value={s.series_id}
                checked={selected === s.series_id}
                onChange={() => setSelected(s.series_id)}
              />
              <span className="series-desc">{s.series_description}</span>
              <span className="series-meta">
                {s.num_slices} 层 · Series {s.series_number}
              </span>
            </label>
          </li>
        ))}
      </ul>
      <button className="primary" disabled={!selected} onClick={() => onConfirm(selected)}>
        开始推理
      </button>
    </section>
  )
}
