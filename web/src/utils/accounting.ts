/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 小企业会计准则（简化版）科目库与映射逻辑
 * 与后端 services/account_mapper.py 保持一致
 */
import { formatMoney } from './format'
import apiClient from '@/api/client'

export interface VoucherLine {
  code: string
  name: string
  side: 'debit' | 'credit'
  amount: number
  // 数量金额式（P1 WP-D）：只有勾了数量核算的科目行才有
  quantity?: number
  unitPrice?: number
  unit?: string
  rdDetail?: {
    projectId: string
    expenseType: string
    originalAmount: number
    rdAmount?: number
    allocationRate?: string
    allocationMethod: string
    allocationBasis?: string
    notes?: string
  }
}

export const CHART_OF_ACCOUNTS: Record<string, string> = {
  '1001': '库存现金',
  '1002': '银行存款',
  '1012': '其他货币资金',
  '1012001': '微信支付待结算',
  '1122': '应收账款',
  '1123': '预付账款',
  '1221': '其他应收款',
  '122101': '其他应收款-代扣代缴社保',
  '122102': '其他应收款-股东',
  '122103': '其他应收款-员工往来',
  '1403': '原材料',
  '1405': '库存商品',
  '1601': '固定资产',
  '1602': '累计折旧',
  '2202': '应付账款',
  '2203': '预收账款',
  '2221': '应交税费',
  '2211': '应付职工薪酬',
  '221101': '应付职工薪酬-工资',
  '221102': '应付职工薪酬-单位社保',
  '221103': '应付职工薪酬-职工福利费',
  '221104': '应付职工薪酬-代扣社保公积金',
  '2221012': '应交个人所得税',
  '2221026': '应交增值税（小规模纳税人专用）',
  '2241': '其他应付款',
  '3001': '实收资本',
  '3002': '资本公积',
  '3101': '盈余公积',
  '3103': '本年利润',
  '3104': '利润分配',
  '5001': '主营业务收入',
  '5051': '其他业务收入',
  '5111': '投资收益',
  '5301': '营业外收入',
  '5401001': '主营业务成本-人力',
  '5401002': '主营业务成本-生产',
  '5403': '税金及附加',
  '4301': '研发支出-费用化支出',
  '5601': '销售费用',
  '5601011': '销售费用-运输费',
  '5601016': '销售费用-业务宣传费',
  '5602': '管理费用',
  '5602001': '管理人员职工薪酬',
  '5602002': '管理费用-业务招待费',
  '5602004': '管理费用-办公费',
  '5602005': '管理费用-水电费',
  '5602006': '管理费用-差旅费',
  '5602007': '管理费用-折旧费',
  '5602013': '管理费用-租赁费',
  '5602014': '管理费用-职工福利费',
  '5602015': '管理费用-信息技术服务费',
  '5602016': '管理费用-单位社保',
  '5602999': '管理费用-其他',
  '5603': '财务费用',
  '5603002': '财务费用-手续费',
  '5603004': '现金折扣',
  '5603005': '财务费用-利息收入',
  '5711': '营业外支出',
  '5801': '所得税费用',
}

