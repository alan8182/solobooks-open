/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import {
  buildBankRows,
  bankColumnDescriptionRole,
  detectBankColumnMapping,
  formatBankDate,
  parseMoney,
  extractStatementCounterparty,
} from '@/utils/bankImport'

// 杭州银行交易明细报表的真实表头：日期信息只在「交易时间」列
const HZ_BANK_HEADERS = [
  '序号', '己方银行账号', '别名', '己方银行户名', '对手方银行户名', '对手方银行账号',
  '己方开户行', '对手方开户名', '支出金额（元）', '收入金额（元）', '交易时间',
  '账户余额（元）', '摘要', '附言',
]

describe('formatBankDate', () => {
  it('解析网银常见的日期形态', () => {
    expect(formatBankDate('2026-08-08 05:50:27')).toBe('2026-08-08 05:50:27')
    expect(formatBankDate('2026-08-08')).toBe('2026-08-08')
    expect(formatBankDate('2026/8/8')).toBe('2026-08-08')
    expect(formatBankDate('20260808')).toBe('2026-08-08')
  })

  it('解析 XLSX 日期序列号，空值原样返回', () => {
    expect(formatBankDate(45800)).toMatch(/^\d{4}-\d{2}-\d{2}$/)
    expect(formatBankDate('')).toBe('')
    expect(formatBankDate(null)).toBe('')
  })
})

describe('detectBankColumnMapping', () => {
  it('杭州银行表头：交易时间是唯一日期来源，须兜底选中', () => {
    const map = detectBankColumnMapping(HZ_BANK_HEADERS)
    expect(map['交易时间']).toBe('transaction_date')
    expect(map['支出金额（元）']).toBe('expense_amount')
    expect(map['收入金额（元）']).toBe('income_amount')
    expect(map['账户余额（元）']).toBe('balance')
    expect(map['摘要']).toBe('description')
    // 附言/对手方列不映射，由描述增强独立收集
    expect(map['附言']).toBe('')
    expect(map['对手方开户名']).toBe('')
  })

  it('交易日期与交易时间并存时优先日期列，避免起息时间覆盖交易日', () => {
    const map = detectBankColumnMapping(['交易日期', '交易时间', '交易金额', '余额'])
    expect(map['交易日期']).toBe('transaction_date')
    expect(map['交易时间']).toBe('')
  })

  it('付款人名称等对手方列不会被误判为金额列', () => {
    const map = detectBankColumnMapping(['付款人名称', '交易金额', '交易日期'])
    expect(map['付款人名称']).toBe('')
    expect(map['交易金额']).toBe('amount')
  })
})

describe('bankColumnDescriptionRole', () => {
  it('识别会被自动并入描述的列及其角色', () => {
    expect(bankColumnDescriptionRole('对手方银行户名')).toBe('对方户名')
    expect(bankColumnDescriptionRole('对方户名')).toBe('对方户名')
    expect(bankColumnDescriptionRole('对手方')).toBe('对方户名')
    expect(bankColumnDescriptionRole('收款人名称')).toBe('收款人')
    expect(bankColumnDescriptionRole('收款人户名')).toBe('收款人')
    expect(bankColumnDescriptionRole('收款方')).toBe('收款人')
    expect(bankColumnDescriptionRole('付款人名称')).toBe('付款人')
    expect(bankColumnDescriptionRole('付款人户名')).toBe('付款人')
    expect(bankColumnDescriptionRole('付款单位')).toBe('付款人')
    expect(bankColumnDescriptionRole('附言')).toBe('附言')
    expect(bankColumnDescriptionRole('摘要')).toBe('摘要')
    expect(bankColumnDescriptionRole('交易类型')).toBe('交易类型')
  })

  it('账号列与开户行/开户名列（杭州银行的对方行名）不并入描述', () => {
    expect(bankColumnDescriptionRole('对手方开户名')).toBe('')
    expect(bankColumnDescriptionRole('对手方银行账号')).toBe('')
    expect(bankColumnDescriptionRole('收款人开户行')).toBe('')
    expect(bankColumnDescriptionRole('收款人账号')).toBe('')
    expect(bankColumnDescriptionRole('付款人开户行')).toBe('')
    expect(bankColumnDescriptionRole('己方开户行')).toBe('')
    expect(bankColumnDescriptionRole('己方银行户名')).toBe('')
    expect(bankColumnDescriptionRole('序号')).toBe('')
  })

  it('「对方账号名称」含名称词，仍按户名收集', () => {
    expect(bankColumnDescriptionRole('对方账号名称')).toBe('对方户名')
  })
})

describe('extractStatementCounterparty', () => {
  it('正确从摘要中提取对方户名', () => {
    expect(
      extractStatementCounterparty('入账 | 附言: 0808_1113670810 (对方: 财付通支付科技有限公司)')
    ).toBe('财付通支付科技有限公司')
    expect(
      extractStatementCounterparty('汇兑 (对方: 何静谷)')
    ).toBe('何静谷')
    expect(
      extractStatementCounterparty('网银转账')
    ).toBe('')
  })
})

