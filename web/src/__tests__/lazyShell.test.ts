/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter, RouterView } from 'vue-router'
import { defineComponent, h, nextTick, ref } from 'vue'
import { lazyShell } from '@/pages/layout/lazyShell'

// lazyShell 的存在理由：Vue Router 会解析匹配链上的懒加载组件，匿名访客打开 `/`
// 时 RootSwitch 渲染的是官网 Landing，但子记录仍会被解析。这里验证壳的契约 ——
// 只有壳真正被渲染，被包裹的大组件才加载；不渲染就一次都不碰。
describe('lazyShell', () => {
  it('壳被渲染前不加载目标组件，渲染后正常显示', async () => {
    const loader = vi.fn(() =>
      Promise.resolve(defineComponent({ template: '<div class="child">child</div>' })),
    )
    const showShell = ref(false)

    // 模拟 RootSwitch：未登录渲染官网，登录后才透传 RouterView
    const Root = defineComponent({
      setup: () => () =>
        showShell.value ? h(RouterView) : h('div', { class: 'landing' }, 'landing'),
    })

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: '/',
          component: Root,
          children: [{ path: '', component: lazyShell(loader) }],
        },
      ],
    })
    await router.push('/')
    await router.isReady()

    const wrapper = mount({ template: '<router-view />' }, { global: { plugins: [router] } })
    await nextTick()

    expect(wrapper.text()).toContain('landing')
    expect(loader).not.toHaveBeenCalled()

    showShell.value = true
    await nextTick()
    await flushPromises()
    await nextTick()

    expect(loader).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.child').exists()).toBe(true)
  })
})
