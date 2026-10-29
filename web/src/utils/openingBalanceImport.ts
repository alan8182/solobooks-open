/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 期初科目余额导入与对齐工具
 * ponytail: 纯前端无依赖解析函数，基于 SheetJS，支持科目别名、客商核算及年中损益累计识别
 */
import * as XLSX from 'xlsx'
import { saveWorkbook } from '@/utils/export'

export interface MatchedOpeningItem {
  account_code: string
  account_name: string
  debit_balance: number
  credit_balance: number
  partner_name?: string
  matched: boolean
  is_expense_revenue?: boolean
  originalCode?: string
  originalName?: string
  warning?: string
}

export interface FixedAssetImportItem {
  name: string
  category?: string
  purchase_date?: string
  cost: number
  accumulated_depreciation: number
  useful_months?: number
  salvage_rate?: number
}

export interface UnmatchedOpeningRow {
  originalCode: string
  originalName: string
  debit: number
  credit: number
}

export interface ParseOpeningResult {
  items: MatchedOpeningItem[]
  ytdItems: MatchedOpeningItem[]
  fixedAssets: FixedAssetImportItem[]
  totalDebit: number
  totalCredit: number
  difference: number
  isBalanced: boolean
  matchedCount: number
  unmatchedCount: number
  warnings: string[]
  unmatchedRows?: UnmatchedOpeningRow[]
  rawRows?: Record<string, unknown>[]
}

const EXPENSE_ASSET_CODES = new Set([
  '5001', '5051', '5111', '5301', '5401', '5401001', '5401002', '5402', '5403',
  '5601', '5601001', '5601006', '5601011', '5601015', '5601016',
  '5602', '5602001', '5602002', '5602004', '5602005', '5602006', '5602007',
  '5602011', '5602013', '5602014', '5602015', '5602999',
  '5603', '5603001', '5603002', '5603003', '5603004',
  '5711', '5801', '4301',
])

const CODE_ALIASES = ['科目编码', '科目代码', '科目编号', '代码', '编码', 'account_code', 'code']
const NAME_ALIASES = ['科目名称', '科目', '名称', 'account_name', 'name']
const DEBIT_ALIASES = ['期初借方', '借方期初', '期初借方余额', '借方余额', '期末借方', '借方', 'debit', 'debit_balance']
const CREDIT_ALIASES = ['期初贷方', '贷方期初', '期初贷方余额', '贷方余额', '期末贷方', '贷方', 'credit', 'credit_balance']
const YTD_DEBIT_ALIASES = ['本年累计借方', '借方累计', '本年借方累计', '累计借方', '借方发生额', '本期借方']
const YTD_CREDIT_ALIASES = ['本年累计贷方', '贷方累计', '本年贷方累计', '累计贷方', '贷方发生额', '本期贷方']
const DIRECTION_ALIASES = ['方向', '借贷方向', '借/贷', 'direction']
const BALANCE_ALIASES = ['期初余额', '年初余额', '余额', '金额', 'balance', 'amount']
const PARTNER_ALIASES = ['客商', '客商名称', '往来单位', '往来客户', '客户', '供应商', '核算项目', '辅助核算', 'partner', 'partner_name']

function findKey(keys: string[], aliases: string[]): string | undefined {
  const normalized = keys.map(k => ({ raw: k, clean: k.trim().toLowerCase() }))
  for (const alias of aliases) {
    const target = alias.toLowerCase()
    const found = normalized.find(n => n.clean === target || n.clean.includes(target))
    if (found) return found.raw
  }
  return undefined
}

function parseNumber(val: unknown): number {
  if (val == null) return 0
  if (typeof val === 'number') return isNaN(val) ? 0 : val
  const str = String(val).replace(/[,¥\s]/g, '')
  const n = parseFloat(str)
  return isNaN(n) ? 0 : n
}

/**
 * 解析从 Excel 转换而来的行数据，匹配到系统的科目体系中
 */
