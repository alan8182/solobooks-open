/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { mergeOpeningRows, mergeYtdRows } from '@/utils/openingMerge'

interface Row {
  account_code: string
  account_name?: string
  debit_balance: number | string
  credit_balance: number | string
  partner_name?: string
  originalCode?: string
}

const row = (r: Partial<Row> & { account_code: string }): Row => ({
  account_name: '',
  debit_balance: 0,
  credit_balance: 0,
  partner_name: '',
  ...r,
})

/**
 * 回归的是 AI 期初确认卡「去期初工作台细调」静默丢行：
 * 草稿合并曾用 Map(account_code)，同一科目下的多个往来单位只留最后一条。
 * 实测一张 23 行的卡只剩 12 行，丢的 11 行全是往来单位——正好抹掉
 * 「欠谁钱、哪些款没收回来」。合并规则收敛到 openingMerge 后与 Excel 导入同源。
 */
describe('mergeOpeningRows', () => {
  it('同科目多客商各成一行，不丢行', () => {
    const existing = [row({ account_code: '1122', account_name: '应收账款' })]
    const incoming = [
      row({ account_code: '1122', account_name: '应收账款', debit_balance: '543.30', partner_name: '梧桐树保险经纪有限公司' }),
      row({ account_code: '1122', account_name: '应收账款', debit_balance: '1614.12', partner_name: '东方大地（武汉）保险经纪有限公司' }),
    ]
    const merged = mergeOpeningRows(existing, incoming)
    const partners = merged.filter((r) => r.account_code === '1122' && r.partner_name)
    expect(partners).toHaveLength(2)
    expect(partners.map((r) => r.partner_name).sort()).toEqual([
      '东方大地（武汉）保险经纪有限公司',
      '梧桐树保险经纪有限公司',
    ])
    expect(partners.map((r) => Number(r.debit_balance)).sort((a, b) => a - b)).toEqual([543.3, 1614.12])
  })

  it('同一科目拆出客商行后，原有无客商行清零，避免重复计数', () => {
    const existing = [row({ account_code: '2202', account_name: '应付账款', credit_balance: 9999 })]
    const incoming = [
      row({ account_code: '2202', credit_balance: '2400.00', partner_name: '杭州公瑾财务咨询有限公司' }),
      row({ account_code: '2202', credit_balance: '6422.66', partner_name: '河南山南水北人力资源服务有限公司' }),
    ]
    const merged = mergeOpeningRows(existing, incoming)
    const plain = merged.filter((r) => r.account_code === '2202' && !r.partner_name)
    expect(plain).toHaveLength(1)
    expect(Number(plain[0].credit_balance)).toBe(0)
    const total = merged
      .filter((r) => r.account_code === '2202')
      .reduce((s, r) => s + Number(r.credit_balance), 0)
    expect(Math.round(total * 100) / 100).toBe(8822.66)
  })

  it('无客商行与客商行净额相等时视为纯汇总并清零（草稿通道不经过解析器）', () => {
    const existing = [row({ account_code: '2202', account_name: '应付账款', credit_balance: 8822.66 })]
    const incoming = [
      row({ account_code: '2202', credit_balance: '8822.66' }), // 合计行
      row({ account_code: '2202', credit_balance: '2400.00', partner_name: '公瑾' }),
      row({ account_code: '2202', credit_balance: '6422.66', partner_name: '山南水北' }),
    ]
    const merged = mergeOpeningRows(existing, incoming)
    const plain = merged.filter((r) => r.account_code === '2202' && !r.partner_name)
    expect(Number(plain[0].credit_balance)).toBe(0)
    const total = merged
      .filter((r) => r.account_code === '2202')
      .reduce((s, r) => s + Number(r.credit_balance), 0)
    expect(Math.round(total * 100) / 100).toBe(8822.66)
  })

  it('同科目既有客商行又有无客商明细时，无客商明细金额保留', () => {
    // 回归：2241 下 2241002 公积金不挂往来单位，四舍五入后与客商行同属 2241。
    // 曾经的合并规则把无客商行一律清零且不回填，页面合计比导入弹窗少 1,200。
    const existing = [row({ account_code: '2241', account_name: '其他应付款', credit_balance: 32735.72 })]
    const incoming = [
      row({ account_code: '2241', credit_balance: '35135.72', partner_name: '张三' }),
      row({ account_code: '2241', debit_balance: '1200.00', originalCode: '2241002' }),
    ]
    const merged = mergeOpeningRows(existing, incoming)
    const plain = merged.filter((r) => r.account_code === '2241' && !r.partner_name)
    expect(plain).toHaveLength(1)
    expect(Number(plain[0].debit_balance)).toBe(1200)
    expect(Number(plain[0].credit_balance)).toBe(0)
    expect(plain[0].originalCode).toBe('2241002')
  })

  it('无客商行按科目回填到原有行，不新增行', () => {
    const existing = [row({ account_code: '1002', account_name: '银行存款' })]
    const incoming = [row({ account_code: '1002', account_name: '银行存款', debit_balance: '5124.30' })]
    const merged = mergeOpeningRows(existing, incoming)
    expect(merged).toHaveLength(1)
    expect(Number(merged[0].debit_balance)).toBe(5124.3)
  })

  it('已存在的同科目同客商行就地更新，不产生重复行', () => {
    const existing = [
      row({ account_code: '1122', debit_balance: 100, partner_name: '甲公司' }),
      row({ account_code: '1122', debit_balance: 200, partner_name: '乙公司' }),
    ]
    const merged = mergeOpeningRows(existing, [
      row({ account_code: '1122', debit_balance: 555, partner_name: '甲公司' }),
    ])
    expect(merged.filter((r) => r.account_code === '1122')).toHaveLength(2)
    expect(Number(merged.find((r) => r.partner_name === '甲公司')!.debit_balance)).toBe(555)
    expect(Number(merged.find((r) => r.partner_name === '乙公司')!.debit_balance)).toBe(200)
  })

  it('金额为 0 的客商行不入表', () => {
    const merged = mergeOpeningRows(
      [row({ account_code: '2202' })],
      [row({ account_code: '2202', partner_name: '空壳公司' })],
    )
    expect(merged.filter((r) => r.partner_name)).toHaveLength(0)
  })

  it('传入的行覆盖 1122/1123/2202/2241 多单位时一行不少', () => {
    const incoming = [
      row({ account_code: '1122', debit_balance: '543.30', partner_name: '梧桐树' }),
      row({ account_code: '1122', debit_balance: '1614.12', partner_name: '东方大地' }),
      row({ account_code: '1123', debit_balance: '16937.56', partner_name: '安徽搜才' }),
      row({ account_code: '1123', debit_balance: '400.00', partner_name: '深度求索' }),
      row({ account_code: '1123', debit_balance: '1300.00', partner_name: '知到财税' }),
      row({ account_code: '2202', credit_balance: '2400.00', partner_name: '公瑾' }),
      row({ account_code: '2202', credit_balance: '600.00', partner_name: '腾讯' }),
      row({ account_code: '2241', credit_balance: '1200.00', partner_name: '李晴' }),
    ]
    const merged = mergeOpeningRows([], incoming)
    expect(merged).toHaveLength(8)
  })
})

