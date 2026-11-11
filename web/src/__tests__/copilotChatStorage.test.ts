/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { beforeEach, describe, expect, it } from 'vitest'
import {
  clearCopilotChat,
  copilotChatStorageKey,
  LEGACY_COPILOT_CHAT_STORAGE_KEY,
  loadCopilotChat,
  saveCopilotChat,
} from '@/utils/copilotChat'

function record(role: 'user' | 'assistant', content: string) {
  return { role, content }
}

describe('copilot chat storage helpers', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('derives a per-company storage key with the repo-wide solo: prefix convention', () => {
    expect(copilotChatStorageKey('c1')).toBe('solo:copilot-chat:c1')
    expect(copilotChatStorageKey(null)).toBe('solo:copilot-chat:default')
  })

  // 根因回归：旧实现存在全局键 solobooks:copilot_last_chat，同账号切换账套共用一份对话
  it('isolates chat records per company', () => {
    saveCopilotChat('c1', [record('user', '账套一的问题')])
    saveCopilotChat('c2', [record('user', '账套二的问题')])

    expect(loadCopilotChat('c1').map((r) => r.content)).toEqual(['账套一的问题'])
    expect(loadCopilotChat('c2').map((r) => r.content)).toEqual(['账套二的问题'])
  })

  it('clears only the active company chat', () => {
    saveCopilotChat('c1', [record('user', 'a')])
    saveCopilotChat('c2', [record('user', 'b')])

    clearCopilotChat('c1')

    expect(loadCopilotChat('c1')).toEqual([])
    expect(loadCopilotChat('c2').map((r) => r.content)).toEqual(['b'])
  })

  it('migrates the legacy global chat into the first company that reads it, once', () => {
    localStorage.setItem(LEGACY_COPILOT_CHAT_STORAGE_KEY, JSON.stringify([record('user', '旧全局记录')]))

    expect(loadCopilotChat('c1').map((r) => r.content)).toEqual(['旧全局记录'])
    expect(localStorage.getItem(LEGACY_COPILOT_CHAT_STORAGE_KEY)).toBeNull()
    // 迁移是一次性的：别的账套不应再看到旧全局记录
    expect(loadCopilotChat('c2')).toEqual([])
  })

  it('drops malformed records instead of restoring them', () => {
    localStorage.setItem(
      copilotChatStorageKey('c1'),
      JSON.stringify([{ role: 'system', content: 'x' }, 'junk', { role: 'user', content: 'ok' }]),
    )
    expect(loadCopilotChat('c1').map((r) => r.content)).toEqual(['ok'])
  })

  it('returns empty for corrupted json', () => {
    localStorage.setItem(copilotChatStorageKey('c1'), '{broken')
    expect(loadCopilotChat('c1')).toEqual([])
  })
})