export function parseOpeningRows(
  rows: Record<string, unknown>[],
  allAccounts: { account_code: string; account_name?: string }[],
  mapping?: Record<string, string>,
): ParseOpeningResult {
  if (!rows || rows.length === 0) {
    return {
      items: [],
      ytdItems: [],
      fixedAssets: [],
      totalDebit: 0,
      totalCredit: 0,
      difference: 0,
      isBalanced: false,
      matchedCount: 0,
      unmatchedCount: 0,
      warnings: ['文件为空或无有效数据行'],
    }
  }

  // 聚合所有行中的键名，防止首行键稀疏
  const allKeys = Array.from(new Set(rows.flatMap(r => Object.keys(r))))

  const codeKey = findKey(allKeys, CODE_ALIASES)
  const nameKey = findKey(allKeys, NAME_ALIASES)
  const debitKey = findKey(allKeys, DEBIT_ALIASES)
  const creditKey = findKey(allKeys, CREDIT_ALIASES)
  const ytdDebitKey = findKey(allKeys, YTD_DEBIT_ALIASES)
  const ytdCreditKey = findKey(allKeys, YTD_CREDIT_ALIASES)
  const directionKey = findKey(allKeys, DIRECTION_ALIASES)
  const balanceKey = findKey(allKeys, BALANCE_ALIASES)
  const partnerKey = findKey(allKeys, PARTNER_ALIASES)

  const warnings: string[] = []
  if (!codeKey && !nameKey) {
    warnings.push('未能识别科目代码或科目名称列')
    return {
      items: [],
      ytdItems: [],
      fixedAssets: [],
      totalDebit: 0,
      totalCredit: 0,
      difference: 0,
      isBalanced: false,
      matchedCount: 0,
      unmatchedCount: rows.length,
      warnings,
    }
  }

  // 建立现有科目的快速索引
  const codeMap = new Map<string, { account_code: string; account_name: string }>()
  const nameMap = new Map<string, { account_code: string; account_name: string }>()
  for (const acc of allAccounts) {
    const code = String(acc.account_code).trim()
    const name = String(acc.account_name || '').trim()
    const item = { account_code: code, account_name: name }
    codeMap.set(code, item)
    if (name) nameMap.set(name, item)
  }

  const resultMap = new Map<string, MatchedOpeningItem>()
  for (const acc of allAccounts) {
    resultMap.set(acc.account_code, {
      account_code: acc.account_code,
      account_name: acc.account_name || acc.account_code,
      debit_balance: 0,
      credit_balance: 0,
      partner_name: '',
      matched: false,
      is_expense_revenue: EXPENSE_ASSET_CODES.has(acc.account_code),
    })
  }

  let matchedCount = 0
  let unmatchedCount = 0
  const unmatchedRows: UnmatchedOpeningRow[] = []

  for (const row of rows) {
    const rawCode = codeKey ? String(row[codeKey] ?? '').trim() : ''
    const rawName = nameKey ? String(row[nameKey] ?? '').trim() : ''
    let rawPartner = partnerKey ? String(row[partnerKey] ?? '').trim() : ''
    if (!rawPartner) {
      for (const k of Object.keys(row)) {
        if (PARTNER_ALIASES.some(alias => k.includes(alias))) {
          const v = String(row[k] ?? '').trim()
          if (v) {
            rawPartner = v
            break
          }
        }
      }
    }

    // 过滤掉总计行/空行
    if (rawCode.includes('合计') || rawName.includes('合计') || rawName.includes('总计')) {
      continue
    }
    if (!rawCode && !rawName) continue

    // 计算借贷数值：优先取借贷分列，若有本年累计借贷则作为备选
    let debit = debitKey ? parseNumber(row[debitKey]) : 0
    let credit = creditKey ? parseNumber(row[creditKey]) : 0

    // 单列金额 + 方向模式
    if (balanceKey && directionKey && debit === 0 && credit === 0) {
      const dir = String(row[directionKey] ?? '').trim()
      const bal = parseNumber(row[balanceKey])
      if (dir.includes('借')) {
        debit = bal
      } else if (dir.includes('贷')) {
        credit = bal
      }
    }

    // 若科目属于损益类，且余额为空，则尝试从“本年累计借贷/发生额”列提取
    const isLikelyExpenseRevenue = rawCode.startsWith('5') || rawCode.startsWith('4301')
    if (isLikelyExpenseRevenue && debit === 0 && credit === 0) {
      if (ytdDebitKey) debit = parseNumber(row[ytdDebitKey])
      if (ytdCreditKey) credit = parseNumber(row[ytdCreditKey])
    }

    // 冲抵同一科目同时填写借贷的情况
    if (debit > 0 && credit > 0 && !isLikelyExpenseRevenue) {
      if (debit >= credit) {
        debit = Math.round((debit - credit) * 100) / 100
        credit = 0
      } else {
        credit = Math.round((credit - debit) * 100) / 100
        debit = 0
      }
    }

    if (debit === 0 && credit === 0) {
      continue
    }

    // 匹配系统科目（映射优先 -> 精准 -> 清洗 -> 前缀 -> 中文名称）
    const mappedCode = mapping?.[rawCode] || mapping?.[rawName]
    let target = mappedCode ? codeMap.get(mappedCode) : undefined
    if (!target) {
      const cleanCode = rawCode.replace(/[.\-_]/g, '')
      const prefixCode = rawCode.includes('.') ? rawCode.split('.')[0] : rawCode.includes('-') ? rawCode.split('-')[0] : ''
      target = codeMap.get(rawCode) || codeMap.get(cleanCode)
      if (!target && prefixCode) {
        target = codeMap.get(prefixCode)
      }
      if (!target && rawName) {
        target = nameMap.get(rawName)
      }
    }

    if (target) {
      const existing = resultMap.get(target.account_code)!
      existing.debit_balance = Math.round((existing.debit_balance + debit) * 100) / 100
      existing.credit_balance = Math.round((existing.credit_balance + credit) * 100) / 100
      if (rawPartner && !existing.partner_name) {
        existing.partner_name = rawPartner
      }
      existing.matched = true
      existing.originalCode = rawCode
      existing.originalName = rawName
      matchedCount++
    } else {
      unmatchedCount++
      warnings.push(`未匹配到系统科目：[${rawCode || '-'}] ${rawName || '-'} (借: ${debit}, 贷: ${credit})`)
      unmatchedRows.push({
        originalCode: rawCode,
        originalName: rawName,
        debit,
        credit,
      })
    }
  }

  const allItems = Array.from(resultMap.values())
  const items = allItems.filter(i => !i.is_expense_revenue)
  const ytdItems = allItems.filter(i => i.is_expense_revenue)

  // 仅资产负债科目核算期初借贷平衡
  const totalDebit = Math.round(items.reduce((s, i) => s + (i.debit_balance || 0), 0) * 100) / 100
  const totalCredit = Math.round(items.reduce((s, i) => s + (i.credit_balance || 0), 0) * 100) / 100
  const difference = Math.round(Math.abs(totalDebit - totalCredit) * 100) / 100
  const isBalanced = difference === 0 && totalDebit > 0

  return {
    items,
    ytdItems,
    fixedAssets: [],
    totalDebit,
    totalCredit,
    difference,
    isBalanced,
    matchedCount,
    unmatchedCount,
    warnings,
    unmatchedRows,
    rawRows: rows,
  }
}

