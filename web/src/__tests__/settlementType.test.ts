/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import {
  SETTLEMENT_TYPE_LABELS,
  isSettlementType,
  settlementCancelMessage,
  settlementTypeLabel,
} from '@/utils/settlementType'

describe('settlementType', () => {
  it('labels every settlement source the backend can generate', () => {
    expect(settlementTypeLabel('wechat_settlement', 'tag')).toBe('微信结算凭证')
    expect(settlementTypeLabel('alipay_settlement', 'tag')).toBe('支付宝结算凭证')
    expect(settlementTypeLabel('platform_settlement', 'tag')).toBe('平台结算凭证')
    expect(settlementTypeLabel('alipay_settlement', 'full')).toBe('支付宝净额结算')
    expect(Object.keys(SETTLEMENT_TYPE_LABELS)).toEqual([
      'wechat_settlement', 'alipay_settlement', 'platform_settlement',
    ])
  })

  it('falls back to the raw type for unknown kinds and empty for missing', () => {
    expect(settlementTypeLabel('custom', 'tag')).toBe('custom')
    expect(settlementTypeLabel(undefined, 'tag')).toBe('')
    expect(settlementTypeLabel(null, 'full')).toBe('')
    expect(isSettlementType('alipay_settlement')).toBe(true)
    expect(isSettlementType('bank_import')).toBe(false)
  })

  it('names the real platform when cancelling a match', () => {
    expect(settlementCancelMessage('alipay_settlement'))
      .toBe('对账关联已取消；原账目仍保留在账上，支付宝结算凭证已一并删除')
    expect(settlementCancelMessage('wechat_settlement')).toContain('微信结算凭证')
    // 旧数据没有 reconciliationType 时不能编造平台
    expect(settlementCancelMessage(undefined))
      .toBe('对账关联已取消；原账目仍保留在账上，结算凭证已一并删除')
  })
})
