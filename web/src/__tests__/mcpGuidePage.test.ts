/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 *
 * AI 连接中心页面渲染测试：守住模板能挂载、Tab 能切、令牌能注入配置。
 * 内容层的事实校验在 mcpGuide.test.ts。
 */

import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import McpGuidePage from '@/pages/mcp-guide/index.vue'
import { CLIENT_GUIDES, TOKEN_PLACEHOLDER } from '@/pages/mcp-guide/data'

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div />' } }],
  })
}

async function mountPage() {
  const wrapper = mount(McpGuidePage, { global: { plugins: [makeRouter()] } })
  await flushPromises()
  return wrapper
}

describe('AI 连接中心页面', () => {
  beforeEach(() => {
    // Star 徽标会打 GitHub API：测试里一律离线，验证降级不报错
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
  })

  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('渲染主标题与全部客户端 Tab', async () => {
    const wrapper = await mountPage()
    expect(wrapper.find('h1').text()).toContain('AI 数字员工')
    expect(wrapper.findAll('.client-tab')).toHaveLength(CLIENT_GUIDES.length)
    expect(wrapper.findAll('.client-tab')[0].classes()).toContain('active')
  })

  it('默认展示第一个客户端的配置代码与令牌占位符', async () => {
    const wrapper = await mountPage()
    expect(wrapper.find('.code-body').text()).toContain(TOKEN_PLACEHOLDER)
    expect(wrapper.find('.panel-head h3').text()).toBe(CLIENT_GUIDES[0].name)
  })

  it('切换 Tab 后面板内容跟着换', async () => {
    const wrapper = await mountPage()
    const target = CLIENT_GUIDES[2]
    await wrapper.findAll('.client-tab')[2].trigger('click')
    expect(wrapper.find('.panel-head h3').text()).toBe(target.name)
    expect(wrapper.find('.code-body').text().length).toBeGreaterThan(0)
  })

  it('粘贴令牌后配置块替换占位符，且令牌不进 localStorage', async () => {
    const wrapper = await mountPage()
    const input = wrapper.find('.inject-input input')
    await input.setValue('sbk_test_injected_token')
    await flushPromises()

    expect(wrapper.find('.code-body').text()).toContain('sbk_test_injected_token')
    expect(wrapper.find('.code-body').text()).not.toContain(TOKEN_PLACEHOLDER)
    expect(JSON.stringify(localStorage)).not.toContain('sbk_test_injected_token')
  })

  it('非 sbk_ 前缀的输入给出提醒', async () => {
    const wrapper = await mountPage()
    expect(wrapper.find('.inject-warn').exists()).toBe(false)
    await wrapper.find('.inject-input input').setValue('wrong-token')
    expect(wrapper.find('.inject-warn').exists()).toBe(true)
  })

  it('工具清单只列名字，24 个全部渲染', async () => {
    const wrapper = await mountPage()
    const chips = wrapper.findAll('.tool-chips code')
    expect(chips).toHaveLength(24)
    expect(chips.map((c) => c.text())).toContain('propose_voucher')
  })
})