const FA_NAME_ALIASES = ['资产名称', '设备名称', '固定资产名称', '固定资产', '名称', 'name', 'asset_name']
const FA_CATEGORY_ALIASES = ['资产类别', '类别', '分类', '资产分类', 'category']
const FA_DATE_ALIASES = ['购入日期', '购置日期', '购买日期', '入账日期', '日期', 'purchase_date', 'date']
const FA_COST_ALIASES = ['原值', '资产原值', '固定资产原值', '金额', '原价', 'cost', 'original_value']
const FA_DEPR_ALIASES = ['累计折旧', '已提折旧', '期初累计折旧', '折旧', '已提金额', 'accumulated_depreciation']
const FA_MONTHS_ALIASES = ['预计使用月数', '使用月数', '折旧期数', '年限(月)', '月份', 'useful_months', 'months']
const FA_SALVAGE_ALIASES = ['预计净残值率', '残值率', '净残值率', '残值率(%)', 'salvage_rate']

/**
 * 解析固定资产卡片行数据
 */
export function parseFixedAssetRows(rows: Record<string, unknown>[]): FixedAssetImportItem[] {
  if (!rows || rows.length === 0) return []
  const allKeys = Array.from(new Set(rows.flatMap(r => Object.keys(r))))
  const nameKey = findKey(allKeys, FA_NAME_ALIASES)
  const catKey = findKey(allKeys, FA_CATEGORY_ALIASES)
  const dateKey = findKey(allKeys, FA_DATE_ALIASES)
  const costKey = findKey(allKeys, FA_COST_ALIASES)
  const deprKey = findKey(allKeys, FA_DEPR_ALIASES)
  const monthsKey = findKey(allKeys, FA_MONTHS_ALIASES)
  const salvageKey = findKey(allKeys, FA_SALVAGE_ALIASES)

  if (!nameKey && !costKey) return []

  const assets: FixedAssetImportItem[] = []
  for (const row of rows) {
    const name = nameKey ? String(row[nameKey] ?? '').trim() : ''
    if (!name || name.includes('合计') || name.includes('总计')) continue

    const cost = costKey ? parseNumber(row[costKey]) : 0
    if (cost <= 0) continue

    const depr = deprKey ? parseNumber(row[deprKey]) : 0
    const cat = catKey ? String(row[catKey] ?? '').trim() : '电子设备'
    let pDate = dateKey ? String(row[dateKey] ?? '').trim() : ''
    // 兼容 Excel 日期数字或标准字符串
    if (/^\d{4}-\d{2}-\d{2}$/.test(pDate)) {
      // 保持原有格式
    } else if (pDate.includes('/')) {
      pDate = pDate.replace(/\//g, '-')
    }

    let months = monthsKey ? parseInt(String(row[monthsKey] ?? '36'), 10) : 36
    if (isNaN(months) || months <= 0) months = 36

    let salvage = salvageKey ? parseNumber(row[salvageKey]) : 5
    if (salvage > 1) salvage = salvage / 100 // 如用户填 5 视为 5%
    if (salvage < 0 || salvage >= 1) salvage = 0.05

    assets.push({
      name,
      category: cat || '电子设备',
      purchase_date: pDate || undefined,
      cost,
      accumulated_depreciation: depr,
      useful_months: months,
      salvage_rate: salvage,
    })
  }
  return assets
}

/**
 * 解析完整 Excel 工作簿（支持多 Sheet：资产负债、损益累计、固定资产卡片）
 */
export function parseOpeningWorkbook(
  workbook: XLSX.WorkBook,
  allAccounts: { account_code: string; account_name?: string }[],
  mapping?: Record<string, string>,
): ParseOpeningResult {
  const sheetNames = workbook.SheetNames
  if (!sheetNames || sheetNames.length === 0) {
    return parseOpeningRows([], allAccounts, mapping)
  }

  // 1. 定位各个 Sheet
  const balanceSheetName = sheetNames.find(n => n.includes('资产负债') || n.includes('科目余额') || n.includes('期初余额')) || sheetNames[0]
  const ytdSheetName = sheetNames.find(n => n.includes('损益') || n.includes('累计发生'))
  const faSheetName = sheetNames.find(n => n.includes('固定资产') || n.includes('资产卡片') || n.includes('卡片'))

  // 解析资产负债 Sheet
  const balanceRows = XLSX.utils.sheet_to_json(workbook.Sheets[balanceSheetName], { defval: '' }) as Record<string, unknown>[]
  const baseResult = parseOpeningRows(balanceRows, allAccounts, mapping)

  // 如果有独立的损益 Sheet，合并损益科目数据
  if (ytdSheetName && ytdSheetName !== balanceSheetName) {
    const ytdRows = XLSX.utils.sheet_to_json(workbook.Sheets[ytdSheetName], { defval: '' }) as Record<string, unknown>[]
    const ytdParsed = parseOpeningRows(ytdRows, allAccounts, mapping)
    if (ytdParsed.ytdItems && ytdParsed.ytdItems.length > 0) {
      const existingYtdMap = new Map(baseResult.ytdItems.map(y => [y.account_code, y]))
      for (const yi of ytdParsed.ytdItems) {
        if (yi.debit_balance > 0 || yi.credit_balance > 0) {
          existingYtdMap.set(yi.account_code, yi)
        }
      }
      baseResult.ytdItems = Array.from(existingYtdMap.values())
    }
  }

  // 解析固定资产 Sheet
  let fixedAssets: FixedAssetImportItem[] = []
  if (faSheetName) {
    const faRows = XLSX.utils.sheet_to_json(workbook.Sheets[faSheetName], { defval: '' }) as Record<string, unknown>[]
    fixedAssets = parseFixedAssetRows(faRows)
  }
  baseResult.fixedAssets = fixedAssets

  return baseResult
}

/**
 * 导出标准期初科目余额导入模板 Excel
 */
export function exportOpeningTemplate(
  accounts: { account_code: string; account_name?: string }[],
  fiscalYear: number,
  startPeriod?: string,
) {
  const isMidYear = Boolean(startPeriod && !startPeriod.endsWith('-01'))
  const pName = startPeriod || `${fiscalYear}-01`

  const balanceAccounts = accounts.filter(a => !EXPENSE_ASSET_CODES.has(a.account_code))
  const expenseAccounts = accounts.filter(a => EXPENSE_ASSET_CODES.has(a.account_code))

  const balanceData = balanceAccounts.map(a => ({
    '科目代码': a.account_code,
    '科目名称': a.account_name || a.account_code,
    '期初借方余额': '',
    '期初贷方余额': '',
    '往来客商名称(可选)': '',
    '填写指引': '资产类通常在借方，负债/权益类通常在贷方，应收应付可填客商',
  }))

  const ytdData = expenseAccounts.map(a => ({
    '科目代码': a.account_code,
    '科目名称': a.account_name || a.account_code,
    '本年累计借方发生额': '',
    '本年累计贷方发生额': '',
    '填写指引': '成本费用在借方，收入在贷方，记录迁入前序月份累计数',
  }))

  const faData = [
    {
      '资产名称': '办公电脑 (示例)',
      '资产类别': '电子设备',
      '购入日期': `${fiscalYear - 1}-06-15`,
      '原值(元)': 12000,
      '已提累计折旧(元)': 4000,
      '预计使用月数': 36,
      '预计净残值率(%)': 5,
      '填写指引': '原值合计应与1601科目借方一致，折旧合计应与1602科目贷方一致',
    },
  ]

  const wb = XLSX.utils.book_new()
  const ws1 = XLSX.utils.json_to_sheet(balanceData)
  ws1['!cols'] = [{ wch: 14 }, { wch: 22 }, { wch: 16 }, { wch: 16 }, { wch: 20 }, { wch: 45 }]
  XLSX.utils.book_append_sheet(wb, ws1, '资产负债期初余额')

  if (isMidYear) {
    const ws2 = XLSX.utils.json_to_sheet(ytdData)
    ws2['!cols'] = [{ wch: 14 }, { wch: 22 }, { wch: 20 }, { wch: 20 }, { wch: 45 }]
    XLSX.utils.book_append_sheet(wb, ws2, '损益本年累计发生额')
  }

  const ws3 = XLSX.utils.json_to_sheet(faData)
  ws3['!cols'] = [{ wch: 22 }, { wch: 16 }, { wch: 16 }, { wch: 16 }, { wch: 18 }, { wch: 14 }, { wch: 16 }, { wch: 45 }]
  XLSX.utils.book_append_sheet(wb, ws3, '固定资产期初卡片')

  saveWorkbook(wb, `梭罗记账_期初建账导入模板_${pName}.xlsx`)
}
