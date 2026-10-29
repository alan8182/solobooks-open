/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { defineComponent, h, nextTick } from 'vue'
import { NDialogProvider, NMessageProvider } from 'naive-ui'
import RdExpensesPage from '@/pages/rd/index.vue'
import router from '@/router'

vi.mock('@/api/rd', () => ({
  getRdProjects: vi.fn().mockResolvedValue([
    {
      id: 'prj-1',
      code: 'RD-2026-01',
      name: '智能记账算法研发',
      manager: '张三',
      startDate: '2026-01-01',
      endDate: '2026-12-31',
      status: 'active',
      initiationDocumentStatus: 'complete',
      notes: '核心自研项目',
    },
    {
      id: 'prj-2',
      code: 'RD-2026-02',
      name: '发票 OCR 增强模型',
      manager: '李四',
      startDate: '2026-03-01',
      endDate: '2026-09-30',
      status: 'completed',
      initiationDocumentStatus: 'partial',
      notes: '',
    },
  ]),
  getRdExpenseTypes: vi.fn().mockResolvedValue({
    items: [
      { value: 'labor', label: '人员人工' },
      { value: 'direct_input', label: '直接投入' },
      { value: 'depreciation', label: '折旧与摊销' },
    ],
  }),
  getRdEvidenceTypes: vi.fn().mockResolvedValue({
    items: [
      { value: 'initiation', label: '立项决议书' },
      { value: 'timesheet', label: '研发工时表' },
      { value: 'contract', label: '委托研发合同' },
    ],
  }),
  getRdLedger: vi.fn().mockResolvedValue({
    items: [
      {
        date: '2026-06-15',
        voucherNo: '记-006',
        entryId: 'ent-rd-1',
        summary: '研发耗材采购',
        projectCode: 'RD-2026-01',
        projectName: '智能记账算法研发',
        expenseTypeLabel: '直接投入',
        accountCode: '5301002',
        amount: 5000,
        allocationRate: 1,
        allocationMethod: 'direct',
        allocationBasis: '采购入库单',
      },
    ],
    total: 5000,
  }),
  getRdUnallocated: vi.fn().mockResolvedValue({
    items: [
      {
        entryId: 'ent-unalloc-1',
        date: '2026-05-10',
        voucherNo: '记-004',
        summary: '待归集技术服务费',
        accountCode: '5301',
        accountName: '研发支出',
        amount: 2000,
        reason: '缺少研发项目及费用类别',
        status: 'posted',
      },
    ],
    count: 1,
  }),
  getRdReconciliationSummary: vi.fn().mockResolvedValue({
    policyVersion: '2026.Q3',
    policyLabel: '小微企业研发费用税前加计扣除口径',
    policyDisclaimer: '本口径依据现行财税法规，具体扣除以汇算清缴税务机关审核为准。',
    ledgerTotal: 5000,
    generalLedgerTotal: 5000,
    difference: 0,
    isReconciled: true,
    unallocatedCount: 1,
    policyStatus: 'suggested',
    projectSummary: [{ projectId: 'prj-1', projectCode: 'RD-2026-01', projectName: '智能记账算法研发', amount: 5000 }],
    expenseTypeSummary: [{ value: 'direct_input', label: '直接投入', accountCode: '5301002', amount: 5000 }],
    evidenceIssues: [],
    evidenceDocuments: [
      {
        id: 'doc-1',
        projectId: 'prj-1',
        documentType: 'initiation',
        referenceNo: 'RD-INIT-2026-01',
        fileUrl: 'https://docs.example.com/rd-01',
        status: 'available',
        notes: '立项会决议',
        createdAt: '2026-01-05',
      },
    ],
    policyReviewItems: [],
  }),
  getRdEvidence: vi.fn().mockResolvedValue({
    items: [
      {
        id: 'doc-1',
        projectId: 'prj-1',
        documentType: 'initiation',
        referenceNo: 'RD-INIT-2026-01',
        fileUrl: 'https://docs.example.com/rd-01',
        status: 'available',
        notes: '立项会决议',
        createdAt: '2026-01-05',
      },
    ],
  }),
  createRdProject: vi.fn().mockResolvedValue({ id: 'prj-new' }),
  updateRdProject: vi.fn().mockResolvedValue({ id: 'prj-1' }),
  correctRdUnallocated: vi.fn().mockResolvedValue({ originalEntryId: 'ent-unalloc-1' }),
  createRdEvidence: vi.fn().mockResolvedValue({ id: 'doc-new' }),
}))

