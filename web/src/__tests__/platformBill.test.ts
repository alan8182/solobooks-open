/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { emptyBillWarning } from '@/utils/platformBill'

describe('emptyBillWarning（空账单提示）', () => {
  it('支付宝单日账单点名具体日期，不再误导成格式错误', () => {
    // 真实案例：2026-11-10 全天空交易，旧文案提示「请检查文件格式」
    const message = emptyBillWarning({ periodStart: '2026-11-10', periodEnd: '2026-11-10' })
    expect(message).toContain('2026-11-10')
    expect(message).toContain('没有任何交易记录')
    expect(message).not.toContain('请检查文件格式')
    expect(message).toContain('无需导入')
  })

  it('跨日期间显示起止范围', () => {
    const message = emptyBillWarning({ periodStart: '2026-11-01', periodEnd: '2026-11-30' })
    expect(message).toContain('2026-11-01 ~ 2026-11-30')
  })

  it('微信等无 period 字段的账单退回通用文案', () => {
    expect(emptyBillWarning({ 总交易单数: 0 })).toContain('请确认导出的账单期间')
    expect(emptyBillWarning(undefined)).toContain('请确认导出的账单期间')
    expect(emptyBillWarning(null)).toContain('请确认导出的账单期间')
  })
})
