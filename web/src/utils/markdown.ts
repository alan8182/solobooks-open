/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { marked } from 'marked'
import DOMPurify from 'dompurify'

marked.setOptions({ gfm: true, breaks: true })

/**
 * 渲染 AI 助手回复的 markdown。
 * AI 输出不可信，必须经 DOMPurify 消毒后才允许 v-html 注入。
 */
export function renderMarkdown(raw: string | undefined | null): string {
  if (!raw) return ''
  const html = marked.parse(raw, { async: false })
  return DOMPurify.sanitize(html, { ADD_ATTR: ['target'] })
}
