/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

export interface BankStatementDescriptionParts {
  typeName?: unknown
  summaryText?: unknown
  commentText?: unknown
  counterpartyName?: unknown
}

const NULL_LIKE = new Set(['', 'null', 'undefined', 'none', 'nil', '-'])

function clean(value: unknown): string {
  const text = String(value ?? '').replace(/\s+/g, ' ').trim()
  return NULL_LIKE.has(text.toLowerCase()) ? '' : text
}

function semanticKey(value: string): string {
  return value
    .replace(/^\s*(?:附言|备注|用途)\s*[:：]\s*/i, '')
    .toLowerCase()
    .replace(/[\s\p{P}\p{S}]+/gu, '')
}

export function buildBankStatementDescription(parts: BankStatementDescriptionParts): string {
  const typeName = clean(parts.typeName)
  const summarySegments = clean(parts.summaryText)
    .split(/[|｜]/)
    .map(clean)
    .filter(Boolean)
  const commentText = clean(parts.commentText)
  const counterpartyName = clean(parts.counterpartyName)

  const output: string[] = []
  const seen = new Set<string>()
  for (const segment of [typeName, ...summarySegments]) {
    const key = semanticKey(segment)
    if (!key || seen.has(key)) continue
    seen.add(key)
    output.push(segment)
  }

  const commentKey = semanticKey(commentText)
  if (commentKey && !seen.has(commentKey)) output.push(`附言: ${commentText}`)

  const description = output.join(' | ')
  const counterparty = counterpartyName ? `(对方: ${counterpartyName})` : ''
  return [description, counterparty].filter(Boolean).join(' ') || '-'
}
