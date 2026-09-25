import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react'
import { api } from '../api'
import type { WindowPreset } from '../types'

const WINDOW_LABEL: Record<string, string> = {
  abdomen: '腹部软组织',
  liver: '肝脏',
  mediastinum: '纵隔',
  lung: '肺',
  bone: '骨',
  wide: '全窗',
}

/** 自定义窗的伪预设名，选中它表示 WW/WL 已被手动修改 */
const CUSTOM_KEY = 'custom'

/** 拖动调窗：水平位移 -> 窗宽，垂直位移 -> 窗位（单位 HU / 像素） */
const WW_PER_PX = 4
const WL_PER_PX = 2
const WW_RANGE: [number, number] = [1, 4000]
const WL_RANGE: [number, number] = [-1200, 3000]

/** 拖动时的渲染节流：太快会把后端逐层渲染打满，也更耗带宽 */
const WINDOW_THROTTLE_MS = 80

/** 位移小于该像素值才算「点击定位」，否则视为拖动调窗 */
const CLICK_SLOP_PX = 4

/** 比例尺候选长度（mm），取不超过图像宽度 1/4 的最大值 */
const SCALE_CANDIDATES = [5, 10, 20, 50, 100, 200]

/** 把索引换算成 0~1 的比例；维度只有 1 层时落在正中 */
function ratio(index: number, size: number) {
  if (size <= 1) return 0.5
  return Math.min(Math.max(index / (size - 1), 0), 1)
}

/**
 * 交叉参考线。
 *
 * 三联视图的联动如果只有「点一下跳过去」，用户其实不知道当前层落在
 * 另外两个视图的哪个位置——尤其是切了几层之后。十字线把三个视图的
 * 空间位置显式连起来，这是 MPR 的标准配置。
 */
function Crosshair({ x, y }: { x: number; y: number }) {
  return (
    <div className="crosshair" aria-hidden="true">
      <span className="crosshair-line crosshair-h" style={{ top: `${y * 100}%` }} />
      <span className="crosshair-line crosshair-v" style={{ left: `${x * 100}%` }} />
    </div>
  )
}

function clampNumber(value: number, [lo, hi]: [number, number]) {
  if (!Number.isFinite(value)) return lo
  return Math.min(hi, Math.max(lo, Math.round(value)))
}

function clampIndex(value: number, size: number) {
  if (size <= 0) return 0
  return Math.min(size - 1, Math.max(0, Math.round(value)))
}

/**
 * 单个视图的指针交互：拖动调窗 + 点击定位。
 * 两者共用一个指针手势，靠位移阈值区分，避免调窗时误触发定位。
 */
function useViewport({
  ww,
  wl,
  onWindow,
  onFlush,
  onPick,
}: {
  ww: number
  wl: number
  onWindow: (ww: number, wl: number) => void
  onFlush: () => void
  onPick?: (fractionX: number, fractionY: number) => void
}) {
  const [dragging, setDragging] = useState(false)
  const origin = useRef<{ x: number; y: number; ww: number; wl: number } | null>(null)
  const moved = useRef(0)

  const onPointerDown = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (e.button !== 0) return
    try {
      e.currentTarget.setPointerCapture(e.pointerId)
    } catch {
      // 部分环境（触摸、合成事件）不支持指针捕获；捕获失败不影响后续拖动
    }
    origin.current = { x: e.clientX, y: e.clientY, ww, wl }
    moved.current = 0
    setDragging(true)
  }

  const onPointerMove = (e: ReactPointerEvent<HTMLDivElement>) => {
    const start = origin.current
    if (!start) return
    const dx = e.clientX - start.x
    const dy = e.clientY - start.y
    moved.current = Math.max(moved.current, Math.abs(dx) + Math.abs(dy))
    if (moved.current <= CLICK_SLOP_PX) return
    onWindow(
      clampNumber(start.ww + dx * WW_PER_PX, WW_RANGE),
      clampNumber(start.wl + dy * WL_PER_PX, WL_RANGE),
    )
  }

  const onPointerUp = (e: ReactPointerEvent<HTMLDivElement>) => {
    if (!origin.current) return
    const wasDrag = moved.current > CLICK_SLOP_PX
    origin.current = null
    setDragging(false)
    onFlush()
    try {
      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId)
      }
    } catch {
      // 同上：捕获本就没成功时释放会抛异常，忽略
    }
    if (!wasDrag && onPick) {
      const rect = e.currentTarget.getBoundingClientRect()
      if (rect.width > 0 && rect.height > 0) {
        onPick((e.clientX - rect.left) / rect.width, (e.clientY - rect.top) / rect.height)
      }
    }
  }

  return {
    dragging,
    handlers: {
      onPointerDown,
      onPointerMove,
      onPointerUp,
      onPointerCancel: onPointerUp,
    },
  }
}