function createTestRouter(initialTab?: string) {
  const testRouter = createRouter({
    history: createMemoryHistory(),
    routes: [
      {
        path: '/rd-expenses',
        name: 'RdExpenses',
        component: RdExpensesPage,
      },
      {
        path: '/vouchers/:id',
        name: 'VoucherDetail',
        component: { template: '<div>Voucher Detail</div>' },
      },
    ],
  })
  if (initialTab) {
    testRouter.push(`/rd-expenses?tab=${initialTab}`)
  } else {
    testRouter.push('/rd-expenses')
  }
  return testRouter
}

describe('研发费用模块（/rd-expenses）重构优化', () => {
  it('路由与工作空间约束：/rd-expenses 路由挂载正确且仅限 accounting 会计视图', () => {
    const resolved = router.resolve('/rd-expenses')
    expect(resolved).toBeDefined()
    expect(resolved.name).toBe('RdExpenses')
    expect(resolved.meta.workspace).toEqual(['accounting'])
  })

  it('分段标签与统计看板集成：渲染指标卡、子标签并根据未归集数量呈现警示 Badge', async () => {
    setActivePinia(createPinia())
    const testRouter = createTestRouter()
    await testRouter.isReady()

    const TestHost = defineComponent({
      render() {
        return h(NDialogProvider, null, {
          default: () =>
            h(NMessageProvider, null, {
              default: () => h(RdExpensesPage),
            }),
        })
      },
    })

    const wrapper = mount(TestHost, {
      global: {
        plugins: [testRouter],
      },
    })

    await nextTick()
    await new Promise((resolve) => setTimeout(resolve, 60))

    // 检查页面头部标题与副标题
    expect(wrapper.text()).toContain('研发费用')
    expect(wrapper.text()).toContain('按研发项目和费用类别精细归集')

    // 检查统计看板
    expect(wrapper.text()).toContain('期间研发费用')
    expect(wrapper.text()).toContain('5,000.00')
    expect(wrapper.text()).toContain('研发项目总数')
    expect(wrapper.text()).toContain('2 个')
    expect(wrapper.text()).toContain('待补充归集')
    expect(wrapper.text()).toContain('1 笔')
    expect(wrapper.text()).toContain('勾稽一致')

    // 检查分段标签栏
    expect(wrapper.text()).toContain('研发辅助账')
    expect(wrapper.text()).toContain('研发项目')
    expect(wrapper.text()).toContain('待补充归集')
    expect(wrapper.text()).toContain('总账勾稽与证据')
  })

  it('子标签切换及参数联动：通过 URL query 或点击切换至指定子标签', async () => {
    setActivePinia(createPinia())
    const testRouter = createTestRouter('projects')
    await testRouter.isReady()

    const TestHost = defineComponent({
      render() {
        return h(NDialogProvider, null, {
          default: () =>
            h(NMessageProvider, null, {
              default: () => h(RdExpensesPage),
            }),
        })
      },
    })

    const wrapper = mount(TestHost, {
      global: {
        plugins: [testRouter],
      },
    })

    await nextTick()
    await new Promise((resolve) => setTimeout(resolve, 60))

    // 项目子标签内包含项目列表
    expect(wrapper.text()).toContain('智能记账算法研发')
    expect(wrapper.text()).toContain('发票 OCR 增强模型')
    expect(wrapper.text()).toContain('RD-2026-01')
  })
})
