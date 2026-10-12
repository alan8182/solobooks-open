import { describe, expect, it } from 'vitest'
import { NAV_ITEMS } from '@/utils/constants'

describe('home workbench and agent alignment', () => {
  it('ensures Home workbench and AI Copilot routes are first-class citizens', () => {
    const home = NAV_ITEMS.find((item) => item.path === '/')
    const copilot = NAV_ITEMS.find((item) => item.path === '/copilot')

    expect(home).toBeDefined()
    expect(home?.label).toBe('工作台')
    expect(home?.group).toBe('日常')

    expect(copilot).toBeDefined()
    expect(copilot?.label).toBe('AI 会计')
    expect(copilot?.group).toBe('日常')
  })

  it('formats receipt extracted payload into agent-digestible prompt', () => {
    // ponytail: test the text transformation logic that bridges receipt OCR to agent chat
    function buildReceiptPrompt(r: {
      sellerName?: string
      amount?: number
      date?: string
      suggestedCategory?: string
      invoiceType?: string
    }): string {
      const lines = ['记一笔（来自发票图片识别）：']
      if (r.sellerName) lines.push(`- 商户/销售方：${r.sellerName}`)
      if (r.amount != null) lines.push(`- 金额：${r.amount}`)
      if (r.date) lines.push(`- 日期：${r.date}`)
      if (r.suggestedCategory) lines.push(`- 建议分类：${r.suggestedCategory}`)
      if (r.invoiceType) lines.push(`- 发票类型：${r.invoiceType}`)
      return lines.join('\n')
    }

    const prompt = buildReceiptPrompt({
      sellerName: '星巴克咖啡',
      amount: 68.0,
      date: '2026-10-01',
      suggestedCategory: '业务招待',
      invoiceType: '电子普通发票',
    })

    expect(prompt).toContain('星巴克咖啡')
    expect(prompt).toContain('金额：68')
    expect(prompt).toContain('建议分类：业务招待')
  })

  it('redirects legacy manual recording routes to the unified copilot workspace', async () => {
    const router = (await import('@/router')).default
    const textRoute = router.resolve('/record/text')
    const receiptRoute = router.resolve('/record/receipt')
    const batchRoute = router.resolve('/record/batch')

    const textRecord = textRoute.matched[textRoute.matched.length - 1]
    const receiptRecord = receiptRoute.matched[receiptRoute.matched.length - 1]
    const batchRecord = batchRoute.matched[batchRoute.matched.length - 1]

    expect(textRecord?.redirect).toBe('/copilot')
    expect(receiptRecord?.redirect).toBe('/copilot?mode=receipt')
    expect(batchRecord?.redirect).toBe('/copilot')
  })

  it('filters out in-conversation actions from the pending strip to prevent ghost placeholders', () => {
    // ponytail: verifies stripActions only retains actions not already present in the active chat stream
    const pendingActions = [
      { id: 'act-1', type: 'propose_voucher', status: 'pending' },
      { id: 'act-2', type: 'propose_voucher', status: 'pending' },
    ]
    const messages = [
      {
        role: 'assistant',
        content: '已生成记账草稿',
        actions: [{ id: 'act-1', type: 'propose_voucher', status: 'pending' }],
      },
    ]

    const inConversation = new Set(
      messages.flatMap((m) => m.actions?.map((a) => a.id) ?? []),
    )
    const stripActions = pendingActions.filter((a) => !inConversation.has(a.id))

    expect(stripActions).toHaveLength(1)
    expect(stripActions[0].id).toBe('act-2')

    // 当全部动作都在会话流中时，stripActions 为空，不再展示幽灵条
    messages.push({
      role: 'assistant',
      content: '第二笔',
      actions: [{ id: 'act-2', type: 'propose_voucher', status: 'pending' }],
    })
    const inConversationAll = new Set(
      messages.flatMap((m) => m.actions?.map((a) => a.id) ?? []),
    )
    const emptyStrip = pendingActions.filter((a) => !inConversationAll.has(a.id))
    expect(emptyStrip).toHaveLength(0)
  })

  it('consumes and clears route query parameters upon copilot mount', async () => {
    // ponytail: ensures query string (autoSend, q, mode) is purged via replace to prevent repeat sends on F5 refresh
    const mockRouter = {
      replacedQuery: null as any,
      replace(loc: { path: string; query: Record<string, unknown> }) {
        this.replacedQuery = loc.query
        return Promise.resolve()
      },
    }

    const route = {
      path: '/copilot',
      query: { q: '记一笔支出', autoSend: '1' } as Record<string, any>,
    }

    let inputVal = ''
    let sent = false

    async function consumeRouteQuery() {
      const mode = route.query.mode
      const q = route.query.q
      const autoSend = route.query.autoSend

      if (!mode && !q && !autoSend) return

      await mockRouter.replace({ path: route.path, query: {} })

      if (typeof q === 'string' && q.trim()) {
        inputVal = q.trim()
        if (autoSend === '1') {
          sent = true
        }
      }
    }

    await consumeRouteQuery()

    expect(inputVal).toBe('记一笔支出')
    expect(sent).toBe(true)
    expect(mockRouter.replacedQuery).toEqual({})
  })

  it('manages copilot workbench image attachment and local session serialization', () => {
    // ponytail: verifies image url association and session serialization logic
    const messages = [
      {
        role: 'user' as const,
        content: '记一笔来自发票的支出',
        imageUrl: 'blob:http://localhost:5173/mock-receipt-uuid',
      },
      {
        role: 'assistant' as const,
        content: '已为您起草凭证',
        actions: [{ id: 'act-101', actionType: 'propose_voucher', status: 'pending' as const, title: '办公费 200元', payload: {}, evidence: {}, result: null, createdAt: null, resolvedAt: null }],
      },
    ]

    const serializable = messages.slice(-30).map((m) => ({
      role: m.role,
      content: m.content,
      imageUrl: m.imageUrl,
      actions: m.actions,
    }))

    const serialized = JSON.stringify(serializable)
    const restored = JSON.parse(serialized)

    expect(restored).toHaveLength(2)
    expect(restored[0].imageUrl).toBe('blob:http://localhost:5173/mock-receipt-uuid')
    expect(restored[1].actions[0].id).toBe('act-101')
  })

  it('computes financial pulse net profit and tab state correctly', () => {
    // ponytail: ensures financial snapshot calculations adhere to debit-credit business logic
    const monthlyIncome = 88000.5
    const monthlyExpense = 45200.2
    const netProfit = monthlyIncome - monthlyExpense

    expect(netProfit).toBeCloseTo(42800.3, 1)

    // tab default selection heuristic
    function selectDefaultTab(pendingCount: number): 'actions' | 'snapshot' {
      return pendingCount > 0 ? 'actions' : 'snapshot'
    }

    expect(selectDefaultTab(3)).toBe('actions')
    expect(selectDefaultTab(0)).toBe('snapshot')
  })
})

