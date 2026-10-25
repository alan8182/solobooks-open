/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 *
 * T03 · 归集规则页（rules.vue）轻量渲染单测。
 * 与 T02/T04 共用 `aggregationPage.test.ts` 会冲突，故独立成文件。
 *
 * 覆盖三条红线：
 *   1. rate 非法（0 / 1.5）时阻止提交并给中文提示，且错误一次性汇总在一个 Alert 里；
 *   2. counterparty（对方单位）字段可编辑（后端 _rule_matches 第 4 条命中条件）；
 *   3. 草稿未保存时不发 PUT —— 证明是整批保存而非逐条写。
 *
 * ⚠️ NModal 走 teleport，内容挂在 document.body 上，wrapper.find 拿不到，
 *    所以弹层断言一律用 bodyText() 读 document.body.innerText。
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import type { VueWrapper } from '@vue/test-utils'

const harness = vi.hoisted(() => ({
  store: null as any,
  rules: [] as any[],
  members: [] as any[],
  put: vi.fn(),
  post: vi.fn(),
  push: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))

vi.mock('@/stores/aggregation', () => ({ useAggregationStore: () => harness.store }))
vi.mock('vue-router', () => ({
  useRouter: () => ({ push: harness.push }),
  useRoute: () => ({ query: {} }),
}))
vi.mock('@/api/aggregation', () => ({
  listAggregationRules: vi.fn(async () => harness.rules.map((r) => ({ ...r }))),
  listMembers: vi.fn(async () => harness.members.map((m) => ({ ...m }))),
  replaceAggregationRules: harness.put,
  applyRules: harness.post,
}))
vi.mock('naive-ui', async (importOriginal) => ({
  ...await importOriginal<typeof import('naive-ui')>(),
  useMessage: () => ({
    success: harness.success,
    error: harness.error,
    warning: harness.warning,
    info: harness.info,
  }),
}))

import RulesPage from '@/pages/aggregation/rules.vue'
import DimensionPicker from '@/pages/aggregation/_components/DimensionPicker.vue'

function makeStore() {
  return reactive({
    currentDimensionId: 'dim_1',
    dimensions: [
      { id: 'dim_1', code: 'RD', name: '研发项目', kind: 'project' },
      { id: 'dim_2', code: 'DEP', name: '部门', kind: 'department' },
    ],
    hasDimensions: true,
    loading: false,
    // DimensionPicker 内部会调这两个（刷新按钮 / v-model 落库）
    loadDimensions: vi.fn(async () => {}),
    // 只保留可被断言的 vi.fn()：原实现里的 `store.currentDimensionId = id` 引用了一个
    // **在本文件作用域内并不存在**的标识符（mock 的属性名是 harness.store，不是 store），
    // 是会抛 ReferenceError 的悬空闭包。因本页无任何 setCurrentDimension 断言、
    // 该回调也不会被触发，故直接去掉函数体最干净。
    setCurrentDimension: vi.fn(),
    methodOptions: [
      { value: 'direct', label: '直接归集' },
      { value: 'timesheet', label: '工时比例' },
    ],
    labelOf: (group: string, value: string) =>
      group === 'kind'
        ? ({ project: '项目', department: '部门' }[value] ?? value)
        : { direct: '直接归集', timesheet: '工时比例' }[value] ?? value,
    bootstrap: vi.fn(async () => {}),
  })
}

const RULE = {
  id: 'rule_1',
  dimensionId: 'dim_1',
  memberId: 'mem_1',
  priority: 10,
  accountPrefix: '4301',
  direction: 'debit',
  keyword: '研发',
  counterparty: '北京云脑科技',
  rate: 1,
  method: 'direct',
  basis: '',
  status: 'active',
}

function draftRow(overrides: Record<string, unknown> = {}) {
  return {
    key: `k_${Math.random()}`,
    memberId: 'mem_1',
    priority: 10,
    accountPrefix: '4301',
    direction: 'debit' as const,
    keyword: '研发',
    counterparty: '北京云脑科技',
    rate: 1,
    method: 'direct',
    basis: '',
    ...overrides,
  }
}

/** 弹层内容在 document.body 上（teleport），统一从这里读文本 */
function bodyText(): string {
  return document.body.innerText || document.body.textContent || ''
}

async function mountPage(): Promise<VueWrapper<any>> {
  const wrapper = mount(RulesPage)
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  harness.store = makeStore()
  harness.rules = [{ ...RULE }]
  harness.members = [
    { id: 'mem_1', code: 'RD-A', name: '研发项目A' },
    { id: 'mem_2', code: 'RD-B', name: '研发项目B' },
  ]
  harness.put.mockReset()
  harness.post.mockReset()
  harness.push.mockReset()
  harness.put.mockImplementation(async (_id: string, rules: any[]) =>
    rules.map((rule, index) => ({
      ...rule,
      id: `rule_new_${index}`,
      dimensionId: 'dim_1',
      status: 'active',
      accountPrefix: rule.accountPrefix ?? null,
      direction: rule.direction ?? null,
      keyword: rule.keyword ?? null,
      counterparty: rule.counterparty ?? null,
      basis: rule.basis ?? null,
    })),
  )
  harness.post.mockResolvedValue({ allocated: 3, skipped: 2, unmatched: 5, rules: 1 })
})

