import { describe, expect, it } from 'vitest'
import { buildBankStatementDescription } from '@/utils/bankStatementDescription'

describe('buildBankStatementDescription', () => {
  it('omits a comment that repeats the summary', () => {
    expect(buildBankStatementDescription({
      typeName: '汇兑',
      summaryText: '投资款',
      commentText: '投资款',
      counterpartyName: '中国工商银行',
    })).toBe('汇兑 | 投资款 (对方: 中国工商银行)')
  })

  it('keeps a distinct comment and ignores null-like values', () => {
    expect(buildBankStatementDescription({
      typeName: '入账',
      summaryText: '货款',
      commentText: '订单 123',
      counterpartyName: 'null',
    })).toBe('入账 | 货款 | 附言: 订单 123')
  })
})
