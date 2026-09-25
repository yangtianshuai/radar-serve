import { useRef, useState } from 'react'
import { useToast } from '../toast'

interface Props {
  onUpload: (input: File | File[]) => void
  busy: boolean
  /** 传输进度 0~1；null 表示还没拿到第一个进度事件 */
  progress: number | null
  onCancel: () => void
}

// 单文件走 NIfTI / zip，多文件或文件夹走 DICOM 序列
const ACCEPT = '.nii,.nii.gz,.zip,.dcm,.dicom,.ima'

/** 与后端 RADAR_MAX_UPLOAD_MB 保持一致 */
const MAX_UPLOAD_MB = 2048

/** 单文件上传允许的格式；多文件按 DICOM 序列处理，不用扩展名卡（DICOM 常常没有扩展名） */
const SINGLE_ALLOWED = ['.nii', '.nii.gz', '.zip', '.dcm', '.dicom', '.ima']

/** 一眼就不是医学影像的类型：传上去只会白等几分钟再报错 */
const BLOCKED_EXT = [
  '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.tif', '.tiff', '.heic',
  '.mp4', '.avi', '.mov', '.mkv', '.wmv', '.flv',
  '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt',
  '.mp3', '.wav', '.rar', '.7z', '.exe', '.dll', '.msi', '.dmg',
]

/**
 * 上传前的基本校验，把明显不对的输入挡在本地。
 *
 * 只做「廉价且不会误伤」的判断：真正解析能不能成，仍然由服务端说了算——
 * 前端不该假装自己能判断 DICOM 序列是否完整。
 */
function validateUpload(list: File[]): string | null {
  const blocked = list.filter((f) =>
    BLOCKED_EXT.some((ext) => f.name.toLowerCase().endsWith(ext)),
  )
  if (blocked.length === list.length) {
    const names = blocked.slice(0, 3).map((f) => f.name).join('、')
    return `这些不是医学影像文件：${names}${blocked.length > 3 ? ' 等' : ''}`
  }

  const totalMb = list.reduce((sum, f) => sum + f.size, 0) / 1024 / 1024
  if (totalMb > MAX_UPLOAD_MB) {
    return `总大小 ${totalMb.toFixed(0)} MB，超过 ${MAX_UPLOAD_MB} MB 上限`
  }

  if (list.length === 1) {
    const name = list[0].name.toLowerCase()
    if (!SINGLE_ALLOWED.some((ext) => name.endsWith(ext))) {
      return `不支持的格式：${list[0].name}。单文件支持 .nii / .nii.gz / .zip，或直接拖入 DICOM 文件夹`
    }
  }

  return null
}

/** 目录读取单次最多返回 100 项，必须循环读到空为止 */
async function readAllEntries(
  reader: FileSystemDirectoryReader,
): Promise<FileSystemEntry[]> {
  const all: FileSystemEntry[] = []
  for (;;) {
    const batch = await new Promise<FileSystemEntry[]>((resolve, reject) =>
      reader.readEntries(resolve, reject),
    )
    if (batch.length === 0) break
    all.push(...batch)
  }
  return all
}

/** 递归收集拖入的文件夹内容 */
async function collectFiles(entry: FileSystemEntry, out: File[]) {
  if (entry.isFile) {
    const file = await new Promise<File>((resolve, reject) =>
      (entry as FileSystemFileEntry).file(resolve, reject),
    )
    // 跳过 .DS_Store、Thumbs.db 这类系统文件
    const name = file.name || ''
    if (name && !name.startsWith('.') && name !== 'Thumbs.db') out.push(file)
    return
  }
  if (entry.isDirectory) {
    const children = await readAllEntries(
      (entry as FileSystemDirectoryEntry).createReader(),
    )
    for (const child of children) await collectFiles(child, out)
  }
}

function progressText(progress: number | null, busy: boolean) {
  if (!busy) return ''
  if (progress === null) return '正在准备上传…'
  if (progress >= 1) return '上传完成，服务端解析中…'
  return `上传中 ${Math.round(progress * 100)}%`
}

