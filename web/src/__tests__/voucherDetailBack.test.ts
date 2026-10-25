/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import VoucherDetail from '@/pages/voucher/detail.vue'
import { useTabsStore } from '@/stores/tabs'

const harness = vi.hoisted(() => ({
  push: vi.fn(),
  back: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
  success: vi.fn(),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: harness.push, back: harness.back }),
  useRoute: () => ({ params: { id: 'e1' }, query: {} }),
}))
vi.mock('@/stores/entry', () => ({
  useEntryStore: () => ({
    activeEntries: [{ id: 'e1', title: '测试凭证', voucherStatus: 'posted', voucherRef: 'VOU-202609-000001', occurDate: '2026-09-05' }],
    fetchEntries: vi.fn(async () => {}),
  }),
}))
vi.mock('@/stores/auth', () => ({ useAuthStore: () => ({ user: null }) }))
vi.mock('@/api/rd', () => ({ getRdProjects: async () => [] }))
vi.mock('@/api/auxiliary', () => ({ assignEntryAuxiliary: vi.fn(), getAuxiliaryOptions: async () => ({ partners: [], departments: [], projects: [] }) }))
vi.mock('@/components/business/VoucherSheet.vue', () => ({ default: { render: () => null } }))
vi.mock('@/composables/useTabGuard', () => ({ useTabGuard: vi.fn() }))
vi.mock('naive-ui', async (importOriginal) => ({
  ...await importOriginal<typeof import('naive-ui')>(),
  useMessage: () => ({ warning: harness.warning, error: harness.error, success: harness.success }),
}))

function mountDetail() {
  return mount(VoucherDetail, { global: { stubs: { NCard: true, NSelect: true } } })
}

function findBackButton(wrapper: ReturnType<typeof mountDetail>) {
  const button = wrapper.findAll('button').find((node) => node.text().includes('返回凭证列表'))
  expect(button, '返回凭证列表按钮应存在').toBeTruthy()
  return button!
}

function setHistoryBack(back: string | undefined) {
  window.history.replaceState({ back }, '')
}

beforeEach(() => {
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.clearAllMocks()
  setHistoryBack(undefined)
})

describe('voucher detail back to list', () => {
  it('goes back in history when the previous entry is the voucher list', async () => {
    setHistoryBack('/vouchers?month=2026-09')
    const wrapper = mountDetail()
    await flushPromises()

    await findBackButton(wrapper).trigger('click')

    expect(harness.back).toHaveBeenCalledTimes(1)
    expect(harness.push).not.toHaveBeenCalled()
  })

  it('falls back to the tab-remembered list url without usable history', async () => {
    setHistoryBack('/closing-wizard')
    useTabsStore().tabs.push({ path: '/vouchers', name: 'vouchers', fullPath: '/vouchers?month=2026-09', pinned: false, lastActiveAt: 0 })
    const wrapper = mountDetail()
    await flushPromises()

    await findBackButton(wrapper).trigger('click')

    expect(harness.back).not.toHaveBeenCalled()
    expect(harness.push).toHaveBeenCalledWith('/vouchers?month=2026-09')
  })

  it('falls back to the plain list url when no tab history exists', async () => {
    setHistoryBack(undefined)
    const wrapper = mountDetail()
    await flushPromises()

    await findBackButton(wrapper).trigger('click')

    expect(harness.back).not.toHaveBeenCalled()
    expect(harness.push).toHaveBeenCalledWith('/vouchers')
  })
})
