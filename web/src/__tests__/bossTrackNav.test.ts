/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import BossTrackNav from '@/components/business/BossTrackNav.vue'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import type { WorkspaceView } from '@/utils/workspace'

function makeRouter(initialPath = '/bank') {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/bank', component: { template: '<div>bank</div>' } },
      { path: '/private-expenses', component: { template: '<div>private</div>' } },
      { path: '/reports/advance-balances', component: { template: '<div>advance</div>' } },
      { path: '/reports/ar-aging', component: { template: '<div>ar</div>' } },
      { path: '/reports/ap-aging', component: { template: '<div>ap</div>' } },
    ],
  })
  router.push(initialPath)
  return router
}

async function mountNav(options: { mode?: WorkspaceView; path?: string } = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const viewStore = useWorkspaceViewStore()
  viewStore.setMode(options.mode ?? 'boss')

  const router = makeRouter(options.path ?? '/bank')
  await router.isReady()

  const wrapper = mount(BossTrackNav, {
    global: { plugins: [router, pinia] },
  })
  return { wrapper, router, viewStore }
}

describe('BossTrackNav business component', () => {
  it('renders all 5 tabs in boss view covering funds and payables/receivables', async () => {
    const { wrapper } = await mountNav({ mode: 'boss', path: '/bank' })

    expect(wrapper.find('.boss-track-nav').exists()).toBe(true)
    expect(wrapper.text()).toContain('看账管钱 · 资金与往来')

    const tabs = wrapper.findAll('.track-tab')
    expect(tabs).toHaveLength(5)

    expect(tabs[0].text()).toContain('公司公户 · 查流水')
    expect(tabs[1].text()).toContain('老板私账 · 记垫付')
    expect(tabs[2].text()).toContain('垫付未报销汇总')
    expect(tabs[3].text()).toContain('客户欠款 (应收)')
    expect(tabs[4].text()).toContain('待付账款 (应付)')

    // 默认在 /bank 时，首项处于激活态
    expect(tabs[0].classes()).toContain('active')
    expect(tabs[1].classes()).not.toContain('active')
  })

  it('hides completely in accounting view so accountant screens stay clean', async () => {
    const { wrapper } = await mountNav({ mode: 'accounting', path: '/bank' })

    expect(wrapper.find('.boss-track-nav').exists()).toBe(false)
    expect(wrapper.text()).toBe('')
  })

  it('highlights the private-expenses tab when on private expenses page and supports navigation', async () => {
    const { wrapper, router } = await mountNav({ mode: 'boss', path: '/private-expenses' })

    const tabs = wrapper.findAll('.track-tab')
    expect(tabs[0].classes()).not.toContain('active')
    expect(tabs[1].classes()).toContain('active')

    // 点击切换到公司公户
    await tabs[0].trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.path).toBe('/bank')
  })
})