const CATEGORY_DEBIT_MAP: Record<string, { code: string; name: string }> = {
  '交通': { code: '5602006', name: '管理费用-差旅费' },
  '差旅': { code: '5602006', name: '管理费用-差旅费' },
  '研发支出-费用化支出': { code: '4301', name: '研发支出-费用化支出' },
  '原材料采购': { code: '1403', name: '原材料' },
  '商品采购': { code: '1405', name: '库存商品' },
  '生产成本': { code: '5401002', name: '主营业务成本-生产' },
  '差旅费': { code: '5602006', name: '管理费用-差旅费' },
  '办公': { code: '5602004', name: '管理费用-办公费' },
  '办公费': { code: '5602004', name: '管理费用-办公费' },
  '餐饮': { code: '5602002', name: '管理费用-业务招待费' },
  '业务招待': { code: '5602002', name: '管理费用-业务招待费' },
  '业务招待费': { code: '5602002', name: '管理费用-业务招待费' },
  '固定资产': { code: '1601', name: '固定资产' },
  '设备采购': { code: '1601', name: '固定资产' },
  '无形资产': { code: '1701', name: '无形资产' },
  '人力成本': { code: '5401001', name: '主营业务成本-人力' },
  '员工福利': { code: '5602014', name: '管理费用-职工福利费' },
  '员工福利费': { code: '5602014', name: '管理费用-职工福利费' },
  '房租物业': { code: '5602013', name: '管理费用-租赁费' },
  '租金物业': { code: '5602013', name: '管理费用-租赁费' },
  '信息技术服务费': { code: '5602015', name: '管理费用-信息技术服务费' },
  '运费及物流': { code: '5601011', name: '销售费用-运输费' },
  '水电通讯费': { code: '5602005', name: '管理费用-水电费' },
  '银行及金融手续费': { code: '5603002', name: '财务费用-手续费' },
  '税费缴纳': { code: '2221026', name: '应交增值税（小规模纳税人专用）' },
  '预付账款': { code: '1123', name: '预付账款' },
  '推广费': { code: '5601016', name: '销售费用-业务宣传费' },
  '营业外支出': { code: '5711', name: '营业外支出' },
  '所得税费用': { code: '5801', name: '所得税费用' },
  '其他支出': { code: '5602999', name: '管理费用-其他' },
  // 工资/社保走工资模块核销（与后端 account_mapper 221101/221102 对齐），不直接记费用
  '工资发放': { code: '221101', name: '应付职工薪酬-工资' },
  '社保公积金': { code: '221102', name: '应付职工薪酬-单位社保' },
  '供应商付款': { code: '2202', name: '应付账款' },
  '员工垫付报销': { code: '2241', name: '其他应付款-员工' },
  '主营业务收入': { code: '5001', name: '主营业务收入' },
  '其他业务收入': { code: '5051', name: '其他业务收入' },
  '营业外收入': { code: '5301', name: '营业外收入' },
  '利息收入': { code: '5603005', name: '财务费用-利息收入' },
  '折旧': { code: '5602007', name: '管理费用-折旧费' },
  '股东投入': { code: '3001', name: '实收资本' },
  '资本公积': { code: '3002', name: '资本公积' },
  '股东垫付': { code: '2241', name: '其他应付款-股东' },
  // 1221 已容器化，往来类一律落末级明细（与后端 account_mapper 对齐）
  '股东取现': { code: '122102', name: '其他应收款-股东' },
  '老板投入': { code: '3001', name: '实收资本' },
  '老板垫付': { code: '2241', name: '其他应付款-股东' },
  '老板取现': { code: '122102', name: '其他应收款-股东' },
  '其他应收款': { code: '122103', name: '其他应收款-员工往来' },
  '其他应付款': { code: '2241', name: '其他应付款-其他' },
  '往来款': { code: '122199', name: '其他应收款-待收款项' },
}

// 权威映射（分类 → 科目）：从后端 /api/categories/account-mapping 加载，覆盖上面的兜底表。
// 未加载或加载失败时回落到 CATEGORY_DEBIT_MAP，保证凭证预览不因网络问题中断。
let _accountMapping: Record<string, { code: string; name: string }> | null = null

// 分类模糊组（随 /account-mapping 同源下发）：银行流水层面不可判定、正确科目
// 取决于账外事实的分类。组内候选需展开交用户选择，且禁止沉淀自动入账规则。
let _ambiguityGroups: Record<string, string[]> = {}

export function getAmbiguityGroup(category: string): string[] {
  return _ambiguityGroups[category] || []
}

