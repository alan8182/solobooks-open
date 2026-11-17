/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { buildOpeningReconciliation } from '@/utils/openingReconciliation'

describe('期初对账视图派生', () => {
  it('按「期初 − 年初」推算本年净变动，并区分借贷方向', () => {
    const result = buildOpeningReconciliation(
      [
        { account_code: '1002', account_name: '银行存款', debit_balance: 600, credit_balance: 0 },
        { account_code: '1122', account_name: '应收账款', debit_balance: 0, credit_balance: 0 },
        { account_code: '2202', account_name: '应付账款', debit_balance: 200, credit_balance: 0 },
      ],
      [
        { account_code: '1002', account_name: '银行存款', debit_balance: 1000, credit_balance: 0 },
        { account_code: '1122', account_name: '应收账款', debit_balance: 300, credit_balance: 0 },
        { account_code: '2202', account_name: '应付账款', debit_balance: 0, credit_balance: 300 },
      ],
    )

    const bank = result.rows.find(r => r.account_code === '1002')!
    expect(bank.movementDebit).toBe(0)
    expect(bank.movementCredit).toBe(400)
    expect(bank.hasMovement).toBe(true)

    // 年内收清的应收：年初有余额、期初为 0，仍要出现在对账表里
    const receivable = result.rows.find(r => r.account_code === '1122')!
    expect(receivable.openingDebit).toBe(0)
    expect(receivable.movementCredit).toBe(300)

    // 方向翻转：年初贷 300 → 期初借 200，净变动借 500
    const payable = result.rows.find(r => r.account_code === '2202')!
    expect(payable.movementDebit).toBe(500)
    expect(payable.movementCredit).toBe(0)

    expect(result.movementCount).toBe(3)
  })

  it('按科目汇总客商行，不按客商拆行', () => {
    const result = buildOpeningReconciliation(
      [
        { account_code: '1122', account_name: '应收账款', debit_balance: 100, credit_balance: 0 },
        { account_code: '1122', account_name: '应收账款', debit_balance: 50, credit_balance: 0 },
      ],
      [
        { account_code: '1122', account_name: '应收账款', debit_balance: 120, credit_balance: 0 },
      ],
    )

    expect(result.rows).toHaveLength(1)
    expect(result.rows[0].openingDebit).toBe(150)
    expect(result.rows[0].yearStartDebit).toBe(120)
    expect(result.rows[0].movementDebit).toBe(30)
  })

  it('只有一方存在时也能出行：年初有、期初无（结清）与年初无、期初有（新增）', () => {
    const result = buildOpeningReconciliation(
      [{ account_code: '1221', account_name: '其他应收款', debit_balance: 80, credit_balance: 0 }],
      [{ account_code: '2241', account_name: '其他应付款', debit_balance: 0, credit_balance: 20 }],
    )

    expect(result.rows.map(r => r.account_code)).toEqual(['1221', '2241'])
    // 年初无、期初借 80：年内新增
    expect(result.rows.find(r => r.account_code === '1221')!.movementDebit).toBe(80)
    // 年初贷 20、期初 0：年内付清，变动在借方
    expect(result.rows.find(r => r.account_code === '2241')!.movementDebit).toBe(20)
    expect(result.rows.find(r => r.account_code === '2241')!.movementCredit).toBe(0)
  })

  it('年初合计与期初合计各自平衡时，变动净额合计为 0', () => {
    const result = buildOpeningReconciliation(
      [
        { account_code: '1001', debit_balance: 60, credit_balance: 0 },
        { account_code: '2202', debit_balance: 0, credit_balance: 60 },
      ],
      [
        { account_code: '1001', debit_balance: 100, credit_balance: 0 },
        { account_code: '2202', debit_balance: 0, credit_balance: 100 },
      ],
    )

    expect(result.totals.yearStartDiff).toBe(0)
    expect(result.totals.openingDiff).toBe(0)
    expect(result.totals.movementDebit).toBe(result.totals.movementCredit)
  })

  it('字符串金额与两位小数按分舍入', () => {
    const result = buildOpeningReconciliation(
      [{ account_code: '1001', debit_balance: '0.3' as unknown as number, credit_balance: 0 }],
      [{ account_code: '1001', debit_balance: '0.1' as unknown as number, credit_balance: 0 }],
    )

    expect(result.rows[0].movementDebit).toBe(0.2)
    expect(result.rows[0].openingDebit).toBe(0.3)
  })

  it('零余额科目不生成对账行', () => {
    const result = buildOpeningReconciliation(
      [
        { account_code: '1001', debit_balance: 100, credit_balance: 0 },
        { account_code: '1012', debit_balance: 0, credit_balance: 0 },
      ],
      [{ account_code: '1013', debit_balance: 0, credit_balance: 0 }],
    )

    expect(result.rows.map(r => r.account_code)).toEqual(['1001'])
  })

  it('年初数未知时透传标记，由页面决定显示「—」', () => {
    const result = buildOpeningReconciliation(
      [{ account_code: '1001', debit_balance: 100, credit_balance: 0 }],
      [],
      { yearStartKnown: false },
    )

    expect(result.yearStartKnown).toBe(false)
    expect(result.rows[0].yearStartDebit).toBe(0)
  })
})
