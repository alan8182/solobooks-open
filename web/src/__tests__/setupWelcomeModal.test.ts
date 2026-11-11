/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import SetupWelcomeModal from '@/components/business/SetupWelcomeModal.vue'
import { useAuthStore } from '@/stores/auth'

const h = vi.hoisted(() => ({ push: vi.fn() }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: h.push }) }))
vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return {
    ...actual,
    useMessage: () => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn() }),
  }
})

describe('SetupWelcomeModal 新用户双通道分流引导 (方案A)', () => {
  let wrapper: any = null

  beforeEach(() => {
    setActivePinia(createPinia())
    h.push.mockReset()
  })

  afterEach(() => {
    if (wrapper) {
      wrapper.unmount()
      wrapper = null
    }
    document.body.innerHTML = ''
  })

  function setDemoUser(isDemo = true) {
    const auth = useAuthStore()
    auth.user = {
      user_id: 'u1',
      nickname: '测试用户',
      has_password: true,
      current_company_id: 'c_demo',
      companies: [
        {
          company_id: 'c_demo',
          name: isDemo ? '【演示】杭州南山科技有限公司' : '我的正式公司',
          isDemo,
          startDate: '2026-01-01',
        },
      ],
    } as any
  }

  it('处于演示账套时，第一屏直接展示沙盒体验说明与双通道入口', () => {
    setDemoUser(true)
    wrapper = mount(SetupWelcomeModal, {
      props: { show: true },
      attachTo: document.body,
    })

    expect(document.body.textContent).toContain('沙盒体验环境')
    expect(document.body.textContent).toContain('【演示】杭州南山科技有限公司')
    expect(document.body.textContent).toContain('建立老板财务直觉')
    expect(document.body.textContent).toContain('好公司不是融到资才开始规范财务')
    expect(document.body.textContent).toContain('带我逛逛演示账套')
    expect(document.body.textContent).toContain('我是老板 · 立即建立真实企业账套')
  })

  it('第一屏直接点击创建真实账套，深链跳转到 /mine/companies?create=new', async () => {
    setDemoUser(true)
    wrapper = mount(SetupWelcomeModal, {
      props: { show: true },
      attachTo: document.body,
    })

    const buttons = Array.from(document.body.querySelectorAll('button'))
    const createBtn = buttons.find((b) => b.textContent?.includes('立即建立真实企业账套'))
    expect(createBtn).toBeDefined()
    createBtn!.click()
    await wrapper.vm.$nextTick()

    expect(h.push).toHaveBeenCalledWith('/mine/companies?create=new')
    expect(wrapper.emitted('update:show')?.[0]).toEqual([false])
  })

  it('第一屏点击带我逛逛演示账套，关闭弹窗进入系统', async () => {
    setDemoUser(true)
    wrapper = mount(SetupWelcomeModal, {
      props: { show: true },
      attachTo: document.body,
    })

    const buttons = Array.from(document.body.querySelectorAll('button'))
    const demoBtn = buttons.find((b) => b.textContent?.includes('带我逛逛演示账套'))
    expect(demoBtn).toBeDefined()
    demoBtn!.click()
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('update:show')?.[0]).toEqual([false])
  })
})