export async function loadAccountMapping(): Promise<void> {
  try {
    const res: any = await apiClient.get('/api/categories/account-mapping')
    const items = res?.items || res || []
    const map: Record<string, { code: string; name: string }> = {}
    for (const it of items) {
      if (it?.category && it?.accountCode) {
        map[it.category] = { code: it.accountCode, name: it.accountName || it.category }
      }
    }
    if (Object.keys(map).length > 0) {
      _accountMapping = map
    }
    if (res?.ambiguityGroups && typeof res.ambiguityGroups === 'object') {
      _ambiguityGroups = res.ambiguityGroups
    }
  } catch {
    // 保留兜底表
  }
}

export function accountForCategory(category: string): { code: string; name: string } | undefined {
  return _accountMapping?.[category] || CATEGORY_DEBIT_MAP[category]
}

export function normalizeVoucherLines(voucherLines: any): VoucherLine[] {
  if (!voucherLines) return []
  const lines: VoucherLine[] = []
  for (const line of voucherLines.debit || []) {
    lines.push({
      code: line.accountCode || line.code,
      name: line.accountName || line.name,
      side: 'debit',
      amount: Number(line.amount) || 0,
      quantity: line.quantity != null && line.quantity !== '' ? Number(line.quantity) : undefined,
      unitPrice: line.unitPrice != null && line.unitPrice !== '' ? Number(line.unitPrice) : undefined,
      unit: line.unit || undefined,
      rdDetail: line.rdDetail ? {
        ...line.rdDetail,
        originalAmount: Number(line.rdDetail.originalAmount) || 0,
        rdAmount: Number(line.rdDetail.rdAmount) || undefined,
      } : undefined,
    })
  }
  for (const line of voucherLines.credit || []) {
    lines.push({
      code: line.accountCode || line.code,
      name: line.accountName || line.name,
      side: 'credit',
      amount: Number(line.amount) || 0,
      quantity: line.quantity != null && line.quantity !== '' ? Number(line.quantity) : undefined,
      unitPrice: line.unitPrice != null && line.unitPrice !== '' ? Number(line.unitPrice) : undefined,
      unit: line.unit || undefined,
      rdDetail: line.rdDetail ? {
        ...line.rdDetail,
        originalAmount: Number(line.rdDetail.originalAmount) || 0,
        rdAmount: Number(line.rdDetail.rdAmount) || undefined,
      } : undefined,
    })
  }
  return lines
}


function normalizedOwnerCategory(category: string): string {
  const aliasMap: Record<string, string> = {
    '老板投入': '股东投入',
    '老板垫付': '股东垫付',
    '老板取现': '股东取现',
  }
  return aliasMap[category] || category
}

export function amountSignForDirection(direction: string, category = '', amount?: number): '+' | '-' | '' {
  // 金额已为负值时，formatMoney 自带 '-' 前缀，不再叠加方向符号
  if (amount !== undefined && amount < 0) return ''
  if (direction === 'income') return '+'
  if (direction === 'expense') return '-'
  if (direction === 'owner_transfer') {
    const normalizedCategory = normalizedOwnerCategory(category)
    return ['股东取现', '股东垫付报销', '员工垫付报销'].includes(normalizedCategory) ? '-' : '+'
  }
  return ''
}

export type MoneyTone = 'income' | 'expense' | 'neutral'

/**
 * 条目金额的展示口径（符号 + 色调按资金实际流向）：
 * 冲退类负金额（如手续费退还 借银行/贷费用、退款给客户）账面方向不变，
 * 但资金流向相反—— expense 负显示 +¥x 绿色，income 负显示 -¥x 红色。
 */
export function moneyPresentation(direction: string, category: string, amount: number | string): { text: string; tone: MoneyTone } {
  const value = Number(amount) || 0
  const abs = formatMoney(Math.abs(value))
  if (value < 0) {
    if (direction === 'expense') return { text: `+${abs}`, tone: 'income' }
    if (direction === 'income') return { text: `-${abs}`, tone: 'expense' }
    return { text: `-${abs}`, tone: 'neutral' }
  }
  const tone: MoneyTone = direction === 'income' ? 'income' : direction === 'expense' ? 'expense' : 'neutral'
  return { text: `${amountSignForDirection(direction, category, value)}${abs}`, tone }
}

