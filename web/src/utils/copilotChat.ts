/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * AI CFO · 交互工作台「上次对话记录」的 localStorage 读写。
 * 与 workspaceView / guideState 同一惯例：键名必须带账套维度
 * （`solo:<feature>:${companyId || 'default'}`），否则同账号切换账套会串对话。
 */
import type { AgentActionItem, ClosingLink } from '@/api/agent'

export interface CopilotChatRecord {
  role: 'user' | 'assistant'
  content: string
  imageUrl?: string
  actions?: AgentActionItem[]
  reasoning?: string
  closingLink?: ClosingLink
}

const STORAGE_PREFIX = 'solo:copilot-chat:'

/** 旧版把聊天记录存在不带账套的全局键下，这里只用于一次性迁移后删除 */
export const LEGACY_COPILOT_CHAT_STORAGE_KEY = 'solobooks:copilot_last_chat'

export function copilotChatStorageKey(companyId: string | null): string {
  return `${STORAGE_PREFIX}${companyId || 'default'}`
}

export function loadCopilotChat(companyId: string | null): CopilotChatRecord[] {
  try {
    let raw = localStorage.getItem(copilotChatStorageKey(companyId))
    if (!raw) {
      // 旧全局键迁移到「第一次读到它的当前账套」名下并立刻删除，避免继续串账套
      const legacy = localStorage.getItem(LEGACY_COPILOT_CHAT_STORAGE_KEY)
      if (legacy) {
        raw = legacy
        localStorage.removeItem(LEGACY_COPILOT_CHAT_STORAGE_KEY)
      }
    }
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed) || parsed.length === 0) return []
    return parsed.filter(
      (item): item is CopilotChatRecord =>
        !!item &&
        typeof item === 'object' &&
        ((item as CopilotChatRecord).role === 'user' || (item as CopilotChatRecord).role === 'assistant') &&
        typeof (item as CopilotChatRecord).content === 'string',
    )
  } catch {
    return []
  }
}

export function saveCopilotChat(companyId: string | null, records: CopilotChatRecord[]): void {
  try {
    localStorage.setItem(copilotChatStorageKey(companyId), JSON.stringify(records))
  } catch {
    // 隐私模式或配额失败不阻断工作台
  }
}

export function clearCopilotChat(companyId: string | null): void {
  try {
    localStorage.removeItem(copilotChatStorageKey(companyId))
  } catch {
    // 静默
  }
}