afterEach(() => {
  vi.clearAllMocks()
  document.body.innerHTML = ''
})

describe('aggregation rules page', () => {
  it('reuses the shared DimensionPicker instead of a page-local dimension select', async () => {
    const wrapper = await mountPage()
    expect(wrapper.findComponent(DimensionPicker).exists()).toBe(true)
    // 早期的临时 NSelect 已移除：页面不再自建维度下拉，也不自己绑 store.currentDimensionId
    expect(wrapper.find('[data-testid="rules-dimension-select"]').exists()).toBe(false)
    // 共享组件内部的「无维度」禁用由组件自己保证，本页不重复实现
    expect(wrapper.findComponent(DimensionPicker).props()).toBeDefined()
  })
  it('renders the saved rule with counterparty and a priority column tooltip trigger', async () => {
    const wrapper = await mountPage()
    const text = wrapper.text()
    expect(text).toContain('优先级')
    expect(text).toContain('对方单位')
    expect(text).toContain('北京云脑科技')
    expect(text).toContain('比例')
    expect(text).toContain('分摊方法')
    // 校验通过时不该有汇总错误 Alert
    expect(text).not.toContain('处问题需要修正')
  })

  it('blocks saving and lists the rate problem in one alert when rate is 0', async () => {
    const wrapper = await mountPage()
    const vm = wrapper.vm as any
    vm.draft = [draftRow({ rate: 0 })]
    await flushPromises()

    await wrapper.find('[data-testid="save-rules"]').trigger('click')
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('处问题需要修正')
    expect(text).toContain('比例必须大于 0 且不超过 100%')
    // 校验不通过 → 绝不发起 PUT，也不弹确认框
    expect(harness.put).not.toHaveBeenCalled()
    expect(bodyText()).not.toContain('整批保存会覆盖')
  })

  it('rejects rate greater than 1 as well', async () => {
    const wrapper = await mountPage()
    const vm = wrapper.vm as any
    vm.draft = [draftRow({ rate: 1.5 })]
    await flushPromises()

    await wrapper.find('[data-testid="save-rules"]').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('比例必须大于 0 且不超过 100%')
    expect(harness.put).not.toHaveBeenCalled()
  })

  it('collects every problem into a single alert instead of one dialog per row', async () => {
    const wrapper = await mountPage()
    const vm = wrapper.vm as any
    vm.draft = [
      draftRow({ memberId: '', rate: 0 }),
      draftRow({ memberId: 'mem_2', rate: 2, method: '' }),
    ]
    await flushPromises()

    await wrapper.find('[data-testid="save-rules"]').trigger('click')
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('4 处问题需要修正')
    expect(text).toContain('未选择归集对象')
    expect(text).toContain('请选择分摊方法')
    expect(harness.put).not.toHaveBeenCalled()
  })

  it('exposes an editable counterparty field (后端第 4 条命中条件）', async () => {
    const wrapper = await mountPage()
    const vm = wrapper.vm as any

    await wrapper.find('[data-testid="add-rule"]').trigger('click')
    await flushPromises()
    expect(vm.showEditModal).toBe(true)

    // 编辑弹层里的对方单位输入框（teleport 到 body）
    const counterparty = document.body.querySelector('[data-testid="rule-counterparty"]')
    expect(counterparty).not.toBeNull()

    vm.editing.memberId = 'mem_1'
    vm.editing.counterparty = '北京云脑科技'
    vm.editing.rate = 1
    await flushPromises()

    const confirmBtn = document.body.querySelector('[data-testid="confirm-edit"]') as HTMLElement
    expect(confirmBtn).not.toBeNull()
    confirmBtn.click()
    await flushPromises()

    const row = (vm.draft as any[]).find((r) => r.counterparty === '北京云脑科技')
    expect(row).toBeTruthy()
    // 服务端数据未被触碰（还没保存）
    expect(harness.put).not.toHaveBeenCalled()
  })

  it('never calls PUT before 保存规则 — 增删改只改本地草稿', async () => {
    const wrapper = await mountPage()
    const vm = wrapper.vm as any

    await wrapper.find('[data-testid="add-rule"]').trigger('click')
    await flushPromises()
    vm.editing.memberId = 'mem_2'
    vm.editing.counterparty = '新值'
    const confirmBtn = document.body.querySelector('[data-testid="confirm-edit"]') as HTMLElement
    confirmBtn.click()
    await flushPromises()
    expect((vm.draft as any[])).toHaveLength(2)

    // 从草稿移除一条（Popconfirm 的 positiveClick）
    vm.removeRule(0)
    await flushPromises()

    // 整批 PUT 一次都没发生
    expect(harness.put).not.toHaveBeenCalled()
    expect(vm.draft).toHaveLength(1)
    expect(wrapper.find('[data-testid="dirty-tag"]').exists()).toBe(true)

    // 重新拉数据（模拟刷新页面 / 切维度）→ 草稿回到服务端基线，未保存的编辑丢失
    await vm.loadRules()
    await flushPromises()
    expect(vm.draft).toHaveLength(1)
    expect(vm.draft[0].memberId).toBe('mem_1')
    expect(harness.put).not.toHaveBeenCalled()
  })

  it('saves the whole batch in one PUT after the confirmation dialog', async () => {
    const wrapper = await mountPage()
    const vm = wrapper.vm as any
    vm.draft = [
      draftRow({ key: 'k1', priority: 10 }),
      draftRow({ key: 'k2', memberId: 'mem_2', priority: 20, rate: 0.5, method: 'timesheet', counterparty: '' }),
    ]
    await flushPromises()

    await wrapper.find('[data-testid="save-rules"]').trigger('click')
    await flushPromises()

    // 弹确认框，提示整批覆盖
    expect(bodyText()).toContain('整批保存会覆盖该维度下全部规则')
    expect(harness.put).not.toHaveBeenCalled()

    const confirmBtn = document.body.querySelector('[data-testid="confirm-save"]') as HTMLElement
    confirmBtn.click()
    await flushPromises()

    expect(harness.put).toHaveBeenCalledTimes(1)
    const [dimensionId, payload] = harness.put.mock.calls[0]
    expect(dimensionId).toBe('dim_1')
    expect(payload).toHaveLength(2)
    // 入参不含 status 字段（后端 AggregationRuleInput 没有 status）
    expect(Object.keys(payload[0])).not.toContain('status')
    expect(payload[0].counterparty).toBe('北京云脑科技')
    expect(payload[1].counterparty).toBeNull()
    expect(payload[1].rate).toBe(0.5)
  })

  it('keeps the draft when the整批 PUT fails so the user does not lose edits', async () => {
    const wrapper = await mountPage()
    const vm = wrapper.vm as any
    vm.draft = [draftRow({ counterparty: '北京云脑科技' })]
    await flushPromises()
    harness.put.mockRejectedValueOnce({ response: { data: { detail: { code: 'INVALID_ALLOCATION', message: '规则比例必须大于 0 且不超过 100%' } } } })

    await wrapper.find('[data-testid="save-rules"]').trigger('click')
    await flushPromises()
    const confirmBtn = document.body.querySelector('[data-testid="confirm-save"]') as HTMLElement
    confirmBtn.click()
    await flushPromises()

    expect(harness.error).toHaveBeenCalled()
    expect(String(harness.error.mock.calls[0][0])).toContain('规则比例必须大于 0')
    // 草稿仍在页面上，用户可改完再试
    expect(vm.draft).toHaveLength(1)
    expect(vm.draft[0].counterparty).toBe('北京云脑科技')
  })

  it('runs apply-rules and shows the four counters plus the 去配置规则 entry', async () => {
    const wrapper = await mountPage()
    const vm = wrapper.vm as any
    vm.openApply()
    await flushPromises()

    const runBtn = document.body.querySelector('[data-testid="run-apply"]') as HTMLElement
    expect(runBtn).not.toBeNull()
    runBtn.click()
    await flushPromises()

    expect(harness.post).toHaveBeenCalledTimes(1)
    const body = harness.post.mock.calls[0][0]
    expect(body.dimensionId).toBe('dim_1')
    // start/end 传 YYYY-MM（后端 period_start_for 内部 replace(day=1)）
    expect(body.start).toMatch(/^\d{4}-\d{2}$/)
    expect(body.end).toMatch(/^\d{4}-\d{2}$/)
    expect(body.basis).toBe('period')

    expect(vm.applyResult).toEqual({ allocated: 3, skipped: 2, unmatched: 5, rules: 1 })
    const resultHtml = document.body.innerHTML
    for (const id of ['stat-allocated', 'stat-skipped', 'stat-unmatched', 'stat-rules']) {
      expect(resultHtml).toContain(`data-testid="${id}"`)
    }

    const goConfigure = document.body.querySelector('[data-testid="go-configure"]') as HTMLElement
    expect(goConfigure).not.toBeNull()
    goConfigure.click()
    await flushPromises()
    expect(harness.push).toHaveBeenCalledTimes(1)
    expect(harness.push.mock.calls[0][0].path).toBe('/aggregation/rules')
  })

  it('hides the 去配置规则 entry when nothing was unmatched', async () => {
    harness.post.mockResolvedValue({ allocated: 3, skipped: 2, unmatched: 0, rules: 1 })
    const wrapper = await mountPage()
    const vm = wrapper.vm as any
    vm.openApply()
    await flushPromises()
    const runBtn = document.body.querySelector('[data-testid="run-apply"]') as HTMLElement
    runBtn.click()
    await flushPromises()
    expect(document.body.querySelector('[data-testid="go-configure"]')).toBeNull()
  })

  it('warns before executing that人工分配不会被覆盖', async () => {
    const wrapper = await mountPage()
    const vm = wrapper.vm as any
    vm.openApply()
    await flushPromises()
    expect(bodyText()).toContain('已存在人工分配的行会被整行跳过，不会被覆盖')
  })

  it('shows the empty-guide alert with a CTA when the dimension has no rules', async () => {
    harness.rules = []
    const wrapper = await mountPage()
    expect(wrapper.text()).toContain('当前维度还没有任何规则')
    expect(wrapper.find('[data-testid="empty-guide-cta"]').exists()).toBe(true)
  })

  it('flags rate < 1 rows with the 尾差 warning', async () => {
    harness.rules = [{ ...RULE, rate: 0.6 }]
    const wrapper = await mountPage()
    expect(wrapper.text()).toContain('尾差不自动补齐，该行将停在「部分归集」')
  })

  it('explains the three matching rules on the page', async () => {
    const wrapper = await mountPage()
    const text = wrapper.text()
    expect(text).toContain('已存在人工分配的行会被整行跳过')
    expect(text).toContain('优先级最小')
    expect(text).toContain('尾差不自动补齐')
  })
})
