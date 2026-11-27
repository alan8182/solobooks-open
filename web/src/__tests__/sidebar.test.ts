/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { createMemoryHistory, createRouter } from 'vue-router'
import { createPinia, setActivePinia } from 'pinia'
import Sidebar from '@/pages/layout/Sidebar.vue'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import type { WorkspaceView } from '@/utils/workspace'

function makeRouter() {
  return createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: { template: '<div />' } },
      { path: '/invoices', component: { template: '<div />' } },
      { path: '/vouchers', component: { template: '<div />' } },
      { path: '/ledger/books', component: { template: '<div />' } },
      { path: '/ledger/book', component: { template: '<div />' } },
      { path: '/assets', component: { template: '<div />' } },
      { path: '/rd-expenses', component: { template: '<div />' } },
      { path: '/mine/salary', component: { template: '<div />' } },
      { path: '/tools', component: { template: '<div />' } },
    ],
  })
}

async function mountSidebar(options: { mode?: WorkspaceView; path?: string; collapsed?: boolean } = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const viewStore = useWorkspaceViewStore()
  viewStore.setMode(options.mode ?? 'accounting')

  const router = makeRouter()
  router.push(options.path ?? '/')
  await router.isReady()
  const wrapper = mount(Sidebar, {
    props: { collapsed: options.collapsed ?? false },
    global: { plugins: [router, pinia] },
  })
  return { wrapper, router, viewStore }
}

function navButton(wrapper: Awaited<ReturnType<typeof mountSidebar>>['wrapper'], label: string) {
  return wrapper.findAll('button.nav-item').find((button) => button.text().includes(label))
}

describe('sidebar accordion behavior', () => {
  it('keeps only one secondary menu expanded at a time', async () => {
    const { wrapper } = await mountSidebar()

    await navButton(wrapper, '凭证与账簿')!.trigger('click')
    expect(wrapper.text()).toContain('记账凭证')
    expect(wrapper.text()).toContain('电子账簿')

    await navButton(wrapper, '专项核算')!.trigger('click')
    expect(wrapper.text()).not.toContain('记账凭证')
    expect(wrapper.text()).toContain('长期资产')
    expect(wrapper.text()).toContain('研发费用')
  })

  it('auto-expands the parent that owns the current route', async () => {
    const { wrapper } = await mountSidebar({ path: '/vouchers' })

    expect(wrapper.text()).toContain('记账凭证')
    expect(wrapper.text()).toContain('电子账簿')
  })

  it('renders invoice ledger as a direct top-level link', async () => {
    const { wrapper } = await mountSidebar()

    const invoiceBtn = navButton(wrapper, '发票台账')
    expect(invoiceBtn?.exists()).toBe(true)
  })

  it('opens the settings flyout on click and closes it when clicking elsewhere', async () => {
    // 左下角图标只在老板视图存在；会计视图把设置放回导航了
    const { wrapper } = await mountSidebar({ mode: 'boss' })

    const anchor = () => wrapper.find('.settings-anchor')
    // 悬停不应再自动展开（触屏/误触会闪），只有点击才开
    await wrapper.find('button.settings-btn').trigger('click')
    expect(anchor().classes()).toContain('open')

    // 点侧边栏以外的地方收起；锚点 ref 未绑定的话这里会失败
    document.body.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await nextTick()
    expect(anchor().classes()).not.toContain('open')

    // Esc 同样能关，且展开态要透出给读屏（aria-expanded）
    await wrapper.find('button.settings-btn').trigger('click')
    await nextTick()
    expect(wrapper.find('button.settings-btn').attributes('aria-expanded')).toBe('true')
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await nextTick()
    expect(anchor().classes()).not.toContain('open')
    expect(wrapper.find('button.settings-btn').attributes('aria-expanded')).toBe('false')
  })

  it('lays the more flyout out as one column per group', async () => {
    const { wrapper } = await mountSidebar({ mode: 'boss' })

    const flyout = wrapper.find('.settings-flyout')
    const columns = flyout.findAll('.settings-column')
    // 一组一列：竖排 20 项要滚动，四列后最高一列只有 8 项
    expect(columns.map((column) => column.find('.subnav-title').text())).toEqual([
      '账务', '凭证与账簿', '专项核算', '设置',
    ])
    expect(columns.map((column) => column.findAll('button.subnav-item').length)).toEqual([2, 4, 6, 8])
    // 换布局不丢入口
    expect(flyout.findAll('button.subnav-item')).toHaveLength(20)
    expect(wrapper.findAll('.settings-column + .settings-column')).toHaveLength(3)
  })

  it('does not render the desktop collapse control inside the mobile drawer', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const router = makeRouter()
    router.push('/')
    await router.isReady()
    const wrapper = mount(Sidebar, {
      props: { collapsed: false, mobile: true },
      global: { plugins: [router, pinia] },
    })

    expect(wrapper.find('.toggle-btn').exists()).toBe(false)
    expect(wrapper.classes()).toContain('mobile')
    // 移动端抽屉没有左下角图标位，设置等入口统一收进「更多」分组
    expect(navButton(wrapper, '更多')).toBeDefined()
  })
})

