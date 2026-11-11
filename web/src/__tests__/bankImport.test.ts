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
  formatBankTime,
  parseMoney,
  extractStatementCounterparty,
  isBankCounterpartySourceBlocked,
  normalizeBankReference,
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

describe('normalizeBankReference', () => {
  it('接受可精确表示的流水号', () => {
    expect(normalizeBankReference('178234433994')).toBe('178234433994')
    expect(normalizeBankReference(178234433994)).toBe('178234433994')
    expect(normalizeBankReference(' 1782344339 94 ')).toBe('178234433994')
    expect(normalizeBankReference('178234433994.00')).toBe('178234433994')
    expect(normalizeBankReference('0012345')).toBe('0012345')
  })

  it('拒绝浮点失真、全零占位与科学计数法', () => {
    expect(normalizeBankReference(1e25)).toBe('')
    expect(normalizeBankReference(259755500999999978999999978)).toBe('')
    expect(normalizeBankReference('2.5975550099999998e+24')).toBe('')
    expect(normalizeBankReference('000000000000')).toBe('')
    expect(normalizeBankReference('')).toBe('')
    expect(normalizeBankReference(null)).toBe('')
    expect(normalizeBankReference(undefined)).toBe('')
    expect(normalizeBankReference(true)).toBe('')
    expect(normalizeBankReference('1'.repeat(65))).toBe('')
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

  it('交易日期与交易时间并存时优先日期列，时间列作时间源不作日期源', () => {
    const map = detectBankColumnMapping(['交易日期', '交易时间', '交易金额', '余额'])
    expect(map['交易日期']).toBe('transaction_date')
    expect(map['交易时间']).toBe('transaction_time')
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

  it('只拦截账户/开户字段，不按银行名称误伤真实对手户名', () => {
    expect(isBankCounterpartySourceBlocked('对手方开户名')).toBe(true)
    expect(isBankCounterpartySourceBlocked('收款人开户行')).toBe(true)
    expect(isBankCounterpartySourceBlocked('对方银行账号')).toBe(true)
    expect(isBankCounterpartySourceBlocked('己方银行户名')).toBe(true)
    expect(isBankCounterpartySourceBlocked('对方户名')).toBe(false)
    expect(isBankCounterpartySourceBlocked('对方账号名称')).toBe(false)
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

  it('显式映射开户行/开户名时仍不会拼进交易对手', () => {
    const rawData = [
      {
        '交易日期': '2026-05-31',
        '金额': '-500.00',
        '摘要': '汇兑',
        '对手方开户名': '招商银行股份有限公司杭州分行',
        '收款人开户行': '招商银行股份有限公司杭州分行',
        '付款人开户行': '招商银行股份有限公司杭州分行',
      },
    ]
    const { rows } = buildBankRows(rawData, {
      '交易日期': 'transaction_date',
      '金额': 'amount',
      '摘要': 'description',
      '对手方开户名': 'counterparty_name',
      '收款人开户行': 'payee_name',
      '付款人开户行': 'payer_name',
    })
    expect(rows[0].description).toBe('汇兑')
    expect(extractStatementCounterparty(rows[0].description)).toBe('')
  })

  it('合法的对方户名列仍允许银行作为真实交易对手', () => {
    const { rows } = buildBankRows(
      [{
        '交易日期': '2026-05-31',
        '金额': '-500.00',
        '摘要': '汇兑',
        '对方户名': '招商银行股份有限公司杭州分行',
      }],
      {
        '交易日期': 'transaction_date',
        '金额': 'amount',
        '摘要': 'description',
        '对方户名': '',
      },
    )
    expect(rows[0].description).toBe('汇兑 (对方: 招商银行股份有限公司杭州分行)')
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

// 中国银行企业网银导出：日期与时间拆成两列（'20260520' + '21:46:46'），
// 交易金额单列带符号，表头中英双语。表头名取自真实文件，不要改简写。
const BOC_HEADERS = [
  '交易类型[ Transaction Type ]', '业务类型[ Business type ]',
  '付款人名称[ Payer\'s Name ]', '收款人名称[ Payee\'s Name ]',
  '交易日期[ Transaction Date ]', '交易时间[ Transaction time ]',
  '交易货币[ Trade Currency ]', '交易金额[ Trade Amount ]',
  '交易后余额[ After-transaction balance ]',
  '交易流水号[ Transaction reference number ]',
  '客户申请号[ Online Banking Transaction Ref.(Bank Ref.) ]',
  '客户业务编号[ Customer Transaction Ref.(Customer Ref.) ]',
  '凭证号码[ Voucher number ]', '记录标识号[ Record ID ]',
  '摘要[ Reference ]',
]

const bocRow = (over: Record<string, unknown> = {}) => ({
  '交易类型[ Transaction Type ]': '往账',
  '业务类型[ Business type ]': '收费',
  '付款人名称[ Payer\'s Name ]': '陕西澜晞文化传媒有限责任公司',
  '收款人名称[ Payee\'s Name ]': '',
  '交易日期[ Transaction Date ]': '20260520',
  '交易时间[ Transaction time ]': '21:46:46',
  '交易货币[ Trade Currency ]': 'CNY',
  '交易金额[ Trade Amount ]': '-4.50',
  '交易后余额[ After-transaction balance ]': '220.50',
  '交易流水号[ Transaction reference number ]': '178234433994',
  '客户申请号[ Online Banking Transaction Ref.(Bank Ref.) ]': '',
  '客户业务编号[ Customer Transaction Ref.(Customer Ref.) ]': '',
  '凭证号码[ Voucher number ]': '',
  '记录标识号[ Record ID ]': '259755500999999978999999978',
  '摘要[ Reference ]': '对公跨行转账汇款手续费',
  ...over,
})

describe('formatBankTime', () => {
  it('归一化裸时间、补零与缺秒', () => {
    expect(formatBankTime('21:46:46')).toBe('21:46:46')
    expect(formatBankTime('9:09')).toBe('09:09:00')
    expect(formatBankTime('09:09:10')).toBe('09:09:10')
  })

  it('时间列写成完整日期时间时取其时分秒', () => {
    expect(formatBankTime('2026-05-20 21:46:46')).toBe('21:46:46')
  })

  it('Excel 时间序列值（一天的小数）换算为时分秒', () => {
    expect(formatBankTime(21.75 / 24)).toBe('21:45:00')
    expect(formatBankTime(0)).toBe('00:00:00')
  })

  it('非时间值与越界小时返回空串', () => {
    expect(formatBankTime('交易时间')).toBe('')
    expect(formatBankTime('20260520')).toBe('')
    expect(formatBankTime(20260520)).toBe('')
    expect(formatBankTime('25:00')).toBe('')
    expect(formatBankTime('')).toBe('')
    expect(formatBankTime(null)).toBe('')
  })
})

describe('银行流水日期+时间合并', () => {
  it('中国银行表头：时间列识别为 transaction_time，日期列仍是交易日期', () => {
    const map = detectBankColumnMapping(BOC_HEADERS)
    expect(map['交易日期[ Transaction Date ]']).toBe('transaction_date')
    expect(map['交易时间[ Transaction time ]']).toBe('transaction_time')
    expect(map['交易后余额[ After-transaction balance ]']).toBe('balance')
    expect(map['交易金额[ Trade Amount ]']).toBe('amount')
  })

  it('交易流水号是身份列；摘要[ Reference ] 归描述，记录标识号等不映射', () => {
    const map = detectBankColumnMapping(BOC_HEADERS)
    expect(map['交易流水号[ Transaction reference number ]']).toBe('transaction_reference')
    expect(map['摘要[ Reference ]']).toBe('description')
    expect(map['记录标识号[ Record ID ]']).toBe('')
    expect(map['客户申请号[ Online Banking Transaction Ref.(Bank Ref.) ]']).toBe('')
    expect(map['客户业务编号[ Customer Transaction Ref.(Customer Ref.) ]']).toBe('')
    expect(map['凭证号码[ Voucher number ]']).toBe('')
  })

  it('空引用号列不覆盖已取到的流水号', () => {
    const { rows } = buildBankRows([bocRow()], {
      '交易日期[ Transaction Date ]': 'transaction_date',
      '交易流水号[ Transaction reference number ]': 'transaction_reference',
      '凭证号码[ Voucher number ]': 'transaction_reference',
    })
    expect(rows[0].transaction_reference).toBe('178234433994')
  })

  it('拆列的日期与时间并成 YYYY-MM-DD HH:mm:ss', () => {
    const { rows, skippedInvalidDate } = buildBankRows([bocRow()], detectBankColumnMapping(BOC_HEADERS))
    expect(skippedInvalidDate).toBe(0)
    expect(rows[0].transaction_date).toBe('2026-05-20 21:46:46')
    expect(rows[0].amount).toBe(-4.5)
    expect(rows[0].balance).toBe(220.5)
  })

  it('同一秒内的多笔同额手续费全部保留，靠余额区分不同交易', () => {
    // 真实文件里 2026-05-17 14:02:48 一秒内连发三笔 -4.50 手续费
    const rows = [
      bocRow({ '交易后余额[ After-transaction balance ]': '3,241.00' }),
      bocRow({ '交易后余额[ After-transaction balance ]': '2,736.50' }),
      bocRow({ '交易后余额[ After-transaction balance ]': '2,332.00' }),
    ]
    const { rows: built, skippedInvalidDate } = buildBankRows(rows, detectBankColumnMapping(BOC_HEADERS))
    expect(skippedInvalidDate).toBe(0)
    expect(built).toHaveLength(3)
    expect(built.map(r => r.balance)).toEqual([3241, 2736.5, 2332])
    expect(new Set(built.map(r => `${r.transaction_date}|${r.amount}`)).size).toBe(1)
  })

  it('每行携带文件行序与互不相同的交易流水号', () => {
    const references = Array.from({ length: 13 }, (_, i) => String(178234400000 + i * 2))
    const fileRows = references.map((ref, i) => bocRow({
      '交易流水号[ Transaction reference number ]': ref,
      '交易后余额[ After-transaction balance ]': String(3000 - i),
    }))
    const { rows } = buildBankRows(fileRows, detectBankColumnMapping(BOC_HEADERS))
    expect(rows.map(r => r.transaction_reference)).toEqual(references)
    expect(rows.map(r => r.source_row_index)).toEqual([...Array(13).keys()])
    expect(new Set(rows.map(r => r.transaction_reference)).size).toBe(13)
  })

  it('用户把时间列设为忽略时仍自动兜底取时间，不因映射丢信息', () => {
    const { rows } = buildBankRows([bocRow()], {
      '交易日期[ Transaction Date ]': 'transaction_date',
      '交易时间[ Transaction time ]': '',
      '交易金额[ Trade Amount ]': 'amount',
      '交易后余额[ After-transaction balance ]': 'balance',
    })
    expect(rows[0].transaction_date).toBe('2026-05-20 21:46:46')
  })

  it('时间列值不是时间时不硬拼，保持纯日期', () => {
    const { rows } = buildBankRows(
      [bocRow({ '交易时间[ Transaction time ]': '' })],
      detectBankColumnMapping(BOC_HEADERS),
    )
    expect(rows[0].transaction_date).toBe('2026-05-20')
  })

  it('日期列本身带时分秒时不再二次拼接（杭州银行单列形态）', () => {
    const { rows } = buildBankRows(
      [{ '交易时间': '2026-08-08 05:50:27', '支出金额（元）': '0.00', '收入金额（元）': '5.95' }],
      detectBankColumnMapping(HZ_BANK_HEADERS),
    )
    expect(rows[0].transaction_date).toBe('2026-08-08 05:50:27')
  })

  it('打印/导出时间列是报表元数据，不当作交易时间盖到每笔上', () => {
    const headers = ['交易日期', '交易金额', '账户余额', '打印时间']
    const map = detectBankColumnMapping(headers)
    expect(map['打印时间']).toBe('')
    const { rows } = buildBankRows(
      [{ '交易日期': '20260520', '交易金额': '-4.50', '账户余额': '220.50', '打印时间': '2026-09-01 08:30:00' }],
      map,
    )
    expect(rows[0].transaction_date).toBe('2026-05-20')
  })
})

