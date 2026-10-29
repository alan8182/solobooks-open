/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

type ViewModeModule = typeof import('@/composables/useStatementViewMode')

async function freshModule(): Promise<ViewModeModule> {
  // 模块级共享 ref 需要每个用例重置：resetModules 后重新 import 模拟新页面挂载
  vi.resetModules()
  return await import('@/composables/useStatementViewMode')
}

describe('useStatementViewMode', () => {
  beforeEach(() => {
    localStorage.clear()
    vi.resetModules()
    setActivePinia(createPinia())
  })

  it('defaults to boss view when nothing stored', async () => {
    const { useStatementViewMode } = await freshModule()
    const { mode } = useStatementViewMode()
    expect(mode.value).toBe('boss')
  })

  it('persists the chosen mode and restores it for a new mount', async () => {
    const { useStatementViewMode } = await freshModule()
    const { setMode, mode } = useStatementViewMode()
    setMode('accounting')
    expect(mode.value).toBe('accounting')
    expect(localStorage.getItem('solo:report-view:default')).toBe('accounting')

    // 新页面挂载（新模块副本）应读回会计视图
    const { useStatementViewMode: useFresh } = await freshModule()
    expect(useFresh().mode.value).toBe('accounting')
  })

  it('switches back to boss and persists the default', async () => {
    const { useStatementViewMode } = await freshModule()
    const { setMode } = useStatementViewMode()
    setMode('accounting')
    setMode('boss')
    expect(localStorage.getItem('solo:report-view:default')).toBe('boss')

    const { useStatementViewMode: useFresh } = await freshModule()
    expect(useFresh().mode.value).toBe('boss')
  })
})
