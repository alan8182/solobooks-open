/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import ShareView from '@/pages/share/index.vue'
import { routes } from '@/router'
import * as auditShareApi from '@/api/auditShare'

const harness = vi.hoisted(() => ({
  message: { warning: vi.fn(), success: vi.fn(), error: vi.fn() },
}))

vi.mock('vue-router', async (importOriginal) => ({
  // 保留 createRouter 等真实导出（本测试要 import 真实 routes 做公开性断言），只替换组合式 API
  ...await importOriginal<typeof import('vue-router')>(),
  useRoute: () => ({ params: { token: 'sas_example_token' } }),
}))
vi.mock('naive-ui', async (importOriginal) => ({
  ...await importOriginal<typeof import('naive-ui')>(),
  useMessage: () => harness.message,
}))

const SNAPSHOT = {
  generatedAt: '2026-10-20T10:00:00+00:00',
  month: '2026-09',
  disclaimer: '本页面为账套数据健康度体检视图，不构成法定代理记账或审计意见。',
  checks: {
    debit: '100.00', credit: '100.00',
    checks: [
      { code: 'LEDGER_TO_LEDGER', name: '账账核对', status: 'passed' },
      { code: 'VOUCHER_TO_SOURCE', name: '账证核对', status: 'blocking', pendingCount: 2 },
    ],
    accounts: [],
  },
  trialBalance: [
    { accountCode: '1002', accountName: '银行存款', debit: '100.00', credit: '0.00', balance: '100.00' },
  ],
  anomalies: [
    { id: 'a1', type: 'receipt', level: 'high', title: '客户餐饮 - 补充票据', description: '¥500，这笔支出需要补齐发票', entryId: 'e1', amount: '500' },
    { id: 'a2', type: 'risk', level: 'medium', title: '招待费复核', entryId: 'e2' },
  ],
  unmatched: { bank_only_count: 1, book_only_count: 0, bank_only: [{ date: '2026-09-01', description: '转账', amount: 10 }] },
  accruals: { estimateCount: 0 },
  incomeSummary: { revenue: '10000.00', expense: '3000.00', profit: '7000.00' },
}

function mockApi(overrides: Partial<typeof auditShareApi> = {}) {
  vi.mocked(auditShareApi.fetchShareView).mockResolvedValue({
    share: {
      shareId: 's1', month: '2026-09', anonymized: true, companyName: '杭**科技',
      expiresAt: null, viewCount: 1, generatedAt: '2026-10-20T10:00:00+00:00',
      disclaimer: SNAPSHOT.disclaimer,
    },
    snapshot: SNAPSHOT as never,
  })
  vi.mocked(auditShareApi.fetchShareAnnotations).mockResolvedValue([])
  Object.assign(overrides)
}

function mountView() {
  return mount(ShareView, { global: { stubs: { NSpin: true, NTag: true, NButton: true, NInput: true } } })
}

beforeEach(() => {
  vi.spyOn(auditShareApi, 'fetchShareView')
  vi.spyOn(auditShareApi, 'fetchShareAnnotations')
  vi.spyOn(auditShareApi, 'createShareAnnotation')
  localStorage.removeItem('audit_share_author')
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('audit share 路由', () => {
  it('/share/:token 是公开路由（CPA 免注册可达）', () => {
    const route = routes.find((r) => r.name === 'AuditShareView')
    expect(route).toBeTruthy()
    expect(route?.meta?.requiresAuth).toBe(false)
    expect(route?.path).toBe('/share/:token')
  })

  it('/mine/audit-share 管理页需要登录', () => {
    const layout = routes.find((r) => r.path === '/')
    const child = layout?.children?.find((r) => r.name === 'AuditShareManage')
    expect(child?.path).toBe('mine/audit-share')
    expect(layout?.meta?.requiresAuth).toBe(true)
  })
})

describe('CPA 协审视图', () => {
  it('渲染体检五段动线与免责声明', async () => {
    mockApi()
    const wrapper = mountView()
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('杭**科技')
    expect(text).toContain('不构成法定代理记账')
    expect(text).toContain('一、体检总览')
    expect(text).toContain('二、异常清单（2）')
    expect(text).toContain('三、票流账勾稽')
    expect(text).toContain('四、科目余额表')
    expect(text).toContain('五、总体意见')
    // 账账核对通过、账证核对阻塞
    expect(text).toContain('账账核对')
    expect(text).toContain('待过账 2')
  })

  it('提交总体意见：先要求署名，署名后调用批注接口', async () => {
    mockApi()
    vi.mocked(auditShareApi.createShareAnnotation).mockResolvedValue({
      id: 'ann1', anchorType: 'overall', anchorId: null, body: '账实基本相符',
      authorName: '注册会计师·陈', status: 'open', createdAt: '2026-10-20T10:05:00+00:00',
    })
    const wrapper = mountView()
    await flushPromises()

    const vm = wrapper.vm as unknown as {
      overallOpinion: string
      authorName: string
      nicknameEditing: boolean
      submitOverall: () => Promise<void>
    }
    vm.overallOpinion = '账实基本相符，注意招待费限额'
    await vm.submitOverall()
    // 未署名：不发请求，进入署名编辑态
    expect(auditShareApi.createShareAnnotation).not.toHaveBeenCalled()
    expect(vm.nicknameEditing).toBe(true)

    vm.authorName = '注册会计师·陈'
    await vm.submitOverall()
    expect(auditShareApi.createShareAnnotation).toHaveBeenCalledWith('sas_example_token', {
      authorName: '注册会计师·陈',
      anchorType: 'overall',
      anchorId: null,
      body: '账实基本相符，注意招待费限额',
    })
    // 署名已记住，意见框清空
    expect(localStorage.getItem('audit_share_author')).toBe('注册会计师·陈')
    expect(vm.overallOpinion).toBe('')
  })

  it('链接失效（410）渲染终止态而非重试态', async () => {
    mockApi()
    vi.mocked(auditShareApi.fetchShareView).mockRejectedValue({
      response: { status: 410, data: { detail: '链接已撤销' } },
    })
    const wrapper = mountView()
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('协审链接已失效')
    expect(text).toContain('请联系经营者重新生成')
  })
})