describe('sidebar workspace filtering', () => {
  it('keeps only the boss workflow in the boss view and prioritizes closing & tax filing at top', async () => {
    const { wrapper } = await mountSidebar({ mode: 'boss' })

    expect(wrapper.text()).toContain('工作台')
    expect(wrapper.text()).toContain('AI 财务官')
    expect(wrapper.text()).toContain('待办')
    expect(wrapper.text()).toContain('资金流水')
    expect(wrapper.text()).toContain('月度结账')
    // 工资薪金不占老板主航，收在左下角「更多」里
    const navLabels = wrapper.findAll('button.nav-item').map((button) => button.text())
    expect(navLabels.some((label) => label.includes('工资薪金'))).toBe(false)
    expect(wrapper.text()).toContain('报税')
    expect(wrapper.text()).toContain('经营')
    expect(wrapper.text()).toContain('每天')
    expect(wrapper.text()).toContain('每月')
    expect(wrapper.text()).toContain('复盘')
    // 项目利润收进「经营报表」页卡片，不再占导航位
    expect(wrapper.text()).not.toContain('项目利润')
    // 每月 2（月度结账、报税）+ 每天 4（工作台、AI 财务官、待办、资金流水）+ 复盘 1
    const navItems = wrapper.findAll('button.nav-item')
    expect(navItems).toHaveLength(7)

    // 核心断言：首组为「每月」，首项为「月度结账」，次项为「报税」
    const groupLabels = wrapper.findAll('.group-label').map((el) => el.text())
    expect(groupLabels[0]).toBe('每月')
    expect(navItems[0].text()).toContain('月度结账')
    expect(navItems[1].text()).toContain('报税')

    // 视觉特权断言：月度结账拥有 hero 样式类及状态徽标
    expect(navItems[0].classes()).toContain('nav-item--hero')
    expect(navItems[0].find('.closing-badge').exists()).toBe(true)

    // 专业入口不占主航，全部收在左下角「更多」
    expect(navLabels.some((label) => label.includes('经营账本'))).toBe(false)
    expect(navLabels.some((label) => label.includes('发票台账'))).toBe(false)
    expect(navLabels.some((label) => label.includes('凭证与账簿'))).toBe(false)
    expect(navLabels.some((label) => label.includes('专项核算'))).toBe(false)
  })

  it('renders status dot on closing wizard item when sidebar is collapsed', async () => {
    const { wrapper } = await mountSidebar({ collapsed: true, mode: 'boss' })
    const heroItem = wrapper.find('.nav-item--hero')
    expect(heroItem.exists()).toBe(true)
    expect(heroItem.find('.closing-dot').exists()).toBe(true)
    expect(heroItem.find('.closing-badge').exists()).toBe(false)
  })

  it('moves settings into the bottom-left icon with a flyout in the boss view', async () => {
    const { wrapper } = await mountSidebar({ mode: 'boss' })

    // 导航列表里不再有设置组
    expect(navButton(wrapper, '设置')).toBeUndefined()
    expect(wrapper.find('.settings-btn').exists()).toBe(true)

    // 点击左下角图标弹出「更多」：所有非主航入口都要能进
    // （公司信息已由顶部公司管理入口承载）
    await wrapper.find('.settings-btn').trigger('click')
    expect(wrapper.text()).not.toContain('公司信息')
    expect(wrapper.text()).toContain('税费种认定')
    expect(wrapper.text()).toContain('个人设置')
    expect(wrapper.text()).toContain('工资薪金')
    expect(wrapper.text()).toContain('备份与归档')
    // 专业项不再被 workspace 过滤掉
    expect(wrapper.text()).toContain('会计科目')
    expect(wrapper.text()).toContain('期初建账')
    expect(wrapper.text()).toContain('AI 连接（MCP）')
    expect(wrapper.text()).toContain('记账凭证')
    expect(wrapper.text()).toContain('经营账本')

    // 未合并的旧入口不应出现
    expect(wrapper.text()).not.toContain('旧账导入')
    expect(wrapper.text()).not.toContain('审计日志')
  })

  it('exposes the full menu through 更多 in the mobile boss drawer', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    useWorkspaceViewStore().setMode('boss')
    const router = makeRouter()
    router.push('/')
    await router.isReady()
    const wrapper = mount(Sidebar, {
      props: { collapsed: false, mobile: true },
      global: { plugins: [router, pinia] },
    })

    await navButton(wrapper, '更多')!.trigger('click')
    expect(wrapper.text()).toContain('会计科目')
    expect(wrapper.text()).toContain('工资薪金')
    expect(wrapper.text()).toContain('经营账本')
  })

  it('shows professional bookkeeping menus in the accounting view', async () => {
    const { wrapper } = await mountSidebar({ mode: 'accounting' })

    expect(wrapper.text()).toContain('日常')
    expect(wrapper.text()).toContain('周期')
    expect(wrapper.text()).toContain('凭证与账簿')

    await navButton(wrapper, '专项核算')!.trigger('click')
    expect(wrapper.text()).toContain('研发费用')
    expect(wrapper.text()).toContain('归集与分摊')
    // 项目利润收进「经营报表」页卡片，导航（含专项核算子项）里不再出现
    expect(navButton(wrapper, '项目利润')).toBeUndefined()
  })

  it('puts settings back into the accounting nav instead of the corner icon', async () => {
    const { wrapper } = await mountSidebar({ mode: 'accounting' })

    // 会计科目 / 期初设置这类「不配就用不了」的项必须一眼可见
    expect(navButton(wrapper, '设置')).toBeDefined()
    await navButton(wrapper, '设置')!.trigger('click')
    expect(wrapper.text()).toContain('会计科目')
    expect(wrapper.text()).toContain('期初建账')
    expect(wrapper.text()).toContain('备份与归档')

    // 已有导航入口，左下角图标不再重复
    expect(wrapper.find('.settings-btn').exists()).toBe(false)
  })
})