describe('buildBankRows', () => {
  const hzRow = (over: Record<string, unknown> = {}) => ({
    '序号': '1',
    '己方银行账号': '3301041060008541793',
    '己方银行户名': '芥子星辰（杭州）智能科技有限责任公司',
    '对手方银行户名': '财付通支付科技有限公司',
    '对手方银行账号': '243300133',
    '己方开户行': '杭州银行余杭支行',
    '对手方开户名': '财付通支付科技有限公司',
    '支出金额（元）': '0.00',
    '收入金额（元）': '5.95',
    '交易时间': '2026-08-08 05:50:27',
    '账户余额（元）': '99,361.17',
    '摘要': '入账',
    '附言': '0808_1113670810',
    ...over,
  })

  it('存入为正、支取为负，描述为统一管道口径', () => {
    const { rows, skippedInvalidDate } = buildBankRows([hzRow()], detectBankColumnMapping(HZ_BANK_HEADERS))
    expect(skippedInvalidDate).toBe(0)
    expect(rows).toHaveLength(1)
    expect(rows[0].transaction_date).toBe('2026-08-08 05:50:27')
    expect(rows[0].amount).toBe(5.95)
    expect(rows[0].balance).toBe(99361.17)
    expect(rows[0].description).toBe('入账 | 附言: 0808_1113670810 (对方: 财付通支付科技有限公司)')
  })

  it('支出行金额为负', () => {
    const { rows } = buildBankRows(
      [hzRow({ '支出金额（元）': '300.00', '收入金额（元）': '0.00', '摘要': '报销微信开放平台认证费用' })],
      detectBankColumnMapping(HZ_BANK_HEADERS),
    )
    expect(rows[0].amount).toBe(-300)
  })

  it('杭州银行个人转入：对方户名取「对手方银行户名」，开户名列是行名不能冒充', () => {
    const { rows } = buildBankRows(
      [hzRow({
        '对手方银行户名': '何静谷',
        '对手方银行账号': '6212261202018366618',
        '对手方开户名': '中国工商银行总行清算中心',
        '收入金额（元）': '20000.00',
        '摘要': '汇兑',
        '附言': '何静谷实缴出资款',
      })],
      detectBankColumnMapping(HZ_BANK_HEADERS),
    )
    expect(rows[0].description).toBe('汇兑 | 附言: 何静谷实缴出资款 (对方: 何静谷)')
  })

  it('无户名列的银行退化用裸对手方列，账号列永不并入描述', () => {
    const { rows } = buildBankRows(
      [{ '交易日期': '2026/07/01', '金额': '-88.5', '对手方': '某供应商', '对方账号': '6222020200112233445' }],
      { '交易日期': 'transaction_date', '金额': 'amount', '对手方': '', '对方账号': '' },
    )
    expect(rows[0].description).toBe('(对方: 某供应商)')
  })

  it('单列收款人名称时，支出流水正确收集为对方户名', () => {
    const { rows } = buildBankRows(
      [{
        '交易日期': '2026-08-01',
        '支出金额': '1500.00',
        '收入金额': '0.00',
        '收款人名称': '阿里云计算有限公司',
        '摘要': '服务器续费',
      }],
      {
        '交易日期': 'transaction_date',
        '支出金额': 'expense_amount',
        '收入金额': 'income_amount',
        '收款人名称': '',
        '摘要': 'description',
      },
    )
    expect(rows[0].amount).toBe(-1500)
    expect(rows[0].description).toBe('服务器续费 (对方: 阿里云计算有限公司)')
    expect(extractStatementCounterparty(rows[0].description)).toBe('阿里云计算有限公司')
  })

  it('同时存在付款人与收款人双列时，按收支方向精准归属对方', () => {
    const rawData = [
      {
        '交易日期': '2026-08-02',
        '金额': '-3000.00',
        '付款人名称': '本公司',
        '收款人名称': '张三（技术外包）',
        '摘要': '外包劳务费',
      },
      {
        '交易日期': '2026-08-03',
        '金额': '50000.00',
        '付款人名称': '客户李四有限公司',
        '收款人名称': '本公司',
        '摘要': '服务收入',
      },
    ]
    const mapping = {
      '交易日期': 'transaction_date',
      '金额': 'amount',
      '付款人名称': '',
      '收款人名称': '',
      '摘要': 'description',
    }
    const { rows } = buildBankRows(rawData, mapping)
    // 支出行取收款人
    expect(rows[0].description).toBe('外包劳务费 (对方: 张三（技术外包）)')
    expect(extractStatementCounterparty(rows[0].description)).toBe('张三（技术外包）')
    // 收入行取付款人
    expect(rows[1].description).toBe('服务收入 (对方: 客户李四有限公司)')
    expect(extractStatementCounterparty(rows[1].description)).toBe('客户李四有限公司')
  })

  it('支持用户手动显式映射 payee_name 与 payer_name', () => {
    const rawData = [
      {
        '交易日期': '2026-08-04',
        '金额': '-200.00',
        '自定义收方': '特约商户A',
      },
    ]
    const { rows } = buildBankRows(rawData, {
      '交易日期': 'transaction_date',
      '金额': 'amount',
      '自定义收方': 'payee_name',
    })
    expect(rows[0].description).toBe('(对方: 特约商户A)')
  })

  it('解析不出合法日期的行被跳过并计数，不送后端', () => {
    const { rows, skippedInvalidDate } = buildBankRows(
      [hzRow(), hzRow({ '交易时间': '交易时间', '附言': '附言', '摘要': '摘要' })],
      detectBankColumnMapping(HZ_BANK_HEADERS),
    )
    expect(rows).toHaveLength(1)
    expect(skippedInvalidDate).toBe(1)
  })

  it('尊重用户手动调整的映射', () => {
    const { rows } = buildBankRows(
      [{ '发生日期': '2026/07/01', '金额': '-88.5', '对方': '某供应商' }],
      { '发生日期': 'transaction_date', '金额': 'amount', '对方': '' },
    )
    expect(rows[0].transaction_date).toBe('2026-07-01')
    expect(rows[0].amount).toBe(-88.5)
  })

  it('金额字段为逗号千分位文本时正常解析', () => {
    expect(parseMoney('99,361.17')).toBe(99361.17)
    expect(parseMoney('')).toBe(0)
  })
})
