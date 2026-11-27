/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

export type OpeningAccountScope = 'common' | 'all'
export type OpeningCategoryFilter = 'all' | 'asset' | 'liability' | 'equity'

/** 小微企业高频资产负债期初科目白名单（默认聚焦展示，降低 110+ 行视觉噪音） */
export const COMMON_OPENING_BALANCE_CODES = new Set<string>([
  '1001',    // 库存现金
  '1002',    // 银行存款
  '1012001', // 微信支付待结算
  '1012002', // 支付宝待结算
  '1122',    // 应收账款
  '1123',    // 预付账款
  '122102',  // 其他应收款-股东
  '122103',  // 其他应收款-员工往来
  '122199',  // 其他应收款-待收款项
  '1405',    // 库存商品
  '1601',    // 固定资产
  '1602',    // 累计折旧
  '2001',    // 短期借款
  '2202',    // 应付账款
  '2203',    // 预收账款
  '221101',  // 应付职工薪酬-工资
  '221102',  // 应付职工薪酬-单位社保
  '221105',  // 应付职工薪酬-住房公积金
  '2221026', // 应交增值税（小规模纳税人专用）
  '2221006', // 应交所得税
  '2221008', // 应交城市维护建设税
  '2221012', // 应交个人所得税
  '2221013', // 教育费附加
  '2221014', // 地方教育费附加
  '2221017', // 印花税
  '2241',    // 其他应付款
  '3001',    // 实收资本
  '3002',    // 资本公积
  '3103',    // 本年利润
  '3104006', // 未分配利润
])

const GENERAL_TAXPAYER_VAT_PREFIXES = ['2221001']
const GENERAL_TAXPAYER_VAT_CODES = new Set<string>([
  '2221018', // 预交增值税
  '2221019', // 待抵扣进项税额
  '2221020', // 待认证进项税额
  '2221021', // 待转销项税额
  '2221022', // 增值税留抵税额
  '2221023', // 简易计税
  '2221024', // 转让金融商品应交增值税
  '2221025', // 代扣代交增值税
])

/** 一般纳税人专属增值税二级/三级明细科目（小规模纳税人默认隐藏，仍可在「全部科目」或导入映射中使用） */
export function isGeneralTaxpayerVatCode(code: string): boolean {
  const c = String(code || '').trim()
  if (!c) return false
  if (GENERAL_TAXPAYER_VAT_PREFIXES.some((prefix) => c.startsWith(prefix))) return true
  return GENERAL_TAXPAYER_VAT_CODES.has(c)
}

export interface OpeningRowFilterable {
  account_code: string
  account_name?: string
  partner_name?: string
  debit_balance?: number
  credit_balance?: number
}

export interface OpeningRowFilterOptions {
  scope: OpeningAccountScope
  category?: OpeningCategoryFilter
  onlyFilled?: boolean
  query?: string
  smallScaleTaxpayer?: boolean
  categoryResolver?: (code: string) => OpeningCategoryFilter
}

function defaultCategory(code: string): OpeningCategoryFilter {
  if (code.startsWith('1') || code.startsWith('4')) return 'asset'
  if (code.startsWith('2')) return 'liability'
  if (code.startsWith('3')) return 'equity'
  return 'asset'
}

/**
 * 决定期初资产负债行是否展示：
 * 1. 类目与关键词过滤始终生效；
 * 2. 已填余额或已填客商的行永远保留（避免切换「常用科目」后隐藏已有数字）；
 * 3. 搜索状态下全量检索，不受「常用科目」白名单限制；
 * 4. 其余空行在「常用科目」模式下只显示小微高频科目，并隐藏一般纳税人专属增值税科目。
 */
export function shouldShowOpeningBalanceRow(
  item: OpeningRowFilterable,
  opts: OpeningRowFilterOptions,
): boolean {
  const code = String(item.account_code || '').trim()
  const hasValue = (Number(item.debit_balance) || 0) > 0 || (Number(item.credit_balance) || 0) > 0
  const hasPartner = Boolean(String(item.partner_name || '').trim())

  if (opts.onlyFilled && !hasValue) return false

  const cat = (opts.categoryResolver ?? defaultCategory)(code)
  if (opts.category && opts.category !== 'all' && cat !== opts.category) {
    return false
  }

  const q = String(opts.query || '').trim().toLowerCase()
  if (q) {
    const codeMatch = code.toLowerCase().includes(q)
    const nameMatch = String(item.account_name || '').toLowerCase().includes(q)
    const partnerMatch = String(item.partner_name || '').toLowerCase().includes(q)
    return codeMatch || nameMatch || partnerMatch
  }

  if (hasValue || hasPartner) return true

  if (opts.scope === 'common') {
    if (opts.smallScaleTaxpayer !== false && isGeneralTaxpayerVatCode(code)) {
      return false
    }
    return COMMON_OPENING_BALANCE_CODES.has(code)
  }

  return true
}

export type AgencyRequestScene = 'check' | 'handover'

/** 生成一键发给原代账的要表话术（先礼后兵，避免引发抵触） */
export function buildAgencyRequestCopy(
  expectedPeriodLabel: string,
  companyName?: string,
  scene: AgencyRequestScene = 'check',
): string {
  const namePart = companyName?.trim() || '我司'
  const periodPart = expectedPeriodLabel?.trim() || '交接截止月'
  if (scene === 'handover') {
    return `老师你好，非常感谢这段时间的照顾。最近家里亲戚（本身是会计）准备顺手帮我接手公司后面的记账报税，麻烦帮忙安排交接一下，把${namePart} ${periodPart} 的 Excel 版科目余额表（带明细科目的）以及电子税务局登录信息等旧账资料发我一份，辛苦啦！`
  }
  return `老师你好，麻烦帮忙导一份${namePart} ${periodPart} 的科目余额表（Excel 版，带明细科目的），我这边内部核对一下今年的账目和往来，辛苦啦！`
}