describe('mergeOpeningRows 不丢表外科目行', () => {
  it('科目表里没有的无客商行追加而不是静默丢弃', () => {
    const merged = mergeOpeningRows(
      [row({ account_code: '1001', account_name: '库存现金' })],
      [row({ account_code: '2221001', account_name: '应交税费-应交增值税', debit_balance: '1540.04' })],
    )
    expect(merged.find((r) => r.account_code === '2221001')?.debit_balance).toBe(1540.04)
  })
})

describe('mergeOpeningRows 保留原文件科目编码', () => {
  it('无客商行与客商行的 originalCode 都随合并保留', () => {
    const merged = mergeOpeningRows(
      [row({ account_code: '1001', account_name: '库存现金' })],
      [
        row({ account_code: '1001', debit_balance: '50.00', originalCode: '1001' }),
        row({ account_code: '1122', debit_balance: '543.30', partner_name: '梧桐树', originalCode: '1122001' }),
      ],
    )
    expect(merged.find((r) => r.account_code === '1001')?.originalCode).toBe('1001')
    expect(merged.find((r) => r.account_code === '1122')?.originalCode).toBe('1122001')
  })
})

describe('mergeYtdRows', () => {
  it('损益行不拆行，按科目回填并计数', () => {
    const existing = [
      row({ account_code: '5001', account_name: '主营业务收入' }),
      row({ account_code: '5602999', account_name: '管理费用-其他' }),
    ]
    const { rows, matchedCount } = mergeYtdRows(existing, [
      row({ account_code: '5001', credit_balance: '120000.00' }),
      row({ account_code: '5602999', debit_balance: '80000.00' }),
    ])
    expect(rows).toHaveLength(2)
    expect(matchedCount).toBe(2)
    expect(Number(rows[0].credit_balance)).toBe(120000)
    expect(Number(rows[1].debit_balance)).toBe(80000)
  })
})
