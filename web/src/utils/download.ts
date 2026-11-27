/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 统一的 Blob 文件下载出口（PDF / 归档等二进制响应共用）。
 *
 * 单独放轻量模块而不是 `utils/export.ts`：页面只下载 PDF 时不应把 xlsx 打包进首屏。
 */
export function saveBlobFile(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}