export interface EntryForVoucher {
  type: 'income' | 'expense' | 'owner_transfer'
  amount: number
  category: string
  payer?: string
}

export function generateVoucherLines(entry: EntryForVoucher): VoucherLine[] {
  const lines: VoucherLine[] = []
  const val = Number(entry.amount) || 0

  let category = entry.category
  if (category === '往来款') {
    category = entry.type === 'expense' ? '其他应收款' : '其他应付款'
  }
  if (entry.type === 'expense' && ['股东垫付报销', '员工垫付报销'].includes(category)) {
    category = '其他支出'
  }

  if (entry.type === 'income') {
    lines.push({ code: '1002', name: '银行存款', side: 'debit', amount: val })
    const acct = accountForCategory(category) || { code: '5001', name: '主营业务收入' }
    lines.push({ code: acct.code, name: acct.name, side: 'credit', amount: val })
  } else if (entry.type === 'expense') {
    const acct = accountForCategory(category) || { code: '5602999', name: '管理费用-其他' }
    lines.push({ code: acct.code, name: acct.name, side: 'debit', amount: val })
    if (category === '折旧') {
      lines.push({ code: '1602', name: '累计折旧', side: 'credit', amount: val })
    } else if (entry.payer === 'boss_personal') {
      lines.push({ code: '2241', name: '其他应付款-股东', side: 'credit', amount: val })
    } else if (entry.payer === 'employee_personal' || entry.payer?.startsWith('employee_personal:')) {
      const employeeName = entry.payer.includes(':') ? entry.payer.split(':').slice(1).join(':').trim() : ''
      lines.push({
        code: '2241',
        name: employeeName ? `其他应付款-员工往来(${employeeName})` : '其他应付款-员工往来',
        side: 'credit',
        amount: val,
      })
    } else {
      lines.push({ code: '1002', name: '银行存款', side: 'credit', amount: val })
    }
  } else if (entry.type === 'owner_transfer') {
    const normCategory = normalizedOwnerCategory(category)
    if (normCategory === '股东取现') {
      lines.push({ code: '122102', name: '其他应收款-股东', side: 'debit', amount: val })
      lines.push({ code: '1002', name: '银行存款', side: 'credit', amount: val })
    } else {
      const acct = accountForCategory(normCategory) || { code: '2241', name: '其他应付款-股东' }
      lines.push({ code: '1002', name: '银行存款', side: 'debit', amount: val })
      lines.push({ code: acct.code, name: acct.name, side: 'credit', amount: val })
    }
  }

  return lines
}

export function formatVoucherRef(dateStr: string, seq: number): string {
  const date = new Date(dateStr)
  const y = date.getFullYear()
  const m = String(date.getMonth() + 1).padStart(2, '0')
  return `记-${y}${m}-${String(seq).padStart(3, '0')}`
}

export function getAccountByCategory(category: string, direction?: string): { code: string; name: string } {
  let cat = category
  if (cat === '往来款') {
    cat = direction === 'income' ? '其他应付款' : '其他应收款'
  }
  return accountForCategory(cat) || { code: '5602999', name: '管理费用-其他' }
}

/** 科目余额方向：负债(2)/权益(3)与收入(5001/5051/5301)贷方增加，资产/成本/费用借方增加 */
export function isCreditIncreasing(accountCode: string): boolean {
  return /^[23]/.test(accountCode) || ['5001', '5051', '5301'].includes(accountCode)
}

/** 由账页首行分录倒推期初余额。账页余额符号统一为「借余为正、贷余为负」
 *  （与科目汇总/电子账簿同口径），勾稽关系与科目方向无关：期初 = 首行余额 − 借 + 贷。
 *  传入其他符号约定的余额会算错（贷方科目会偏差 2×|首行净额|）。 */
export function deriveOpeningBalance(first: { debit: number; credit: number; balance: number }): number {
  return first.balance - first.debit + first.credit
}
