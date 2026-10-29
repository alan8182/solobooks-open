/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import LandingPage from '@/pages/landing/index.vue'

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: vi.fn(),
  }),
}))

const wrappers: ReturnType<typeof mount>[] = []

beforeEach(() => {
  window.scrollTo = vi.fn()
})

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
  document.body.innerHTML = ''
})

describe('LandingPage 移动端适配交互', () => {
  it('正确渲染移动端汉堡切换按钮，并可展开/折叠抽屉导航', async () => {
    const wrapper = mount(LandingPage, { attachTo: document.body })
    wrappers.push(wrapper)

    const menuBtn = wrapper.find('.mobile-menu-btn')
    expect(menuBtn.exists()).toBe(true)

    // 默认抽屉菜单处于折叠状态
    expect(wrapper.find('.mobile-drawer').exists()).toBe(false)

    // 点击打开抽屉菜单
    await menuBtn.trigger('click')
    await nextTick()

    expect(wrapper.find('.mobile-drawer').exists()).toBe(true)
    const links = wrapper.findAll('.mobile-drawer-links a')
    expect(links.length).toBeGreaterThanOrEqual(6)
    expect(wrapper.text()).toContain('核心优势')
    expect(wrapper.text()).toContain('功能展台')
    expect(wrapper.text()).toContain('AI 连接 (MCP & Skills)')

    // 再次点击按钮或遮罩可关闭
    await menuBtn.trigger('click')
    await nextTick()
    expect(wrapper.find('.mobile-drawer').exists()).toBe(false)
  })

  it('点击抽屉导航项触发锚点导航并自动收起抽屉', async () => {
    const wrapper = mount(LandingPage, { attachTo: document.body })
    wrappers.push(wrapper)

    // 展开抽屉
    await wrapper.find('.mobile-menu-btn').trigger('click')
    await nextTick()
    expect(wrapper.find('.mobile-drawer').exists()).toBe(true)

    // 点击某一个锚点链接
    const firstLink = wrapper.find('.mobile-drawer-links a')
    await firstLink.trigger('click')
    await nextTick()

    // 抽屉应自动收拢
    expect(wrapper.find('.mobile-drawer').exists()).toBe(false)
  })

  it('首屏 Bento 看板包含 4 大精简指标卡片', () => {
    const wrapper = mount(LandingPage, { attachTo: document.body })
    wrappers.push(wrapper)

    const bentoCards = wrapper.findAll('.hero-proof-bento .proof-card')
    expect(bentoCards.length).toBe(4)
    expect(wrapper.find('.hero-proof-bento').text()).toContain('10 min')
    expect(wrapper.find('.hero-proof-bento').text()).toContain('100%')
    expect(wrapper.find('.hero-proof-bento').text()).toContain('MCP & Skills')
    expect(wrapper.find('.hero-proof-bento').text()).toContain('¥0')
  })
})
