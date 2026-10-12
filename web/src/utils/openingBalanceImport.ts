/**
 * 期初科目余额导入与对齐工具
 * ponytail: 纯前端函数，基于 SheetJS 解析，支持模糊表头识别与借贷平衡校验
 */
import * as XLSX from 'xlsx'

export interface MatchedOpeningItem {
  account_code: string
  account_name: string
  debit_balance: number
  credit_balance: number
  matched: boolean
  originalCode?: string
  originalName?: string
  warning?: string
}

export interface ParseOpeningResult {
  items: MatchedOpeningItem[]
  totalDebit: number
  totalCredit: number
  difference: number
  isBalanced: boolean
  matchedCount: number
  unmatchedCount: number
  warnings: string[]
}

const CODE_ALIASES = ['科目编码', '科目代码', '科目编号', '代码', '编码', 'account_code', 'code']
const NAME_ALIASES = ['科目名称', '科目', '名称', 'account_name', 'name']
const DEBIT_ALIASES = ['期初借方', '借方期初', '期初借方余额', '借方余额', '期末借方', '借方', 'debit', 'debit_balance']
const CREDIT_ALIASES = ['期初贷方', '贷方期初', '期初贷方余额', '贷方余额', '期末贷方', '贷方', 'credit', 'credit_balance']
const DIRECTION_ALIASES = ['方向', '借贷方向', '借/贷', 'direction']
const BALANCE_ALIASES = ['期初余额', '年初余额', '余额', '金额', 'balance', 'amount']

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
  existingAccounts: { account_code: string; account_name?: string }[],
): ParseOpeningResult {
  if (!rows || rows.length === 0) {
    return {
      items: [],
      totalDebit: 0,
      totalCredit: 0,
      difference: 0,
      isBalanced: false,
      matchedCount: 0,
      unmatchedCount: 0,
      warnings: ['文件为空或无有效数据行'],
    }
  }

  // 聚合所有行中的键名，防止第一行某些稀疏列未出现
  const allKeys = Array.from(new Set(rows.flatMap(r => Object.keys(r))))

  const codeKey = findKey(allKeys, CODE_ALIASES)
  const nameKey = findKey(allKeys, NAME_ALIASES)
  const debitKey = findKey(allKeys, DEBIT_ALIASES)
  const creditKey = findKey(allKeys, CREDIT_ALIASES)
  const directionKey = findKey(allKeys, DIRECTION_ALIASES)
  const balanceKey = findKey(allKeys, BALANCE_ALIASES)

  const warnings: string[] = []
  if (!codeKey && !nameKey) {
    warnings.push('未能识别科目代码或科目名称列')
    return {
      items: [],
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
  for (const acc of existingAccounts) {
    const code = String(acc.account_code).trim()
    const name = String(acc.account_name || '').trim()
    const item = { account_code: code, account_name: name }
    codeMap.set(code, item)
    if (name) nameMap.set(name, item)
  }

  const resultMap = new Map<string, MatchedOpeningItem>()

  // 初始化所有系统已有科目，默认余额为 0
  for (const acc of existingAccounts) {
    resultMap.set(acc.account_code, {
      account_code: acc.account_code,
      account_name: acc.account_name || acc.account_code,
      debit_balance: 0,
      credit_balance: 0,
      matched: false,
    })
  }

  let matchedCount = 0
  let unmatchedCount = 0

  for (const row of rows) {
    const rawCode = codeKey ? String(row[codeKey] ?? '').trim() : ''
    const rawName = nameKey ? String(row[nameKey] ?? '').trim() : ''

    // 过滤掉总计行/空行
    if (rawCode.includes('合计') || rawName.includes('合计') || rawName.includes('总计')) {
      continue
    }
    if (!rawCode && !rawName) continue

    // 计算借贷数值
    let debit = debitKey ? parseNumber(row[debitKey]) : 0
    let credit = creditKey ? parseNumber(row[creditKey]) : 0

    // 如果是单列金额 + 方向模式
    if (balanceKey && directionKey) {
      const dir = String(row[directionKey] ?? '').trim()
      const bal = parseNumber(row[balanceKey])
      if (dir.includes('借')) {
        debit = bal
      } else if (dir.includes('贷')) {
        credit = bal
      }
    }

    // 冲抵同一科目同时填写借贷的情况，避免后端冲突
    if (debit > 0 && credit > 0) {
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

    // 匹配系统科目（先按代码精准匹配，再按前缀/清洗代码匹配，最后按名称匹配）
    const cleanCode = rawCode.replace(/[.\-_]/g, '')
    const prefixCode = rawCode.includes('.') ? rawCode.split('.')[0] : rawCode.includes('-') ? rawCode.split('-')[0] : ''
    let target = codeMap.get(rawCode) || codeMap.get(cleanCode)
    if (!target && prefixCode) {
      target = codeMap.get(prefixCode)
    }
    if (!target && rawName) {
      target = nameMap.get(rawName)
    }

    if (target) {
      const existing = resultMap.get(target.account_code)!
      existing.debit_balance = Math.round((existing.debit_balance + debit) * 100) / 100
      existing.credit_balance = Math.round((existing.credit_balance + credit) * 100) / 100
      existing.matched = true
      existing.originalCode = rawCode
      existing.originalName = rawName
      matchedCount++
    } else {
      unmatchedCount++
      warnings.push(`未匹配到系统科目：[${rawCode || '-'}] ${rawName || '-'} (借: ${debit}, 贷: ${credit})`)
    }
  }

  const items = Array.from(resultMap.values())
  const totalDebit = Math.round(items.reduce((s, i) => s + (i.debit_balance || 0), 0) * 100) / 100
  const totalCredit = Math.round(items.reduce((s, i) => s + (i.credit_balance || 0), 0) * 100) / 100
  const difference = Math.round(Math.abs(totalDebit - totalCredit) * 100) / 100
  const isBalanced = difference === 0 && totalDebit > 0

  return {
    items,
    totalDebit,
    totalCredit,
    difference,
    isBalanced,
    matchedCount,
    unmatchedCount,
    warnings,
  }
}

/**
 * 导出标准期初科目余额导入模板 Excel
 */
export function exportOpeningTemplate(
  accounts: { account_code: string; account_name?: string }[],
  fiscalYear: number,
) {
  const data = accounts.map(a => ({
    '科目代码': a.account_code,
    '科目名称': a.account_name || a.account_code,
    '期初借方余额': '',
    '期初贷方余额': '',
    '填写指引': '资产/成本/费用类通常在借方，负债/权益/收入类通常在贷方，只需填一侧，无余额留空',
  }))

  const ws = XLSX.utils.json_to_sheet(data)
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, `${fiscalYear}期初余额模板`)

  ws['!cols'] = [
    { wch: 14 },
    { wch: 22 },
    { wch: 16 },
    { wch: 16 },
    { wch: 50 },
  ]

  XLSX.writeFile(wb, `梭罗记账_期初科目余额导入模板_${fiscalYear}.xlsx`)
}
