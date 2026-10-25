/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SetupChecklist from '@/components/business/SetupChecklist.vue'
import { useAuthStore } from '@/stores/auth'
import { writeGuideState } from '@/stores/guideState'
import type { SetupReadiness, SetupStep } from '@/api/types'

const h = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: h.push }) }))

const DAY = 86_400_000

function step(overrides: Partial<SetupStep> = {}): SetupStep {
  return {
    key: 'company_profile',
    title: '建好账套',
    hint: '告诉系统你是什么主体',
    status: 'todo',
    blockedBy: null,
    detail: null,
    deepLink: '/mine/companies',
    informational: false,
    progress: null,
    ...overrides,
  }
}

function readiness(overrides: Partial<SetupReadiness> = {}): SetupReadiness {
  return {
    companyId: 'c1',
    view: 'boss',
    fiscalYear: 2026,
    completed: 0,
    total: 3,
    ready: false,
    generatedAt: new Date().toISOString(),
    items: [
      step(),
      step({ key: 'opening_balance', title: '录入期初余额', deepLink: '/mine/opening' }),
      step({ key: 'first_voucher', title: '记第一笔账', deepLink: '/copilot' }),
    ],
    ...overrides,
  }
}

function setCompany(createdAt: string, isDemo = false) {
  const authStore = useAuthStore()
  authStore.user = {
    user_id: 'u1',
    nickname: '测试',
    current_company_id: 'c1',
    companies: [
      {
        company_id: 'c1',
        name: '测试主体',
        created_at: createdAt,
        isDemo,
      },
    ],
  } as never
}

function mountChecklist(props: Record<string, unknown> = {}) {
  return mount(SetupChecklist, {
    props: { readiness: readiness(), ...props },
  })
}

describe('SetupChecklist', () => {
  beforeEach(() => {
    localStorage.clear()
    h.push.mockClear()
    setActivePinia(createPinia())
    setCompany(new Date(Date.now() - 2 * DAY).toISOString())
  })

  it('expands every step for a fresh book', () => {
    const wrapper = mountChecklist()

    expect(wrapper.findAll('.checklist-row')).toHaveLength(3)
    expect(wrapper.text()).toContain('上手清单')
  })

  it('collapses to one line once the book is ready', () => {
    const wrapper = mountChecklist({
      readiness: readiness({ ready: true, completed: 3 }),
    })

    expect(wrapper.find('.checklist-list').exists()).toBe(false)
    expect(wrapper.text()).toContain('账套已就绪 · 3/3')
  })

  it('degrades to a summary after the onboarding window', () => {
    setCompany(new Date(Date.now() - 20 * DAY).toISOString())
    const wrapper = mountChecklist()

    expect(wrapper.find('.checklist-list').exists()).toBe(false)
    expect(wrapper.text()).toContain('已完成 0/3')
  })

  it('renders nothing when the user dismissed it today', () => {
    writeGuideState('c1', { dismissedAt: new Date().toISOString() })
    const pinia = createPinia()
    setActivePinia(pinia)
    setCompany(new Date(Date.now() - 2 * DAY).toISOString())

    const wrapper = mountChecklist()

    expect(wrapper.find('.setup-checklist').exists()).toBe(false)
  })

  it('hides the checklist for a demo book on the home page', () => {
    setCompany(new Date(Date.now() - 2 * DAY).toISOString(), true)
    const wrapper = mountChecklist({
      readiness: readiness({ ready: true, completed: 3 }),
    })

    expect(wrapper.find('.setup-checklist').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('账套已就绪')
  })

  it('marks finished steps and blocks locked ones', async () => {
    const wrapper = mountChecklist({
      readiness: readiness({
        items: [
          step({ key: 'a', title: '已完成项', status: 'done' }),
          step({
            key: 'b',
            title: '被锁项',
            status: 'locked',
            blockedBy: 'company_profile',
            detail: '填好启用日期才能判断期初',
          }),
        ],
      }),
    })

    expect(wrapper.find('.checklist-state.is-done i').classes()).toContain('ri-check-line')
    expect(wrapper.text()).toContain('待解锁')

    await wrapper.findAll('.checklist-row')[1].trigger('click')
    expect(h.push).not.toHaveBeenCalled()
  })

  it('navigates through the deep link of a clickable step', async () => {
    const wrapper = mountChecklist()

    await wrapper.findAll('.checklist-row')[2].trigger('click')

    expect(h.push).toHaveBeenCalledWith('/copilot')
  })

  it('shows skeleton rows while loading instead of jumping', () => {
    const wrapper = mountChecklist({ readiness: null, loading: true })

    expect(wrapper.findAll('.skeleton-row').length).toBeGreaterThan(0)
  })
})
