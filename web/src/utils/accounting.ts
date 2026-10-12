/**
 * 小企业会计准则（简化版）科目库与映射逻辑
 * 与后端 services/account_mapper.py 保持一致
 */
import { formatMoney } from './format'

export interface VoucherLine {
  code: string
  name: string
  side: 'debit' | 'credit'
  amount: number
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
  '1012': '微信支付待结算',
  '1122': '应收账款',
  '1123': '预付账款',
  '1221': '其他应收款',
  '1403': '原材料',
  '1405': '库存商品',
  '1601': '固定资产',
  '1602': '累计折旧',
  '2202': '应付账款',
  '2221': '应交税费',
  '2211': '应付职工薪酬',
  '2241': '其他应付款',
  '3001': '实收资本',
  '4001': '实收资本',
  '4002': '资本公积-股本溢价',
  '3103': '本年利润',
  '3104': '利润分配-未分配利润',
  '6001': '主营业务收入',
  '6051': '其他业务收入',
  '6301': '营业外收入',
  '5001': '主营业务成本-人力',
  '5101': '主营业务成本-生产',
  '5301': '研发支出-费用化支出',
  '5600': '管理费用-折旧费',
  '5601': '管理费用-差旅费',
  '5602': '管理费用-办公费',
  '5603': '管理费用-业务招待费',
  '5604': '管理费用-租赁物业',
  '5605': '管理费用-信息技术服务费',
  '5606': '管理费用-运费及物流',
  '5607': '管理费用-水电通讯费',
  '5608': '管理费用-银行及金融手续费',
  '5609': '管理费用-职工福利费',
  '5699': '管理费用-其他',
  '6601': '销售费用-推广费',
  '6603': '财务费用-利息收入',
  '5711': '营业外支出',
  '5801': '所得税费用',
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
      rdDetail: line.rdDetail ? {
        ...line.rdDetail,
        originalAmount: Number(line.rdDetail.originalAmount) || 0,
        rdAmount: Number(line.rdDetail.rdAmount) || undefined,
      } : undefined,
    })
  }
  return lines
}

const CATEGORY_DEBIT_MAP: Record<string, { code: string; name: string }> = {
  '交通': { code: '5601', name: '管理费用-差旅费' },
  '差旅': { code: '5601', name: '管理费用-差旅费' },
  '研发支出-费用化支出': { code: '5301', name: '研发支出-费用化支出' },
  '原材料采购': { code: '1403', name: '原材料' },
  '商品采购': { code: '1405', name: '库存商品' },
  '生产成本': { code: '5101', name: '主营业务成本-生产' },
  '差旅费': { code: '5601', name: '管理费用-差旅费' },
  '办公': { code: '5602', name: '管理费用-办公费' },
  '办公费': { code: '5602', name: '管理费用-办公费' },
  '餐饮': { code: '5603', name: '管理费用-业务招待费' },
  '业务招待': { code: '5603', name: '管理费用-业务招待费' },
  '业务招待费': { code: '5603', name: '管理费用-业务招待费' },
  '固定资产': { code: '1601', name: '固定资产' },
  '设备采购': { code: '1601', name: '固定资产' },
  '人力成本': { code: '5001', name: '主营业务成本-人力' },
  '员工福利': { code: '5609', name: '管理费用-职工福利费' },
  '员工福利费': { code: '5609', name: '管理费用-职工福利费' },
  '房租物业': { code: '5604', name: '管理费用-租赁物业' },
  '租金物业': { code: '5604', name: '管理费用-租赁物业' },
  '信息技术服务费': { code: '5605', name: '管理费用-信息技术服务费' },
  '运费及物流': { code: '5606', name: '管理费用-运费及物流' },
  '水电通讯费': { code: '5607', name: '管理费用-水电通讯费' },
  '银行及金融手续费': { code: '5608', name: '管理费用-银行及金融手续费' },
  '税费缴纳': { code: '2221', name: '应交税费' },
  '预付账款': { code: '1123', name: '预付账款' },
  '推广费': { code: '6601', name: '销售费用-推广费' },
  '其他支出': { code: '5699', name: '管理费用-其他' },
  '主营业务收入': { code: '6001', name: '主营业务收入' },
  '其他业务收入': { code: '6051', name: '其他业务收入' },
  '营业外收入': { code: '6301', name: '营业外收入' },
  '利息收入': { code: '6603', name: '财务费用-利息收入' },
  '折旧': { code: '5600', name: '管理费用-折旧费' },
  '股东投入': { code: '4001', name: '实收资本' },
  '资本公积': { code: '4002', name: '资本公积-股本溢价' },
  '股东垫付': { code: '2241', name: '其他应付款-股东' },
  '股东取现': { code: '1221', name: '其他应收款-股东' },
  '老板投入': { code: '4001', name: '实收资本' },
  '老板垫付': { code: '2241', name: '其他应付款-股东' },
  '老板取现': { code: '1221', name: '其他应收款-股东' },
  '其他应收款': { code: '1221', name: '其他应收款-其他' },
  '其他应付款': { code: '2241', name: '其他应付款-其他' },
  '往来款': { code: '1221', name: '其他应收款-其他' },
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
    const acct = CATEGORY_DEBIT_MAP[category] || { code: '6001', name: '主营业务收入' }
    lines.push({ code: acct.code, name: acct.name, side: 'credit', amount: val })
  } else if (entry.type === 'expense') {
    const acct = CATEGORY_DEBIT_MAP[category] || { code: '5699', name: '管理费用-其他' }
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
      lines.push({ code: '1221', name: '其他应收款-股东', side: 'debit', amount: val })
      lines.push({ code: '1002', name: '银行存款', side: 'credit', amount: val })
    } else {
      const acct = CATEGORY_DEBIT_MAP[normCategory] || { code: '2241', name: '其他应付款-股东' }
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
  return CATEGORY_DEBIT_MAP[cat] || { code: '5699', name: '管理费用-其他' }
}