export default function UploadPanel({ onUpload, busy, progress, onCancel }: Props) {
  const toast = useToast()
  const fileRef = useRef<HTMLInputElement>(null)
  const dirRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [summary, setSummary] = useState('')

  const submit = (list: File[]) => {
    if (list.length === 0) return

    const problem = validateUpload(list)
    if (problem) {
      toast.error(problem)
      return
    }

    const mb = list.reduce((sum, f) => sum + f.size, 0) / 1024 / 1024
    setSummary(
      list.length === 1 ? `${list[0].name} · ${mb.toFixed(1)} MB` : `${list.length} 个文件 · ${mb.toFixed(1)} MB`,
    )
    // 单文件保持原样（可能是 .nii.gz 或 zip），多文件交给后端拍平
    onUpload(list.length === 1 ? list[0] : list)
  }

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault()
    setDragging(false)
    if (busy) return

    // 优先走 Entry API，这样拖入文件夹也能展开
    const entries = Array.from(e.dataTransfer.items ?? [])
      .map((item) => (item.kind === 'file' ? item.webkitGetAsEntry?.() : null))
      .filter((entry): entry is FileSystemEntry => Boolean(entry))

    if (entries.length > 0) {
      const out: File[] = []
      for (const entry of entries) await collectFiles(entry, out)
      out.sort((a, b) => (a.webkitRelativePath || a.name).localeCompare(b.webkitRelativePath || b.name))
      submit(out)
      return
    }

    submit(Array.from(e.dataTransfer.files ?? []))
  }

  const percent = progress === null ? 0 : Math.min(progress, 1) * 100

  return (
    <section className="panel">
      <h2>上传腹部 CT</h2>
      <div
        className={`dropzone ${dragging ? 'dragging' : ''} ${busy ? 'busy' : ''}`}
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={handleDrop}
        onClick={() => !busy && fileRef.current?.click()}
      >
        {busy ? (
          <div className="dropzone-inner">
            <p className="dropzone-title">{progressText(progress, busy)}</p>
            <div className="upload-track">
              <div className="upload-bar" style={{ width: `${percent}%` }} />
            </div>
            <button
              type="button"
              className="ghost small"
              onClick={(e) => {
                e.stopPropagation()
                onCancel()
              }}
            >
              取消上传
            </button>
          </div>
        ) : (
          <div className="dropzone-inner">
            <div className="dropzone-icon">＋</div>
            <p className="dropzone-title">拖拽文件或文件夹到此处，或点击选择</p>
            <p className="dropzone-hint">
              <code>.nii</code> / <code>.nii.gz</code> 单文件、打包 DICOM 序列的{' '}
              <code>.zip</code>，或直接拖入 DICOM 文件夹
            </p>
            {summary && <p className="dropzone-file">{summary}</p>}
          </div>
        )}
      </div>

      <div className="upload-actions">
        <button type="button" disabled={busy} onClick={() => fileRef.current?.click()}>
          选择文件
        </button>
        <button type="button" disabled={busy} onClick={() => dirRef.current?.click()}>
          选择文件夹
        </button>
      </div>

      <input
        ref={fileRef}
        type="file"
        accept={ACCEPT}
        multiple
        hidden
        onChange={(e) => {
          const list = Array.from(e.target.files ?? [])
          e.target.value = '' // 允许连续选择同一个文件
          submit(list)
        }}
      />

      <input
        ref={dirRef}
        type="file"
        hidden
        // webkitdirectory 不在 React 的 input 类型里，只能这样传
        {...({ webkitdirectory: '', directory: '' } as Record<string, string>)}
        onChange={(e) => {
          const list = Array.from(e.target.files ?? [])
          e.target.value = ''
          submit(list)
        }}
      />

      <ul className="upload-notes">
        <li>
          DICOM 可直接拖入文件夹或整包 zip，服务端会自动按层位置排序、完成 HU 标定。
        </li>
        <li>检测到多个期相时，会先让你选择序列再开始推理。</li>
        <li>
          RADAR 针对<strong>增强腹部 CT</strong>训练，平扫或非腹部数据结果不可靠。
        </li>
      </ul>
    </section>
  )
}