export default function ImageBrowser({
  caseId,
  depth,
  spacing,
  shape,
}: {
  caseId: string
  depth: number
  /** spacing_zyx，[层厚, 行间距, 列间距]（mm） */
  spacing?: number[]
  /** shape_zyx，[层数, 行数, 列数] */
  shape?: number[]
}) {
  const zSize = depth
  const ySize = shape?.[1] ?? 0
  const xSize = shape?.[2] ?? 0

  const [axial, setAxial] = useState(() => Math.floor(depth / 2))
  const [coronal, setCoronal] = useState(() => Math.floor((shape?.[1] ?? 0) / 2))
  const [sagittal, setSagittal] = useState(() => Math.floor((shape?.[2] ?? 0) / 2))

  const [presets, setPresets] = useState<WindowPreset[]>([])
  const [presetKey, setPresetKey] = useState('abdomen')
  const [ww, setWw] = useState(400)
  const [wl, setWl] = useState(50)
  const [loading, setLoading] = useState(true)

  const wheelLock = useRef(0)
  const pendingWindow = useRef<{ ww: number; wl: number } | null>(null)
  const windowTimer = useRef(0)

  // 切换病例时回到解剖中心附近：轴位取中层，冠状/矢状取行/列中点
  useEffect(() => {
    setAxial(Math.floor(depth / 2))
    setCoronal(Math.floor((shape?.[1] ?? 0) / 2))
    setSagittal(Math.floor((shape?.[2] ?? 0) / 2))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [caseId, depth])

  useEffect(() => {
    api
      .windows()
      .then((items) => {
        setPresets(items)
        const abdomen = items.find((p) => p.key === 'abdomen')
        if (abdomen) {
          setWw(Math.round(abdomen.window_width))
          setWl(Math.round(abdomen.window_level))
        }
      })
      .catch(() => setPresets([]))
  }, [])

  const flushWindow = useCallback(() => {
    if (windowTimer.current) {
      window.clearTimeout(windowTimer.current)
      windowTimer.current = 0
    }
    const pending = pendingWindow.current
    if (pending) {
      pendingWindow.current = null
      setWw(pending.ww)
      setWl(pending.wl)
    }
  }, [])

  /** 拖动中节流提交，避免每个 pointermove 都请求一张新图 */
  const scheduleWindow = useCallback(
    (nextWw: number, nextWl: number) => {
      pendingWindow.current = { ww: nextWw, wl: nextWl }
      if (windowTimer.current) return
      windowTimer.current = window.setTimeout(() => {
        windowTimer.current = 0
        const pending = pendingWindow.current
        if (pending) {
          setWw(pending.ww)
          setWl(pending.wl)
        }
      }, WINDOW_THROTTLE_MS)
    },
    [],
  )

  const handleWindowDrag = useCallback(
    (nextWw: number, nextWl: number) => {
      setPresetKey(CUSTOM_KEY)
      scheduleWindow(nextWw, nextWl)
    },
    [scheduleWindow],
  )

  useEffect(() => () => flushWindow(), [flushWindow])

  // 冠状面与轴位共享同一个层号（都反映 Z 位置）
  const pickOnCoronal = (fx: number, fy: number) => {
    setSagittal(clampIndex(fx * (xSize - 1), xSize))
    setAxial(clampIndex(fy * (zSize - 1), zSize))
  }
  const pickOnSagittal = (fx: number, fy: number) => {
    setCoronal(clampIndex(fx * (ySize - 1), ySize))
    setAxial(clampIndex(fy * (zSize - 1), zSize))
  }

  const axialView = useViewport({
    ww,
    wl,
    onWindow: handleWindowDrag,
    onFlush: flushWindow,
  })
  const coronalView = useViewport({
    ww,
    wl,
    onWindow: handleWindowDrag,
    onFlush: flushWindow,
    onPick: pickOnCoronal,
  })
  const sagittalView = useViewport({
    ww,
    wl,
    onWindow: handleWindowDrag,
    onFlush: flushWindow,
    onPick: pickOnSagittal,
  })

  // 滚轮翻层：各视图各管一个方向
  const bindWheel = useCallback(
    (el: HTMLDivElement | null, step: (direction: number) => void) => {
      if (!el) return undefined
      const handler = (e: WheelEvent) => {
        e.preventDefault()
        const now = Date.now()
        if (now - wheelLock.current < 60) return
        wheelLock.current = now
        step(e.deltaY > 0 ? 1 : -1)
      }
      el.addEventListener('wheel', handler, { passive: false })
      return () => el.removeEventListener('wheel', handler)
    },
    [],
  )

  const axialRef = useRef<HTMLDivElement>(null)
  const coronalRef = useRef<HTMLDivElement>(null)
  const sagittalRef = useRef<HTMLDivElement>(null)

  useEffect(
    () =>
      bindWheel(axialRef.current, (dir) =>
        setAxial((i) => clampIndex(i + dir, zSize)),
      ),
    [bindWheel, zSize],
  )
  useEffect(
    () =>
      bindWheel(coronalRef.current, (dir) =>
        setCoronal((i) => clampIndex(i + dir, ySize)),
      ),
    [bindWheel, ySize],
  )
  useEffect(
    () =>
      bindWheel(sagittalRef.current, (dir) =>
        setSagittal((i) => clampIndex(i + dir, xSize)),
      ),
    [bindWheel, xSize],
  )

  const axialUrl = api.sliceUrl(caseId, axial, { ww, wl })
  const coronalUrl = api.sliceUrl(caseId, coronal, { ww, wl, plane: 'coronal' })
  const sagittalUrl = api.sliceUrl(caseId, sagittal, { ww, wl, plane: 'sagittal' })

  useEffect(() => {
    setLoading(true)
  }, [axialUrl])

  // 预加载相邻层，翻页时不易白屏
  useEffect(() => {
    for (const i of [axial + 1, axial - 1]) {
      if (i >= 0 && i < zSize) {
        const img = new Image()
        img.src = api.sliceUrl(caseId, i, { ww, wl })
      }
    }
  }, [caseId, axial, zSize, ww, wl])

  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.shiftKey ? 5 : 1
    switch (e.key) {
      case 'ArrowUp':
      case 'ArrowLeft':
        e.preventDefault()
        setAxial((i) => clampIndex(i - step, zSize))
        break
      case 'ArrowDown':
      case 'ArrowRight':
        e.preventDefault()
        setAxial((i) => clampIndex(i + step, zSize))
        break
      case 'PageUp':
        e.preventDefault()
        setAxial((i) => clampIndex(i - 10, zSize))
        break
      case 'PageDown':
        e.preventDefault()
        setAxial((i) => clampIndex(i + 10, zSize))
        break
      case 'Home':
        e.preventDefault()
        setAxial(0)
        break
      case 'End':
        e.preventDefault()
        setAxial(zSize - 1)
        break
      default:
        break
    }
  }

  const applyPreset = (key: string) => {
    setPresetKey(key)
    const found = presets.find((p) => p.key === key)
    if (found) {
      setWw(Math.round(found.window_width))
      setWl(Math.round(found.window_level))
    }
  }

  /** 以当前预设为准恢复窗值（自定义后想退回默认值时用） */
  const resetWindow = () => {
    applyPreset(presetKey === CUSTOM_KEY ? 'abdomen' : presetKey)
  }

  const sliceThickness = spacing?.[0]
  const positionMm = sliceThickness !== undefined ? (axial + 0.5) * sliceThickness : undefined

  // 比例尺：按列方向像素间距换算成图像宽度的百分比，与显示尺寸无关
  const scale = useMemo(() => {
    if (!xSize || !spacing?.[2]) return null
    const widthMm = xSize * spacing[2]
    const target = widthMm / 4
    let best = SCALE_CANDIDATES[0]
    for (const candidate of SCALE_CANDIDATES) {
      if (candidate <= target) best = candidate
    }
    return { mm: best, pct: (best / widthMm) * 100 }
  }, [xSize, spacing])

  const options = presets.length
    ? presets
    : [{ key: 'abdomen', window_width: 400, window_level: 50 }]

  const showBadge = (dragging: boolean) =>
    dragging ? <span className="browser-window-badge">W{ww} / L{wl}</span> : null

  return (
    <section className="panel">
      <div className="browser-head">
        <h2>影像浏览</h2>
        <span className="browser-pos">
          轴位第 <strong>{axial + 1}</strong> / {zSize} 层
          {sliceThickness !== undefined && ` · 层厚 ${sliceThickness.toFixed(2)} mm`}
          {positionMm !== undefined && ` · 约 ${positionMm.toFixed(1)} mm`}
        </span>
      </div>

      <div className="mpr-grid">
        <figure className="mpr-view mpr-axial">
          <div
            className="browser-stage"
            ref={axialRef}
            tabIndex={0}
            onKeyDown={handleKeyDown}
            {...axialView.handlers}
            title="按住左键拖动调窗；滚轮或方向键翻层"
          >
            <div className="stage-canvas">
              <img
                src={axialUrl}
                alt={`轴位第 ${axial + 1} 层`}
                draggable={false}
                onLoad={() => setLoading(false)}
                onError={() => setLoading(false)}
              />
              <Crosshair x={ratio(sagittal, xSize)} y={ratio(coronal, ySize)} />
            </div>
            {scale && (
              <div className="browser-scale" style={{ width: `${scale.pct}%` }}>
                <span>{scale.mm} mm</span>
              </div>
            )}
            {loading && !axialView.dragging && <span className="browser-loading">加载中…</span>}
            {showBadge(axialView.dragging)}
          </div>
          <figcaption>
            轴位 · {axial + 1} / {zSize}
          </figcaption>
          <input
            className="browser-slider"
            type="range"
            min={0}
            max={Math.max(zSize - 1, 0)}
            value={axial}
            onChange={(e) => setAxial(Number(e.target.value))}
            aria-label="轴位层索引"
          />
        </figure>

        <figure className="mpr-view">
          <div
            className="browser-stage small"
            ref={coronalRef}
            {...coronalView.handlers}
            title="按住拖动调窗；点击可定位到对应层"
          >
            <div className="stage-canvas">
              <img src={coronalUrl} alt={`冠状第 ${coronal + 1} 层`} draggable={false} />
              <Crosshair x={ratio(sagittal, xSize)} y={ratio(axial, zSize)} />
            </div>
            {showBadge(coronalView.dragging)}
          </div>
          <figcaption>
            冠状 · {coronal + 1} / {ySize}
          </figcaption>
          <input
            className="browser-slider"
            type="range"
            min={0}
            max={Math.max(ySize - 1, 0)}
            value={coronal}
            onChange={(e) => setCoronal(Number(e.target.value))}
            aria-label="冠状行索引"
          />
        </figure>

        <figure className="mpr-view">
          <div
            className="browser-stage small"
            ref={sagittalRef}
            {...sagittalView.handlers}
            title="按住拖动调窗；点击可定位到对应层"
          >
            <div className="stage-canvas">
              <img src={sagittalUrl} alt={`矢状第 ${sagittal + 1} 层`} draggable={false} />
              <Crosshair x={ratio(coronal, ySize)} y={ratio(axial, zSize)} />
            </div>
            {showBadge(sagittalView.dragging)}
          </div>
          <figcaption>
            矢状 · {sagittal + 1} / {xSize}
          </figcaption>
          <input
            className="browser-slider"
            type="range"
            min={0}
            max={Math.max(xSize - 1, 0)}
            value={sagittal}
            onChange={(e) => setSagittal(Number(e.target.value))}
            aria-label="矢状列索引"
          />
        </figure>
      </div>

      <div className="browser-tools">
        <label>
          预设
          <select value={presetKey} onChange={(e) => applyPreset(e.target.value)}>
            {options.map((p) => (
              <option key={p.key} value={p.key}>
                {WINDOW_LABEL[p.key] ?? p.key}（W{p.window_width} / L{p.window_level}）
              </option>
            ))}
            {presetKey === CUSTOM_KEY && <option value={CUSTOM_KEY}>自定义</option>}
          </select>
        </label>

        <label>
          WW
          <input
            className="window-input"
            type="number"
            min={WW_RANGE[0]}
            max={WW_RANGE[1]}
            step={10}
            value={ww}
            onChange={(e) => {
              setPresetKey(CUSTOM_KEY)
              setWw(clampNumber(Number(e.target.value), WW_RANGE))
            }}
          />
        </label>

        <label>
          WL
          <input
            className="window-input"
            type="number"
            min={WL_RANGE[0]}
            max={WL_RANGE[1]}
            step={5}
            value={wl}
            onChange={(e) => {
              setPresetKey(CUSTOM_KEY)
              setWl(clampNumber(Number(e.target.value), WL_RANGE))
            }}
          />
        </label>

        <button type="button" className="ghost" onClick={resetWindow}>
          重置窗位
        </button>

        <span className="muted browser-hint">
          拖动调窗 · 滚轮/↑↓ 翻层 · 点冠状/矢状图定位
        </span>
      </div>
    </section>
  )
}
