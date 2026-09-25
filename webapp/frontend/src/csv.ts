export type CsvValue = string | number | null | undefined

function escapeCell(value: CsvValue): string {
  if (value === null || value === undefined) return ''
  const text = String(value)
  // 含分隔符、引号或换行的单元格必须加引号，内部引号翻倍转义
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text
}

export function toCsv(rows: CsvValue[][]): string {
  return rows.map((row) => row.map(escapeCell).join(',')).join('\r\n')
}

/**
 * 触发浏览器下载。
 * 前置 BOM 是为了 Excel 正确识别 UTF-8，否则中文表头会变乱码。
 */
export function downloadCsv(filename: string, rows: CsvValue[][]): void {
  const blob = new Blob([`\ufeff${toCsv(rows)}`], { type: 'text/csv;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  document.body.appendChild(link)
  link.click()
  document.body.removeChild(link)
  URL.revokeObjectURL(url)
}

/** 去掉不适合出现在文件名里的字符 */
export function safeFilename(name: string): string {
  return name.replace(/[\\/:*?"<>|\s]+/g, '_').replace(/^_+|_+$/g, '') || 'case'
}
