// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import GlobalSearch from '@/components/GlobalSearch.vue'
import { createPinia, setActivePinia } from 'pinia'

const pushMock = vi.fn()
vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: pushMock,
  }),
}))

vi.mock('@/api/client', () => ({
  default: {
    get: vi.fn().mockResolvedValue({ entries: [], accounts: [] }),
  },
}))

describe('GlobalSearch / Command Palette', () => {
  let wrapper: any

  beforeEach(() => {
    setActivePinia(createPinia())
    pushMock.mockClear()
    document.body.innerHTML = ''
  })

  afterEach(() => {
    if (wrapper) wrapper.unmount()
  })

  it('renders default quick actions when search query is empty', () => {
    wrapper = mount(GlobalSearch, { attachTo: document.body })
    const text = document.body.textContent || ''
    expect(text).toContain('快捷动作')
    expect(text).toContain('记一笔 / 问 AI 会计')
    expect(text).toContain('上传发票')
    expect(text).toContain('上传银行流水')
    expect(text).toContain('月末结账向导')
  })

  it('filters actions when searching with keywords', async () => {
    wrapper = mount(GlobalSearch, { attachTo: document.body })
    const input = document.body.querySelector('.palette-input input') as HTMLInputElement
    expect(input).toBeTruthy()
    input.value = '发票'
    input.dispatchEvent(new Event('input'))
    await wrapper.vm.$nextTick()

    const text = document.body.textContent || ''
    expect(text).toContain('上传发票')
    expect(text).not.toContain('上传银行流水')
  })

  it('matches pinyin abbreviations like fp for invoice', async () => {
    wrapper = mount(GlobalSearch, { attachTo: document.body })
    const input = document.body.querySelector('.palette-input input') as HTMLInputElement
    expect(input).toBeTruthy()
    input.value = 'fp'
    input.dispatchEvent(new Event('input'))
    await wrapper.vm.$nextTick()

    const text = document.body.textContent || ''
    expect(text).toContain('上传发票')
  })

  it('triggers router push when clicking an action', async () => {
    wrapper = mount(GlobalSearch, { attachTo: document.body })
    const actionRows = document.body.querySelectorAll('.command-row')
    expect(actionRows.length).toBeGreaterThan(0)

    // Click the first action ("记一笔 / 问 AI 会计")
    const firstRow = actionRows[0] as HTMLElement
    firstRow.click()
    expect(pushMock).toHaveBeenCalledWith('/copilot')
    expect(wrapper.emitted('close')).toBeTruthy()
  })
})
