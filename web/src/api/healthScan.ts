/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import * as XLSX from 'xlsx'
import apiClient from '@/api/client'

export interface ScanFlag {
  code: string
  name: string
  severity: 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'LOW'
  deduction: number
  amount?: string
  desc: string
  fix: string
}

export interface DimensionMetric {
  key: string
  name: string
  score: number | null
  status: string
  statusLabel: string
  detail: string
}

export interface ScanCheck {
  code: string
  name: string
  status: 'PASS' | 'RISK' | 'UNKNOWN' | 'NA' | 'ERROR'
  observed: string
  missing: string[]
  fix: string
  source: string
  dimension: string
  deduction: number
}

export interface HealthScanReport {
  totalScore: number | null
  healthLevel: string
  levelLabel: string
  oneSentenceVerdict: string
  taxBureauView: { ruleId: string; riskLevel: string; details: string; checkChain: string }
  bankFunderView: { ruleId: string; creditLevel: string; details: string; checkChain: string }
  dimensions: DimensionMetric[]
  flags: ScanFlag[]
  checks: ScanCheck[]
  coverage: { completed: number; applicable: number; percent: number | null }
  scoreBreakdown: unknown
  urgentAction: string
  recommendedFix: string
  meta: {
    mode: string
    ruleCount: number
    ephemeral: boolean
    baselineVersion: string
    source?: 'backend'
    analysisSource?: string
  }
}

export interface HealthScanPayload {
  mode: 'balance' | 'quiz'
  rows?: Record<string, unknown>[]
  answers?: Record<string, string>
  profile?: { companyType: string; period: string; accountingStandard: string; vatType: string }
  balanceMeta?: { period: string; balanceType: string; currencyUnit: string; confirmed: boolean; complete: boolean }
}

/** 不使用离线示例或第二套规则：失败时由页面提示并允许重试。 */
export async function submitHealthScan(payload: HealthScanPayload): Promise<HealthScanReport> {
  const res = await apiClient.post<{ ok: boolean; report: HealthScanReport }, { ok: boolean; report: HealthScanReport }>(
    '/api/health-scan/analyze', payload,
  )
  if (!res?.ok || !res.report || !Array.isArray(res.report.checks) || !res.report.coverage) {
    throw new Error('初筛服务返回的数据不完整，本次未生成报告，请重试。')
  }
  return { ...res.report, meta: { ...res.report.meta, source: 'backend' } }
}

/** 解析科目余额表；预览中保留识别口径和需要用户确认的不确定性。 */

const BALANCE_CODE_KEYWORDS = ['科目编码', '科目代码', '科目编号']
const BALANCE_NAME_KEYWORDS = ['科目名称', '账户名称']
const BALANCE_HEADER_KEYWORDS = [
  ...BALANCE_CODE_KEYWORDS,
  ...BALANCE_NAME_KEYWORDS,
  '科目', '余额', '借方', '贷方', '方向', '期初', '期末', '年初', '年末', '发生额',
]

function balanceCellText(v: unknown): string {
  return String(v ?? '').trim()
}

function balanceAmountMissing(v: unknown): boolean {
  return v === null || v === undefined || (typeof v === 'string' && /^(\s*|\s*[-—–]+\s*)$/.test(v))
}

function balanceToAmount(v: unknown, row: number): number {
  if (balanceAmountMissing(v)) return 0
  let s = String(v).trim().replace(/[¥￥\s]/g, '')
  const paren = s.match(/^(?:\(([^()]+)\)|（([^（）]+)）)$/)
  if (paren) s = `-${paren[1] || paren[2]}`
  if (!/^[+-]?(?:(?:\d{1,3}(?:[,，]\d{3})+)|\d+)(?:\.\d{1,2})?$/.test(s)) {
    throw new Error(`第 ${row} 行存在无效金额，请检查余额列`)
  }
  const clean = s.replace(/[,，]/g, '')
  const negative = clean.startsWith('-')
  const [whole, fraction = ''] = clean.replace(/^[+-]/, '').split('.')
  const cents = Number(whole) * 100 + Number(fraction.padEnd(2, '0'))
  if (!Number.isSafeInteger(cents)) throw new Error(`第 ${row} 行存在无效金额，请检查余额列`)
  return (negative ? -cents : cents) / 100
}

// 严格白名单：仅保留会计科目语义，不上传自由文本中的人名或往来单位。
function balanceSafeName(name: string): string {
  const known = ['其他应收款', '其他应付款', '未分配利润', '管理费用', '研发支出', '研发费用', '累计折旧', '固定资产', '库存现金', '银行存款', '预收账款', '实收资本', '注册资本', '原材料', '库存商品', '周转材料', '备用金']
  const matches = known.filter(k => name.includes(k))
  return matches.length ? matches.join('-') : '已脱敏科目'
}

function balanceIsSummaryRow(code: string, name: string): boolean {
  const t = (code + name).replace(/\s/g, '')
  if (!t) return false
  // 合计 / 总计 / 小计 / 本年合计 / 累计合计 …（合计行混入会重复计金额）
  return /^(合计|总计|小计|累计|本年合计|本期合计|月合计)/.test(t) || (t.includes('合计') && t.length <= 6)
}

function balanceLooksLikeHeaderRow(row: unknown[]): boolean {
  let genericHits = 0
  let strongHit = false
  for (const cell of row) {
    const t = balanceCellText(cell)
    if (!t) continue
    if (BALANCE_CODE_KEYWORDS.some(kw => t.includes(kw))) strongHit = true
    if (BALANCE_NAME_KEYWORDS.some(kw => t.includes(kw))) strongHit = true
    if (BALANCE_HEADER_KEYWORDS.some(kw => t.includes(kw))) genericHits += 1
  }
  return strongHit && genericHits >= 2
}

export interface BalanceFilePreview {
  periodLabel: string
  balanceType: 'ending' | 'opening' | 'unknown'
  unit: 'yuan' | 'unknown'
  debitTotal: number
  creditTotal: number
  warnings: string[]
  hasAmbiguity: boolean
  /** 零基列号；-1 表示不存在。 */
  selectedColumns: { code: number; name: number; direction: number; debit: number; credit: number; balance: number }
}

export async function parseBalanceFile(file: File): Promise<Record<string, unknown>[]> {
  return (await parseBalanceFileWithPreview(file)).rows
}

export async function parseBalanceFileWithPreview(file: File): Promise<{ rows: Record<string, unknown>[]; preview: BalanceFilePreview }> {
  const buffer = await file.arrayBuffer()
  const workbook = XLSX.read(buffer, { type: 'array' })
  if (!workbook.SheetNames.length) {
    throw new Error('未读取到工作表')
  }
  // 多工作表时优先取名字含「余额/科目/试算」的表
  const sheetName = workbook.SheetNames.find(n => /余额|科目|试算/.test(n)) || workbook.SheetNames[0]
  const sheet = workbook.Sheets[sheetName]
  const grid = XLSX.utils.sheet_to_json(sheet, { header: 1, defval: '' }) as unknown[][]

  if (!grid.length) {
    throw new Error('表格为空，未检测到科目数据')
  }

  // ── 1. 定位表头行（前 12 行内：命中科目编码/科目名称 + ≥2 个表头关键词）──
  let headerIdx = -1
  for (let r = 0; r < Math.min(12, grid.length); r++) {
    if (balanceLooksLikeHeaderRow(grid[r] || [])) {
      headerIdx = r
      break
    }
  }
  if (headerIdx === -1) {
    throw new Error('未识别到表头行：请确认导出文件包含「科目编码/科目名称」与余额金额列的表头')
  }
  const parentRow = grid[headerIdx]

  // ── 2. 识别两级合并表头（子行仅含 借方/贷方）──
  const secondRow = grid[headerIdx + 1] || []
  const secondNonEmpty = secondRow.filter(c => balanceCellText(c)).length
  const secondAllSide = secondRow.every(c => {
    const t = balanceCellText(c)
    return !t || /借|贷/.test(t)
  })
  const isTwoLevel = secondNonEmpty >= 2 && secondAllSide

  // ── 3. 解析列语义（父表头向左回溯承接合并单元格，如「期末余额」横跨借/贷两列）──
  const width = Math.max(parentRow.length, isTwoLevel ? secondRow.length : 0)
  const colParent: string[] = []
  const colLeaf: string[] = []
  let carry = ''
  for (let c = 0; c < width; c++) {
    const p = balanceCellText(parentRow[c])
    if (p) carry = p
    colParent.push(carry)
    colLeaf.push(isTwoLevel ? balanceCellText(secondRow[c]) : '')
  }
  const colLabel = (c: number) => (colLeaf[c] ? `${colParent[c]}|${colLeaf[c]}` : colParent[c])
  const findCol = (pred: (label: string) => boolean) => {
    for (let c = 0; c < width; c++) {
      if (pred(colLabel(c))) return c
    }
    return -1
  }

  const codeCol = findCol(l => BALANCE_CODE_KEYWORDS.some(kw => l.includes(kw)))
  let nameCol = findCol(l => BALANCE_NAME_KEYWORDS.some(kw => l.includes(kw)))
  if (nameCol === -1) nameCol = findCol(l => l === '科目')
  const directionCol = findCol(l => l.includes('方向'))

  // 按同一期别选择完整列组，不把期初与期末或发生额混用。
  type BalanceType = BalanceFilePreview['balanceType']
  const candidates: Record<BalanceType, { debit: number[]; credit: number[]; balance: number[] }> = {
    ending: { debit: [], credit: [], balance: [] },
    opening: { debit: [], credit: [], balance: [] },
    unknown: { debit: [], credit: [], balance: [] },
  }
  for (let c = 0; c < width; c++) {
    const label = colLabel(c).replace(/\s/g, '')
    if (/发生额|累计/.test(label)) continue
    const kind: BalanceType = /期初|年初|上年末/.test(label) ? 'opening' : /期末|年末|本期末/.test(label) ? 'ending' : 'unknown'
    if (/[借]/.test(label) && !/[贷]/.test(label) && (kind !== 'unknown' || /余额|^借方?$/.test(label))) candidates[kind].debit.push(c)
    else if (/[贷]/.test(label) && !/[借]/.test(label) && (kind !== 'unknown' || /余额|^贷方?$/.test(label))) candidates[kind].credit.push(c)
    else if (/余额|期末数|年末数|年初数|期初数/.test(label) && !/[借贷]/.test(label)) candidates[kind].balance.push(c)
  }
  const kind = (['ending', 'unknown', 'opening'] as BalanceType[]).find(k =>
    candidates[k].debit.length || candidates[k].credit.length || (directionCol >= 0 && candidates[k].balance.length),
  )
  const selected = kind ? candidates[kind] : { debit: [], credit: [], balance: [] }
  if (selected.debit.length > 1 || selected.credit.length > 1 || (!selected.debit.length && !selected.credit.length && selected.balance.length > 1)) {
    throw new Error('余额金额列存在歧义，请整理表头后重试')
  }
  const debitCol = selected.debit[0] ?? -1
  const creditCol = selected.credit[0] ?? -1
  const balanceCol = debitCol === -1 && creditCol === -1 && directionCol >= 0 ? (selected.balance[0] ?? -1) : -1
  if ((debitCol === -1) !== (creditCol === -1)) {
    throw new Error('余额借贷列不完整，请确认同一期别的借方和贷方列')
  }

  if (codeCol === -1 && nameCol === -1) {
    throw new Error('未识别到科目编码/科目名称列，请确认导出的是标准科目余额表')
  }
  if (debitCol === -1 && creditCol === -1 && !(directionCol >= 0 && balanceCol >= 0)) {
    throw new Error('未识别到余额金额列：表头需包含「期末余额-借方/贷方」「期初借方余额」或「方向 + 期末余额」等金额列')
  }

  // ── 4. 提取数据行（剔除合计行、重复表头行、元信息行）──
  const out: Record<string, unknown>[] = []
  let singleSideMissing = 0
  let seq = 0
  for (let r = headerIdx + (isTwoLevel ? 2 : 1); r < grid.length; r++) {
    const row = grid[r] || []
    const code = codeCol >= 0 ? balanceCellText(row[codeCol]) : ''
    const name = nameCol >= 0 ? balanceCellText(row[nameCol]) : ''
    if (!code && !name) continue
    const joined = (code + '|' + name).replace(/\s/g, '')
    // 多页导出的重复表头行 / 标题与核算单位元信息行
    if (
      BALANCE_CODE_KEYWORDS.some(kw => joined.includes(kw)) ||
      BALANCE_NAME_KEYWORDS.some(kw => joined.includes(kw)) ||
      joined.includes('核算单位') ||
      /^期间[:：]/.test(joined)
    ) {
      continue
    }
    if (balanceIsSummaryRow(code, name)) continue

    seq += 1
    let debit = 0
    let credit = 0
    if (debitCol >= 0 || creditCol >= 0) {
      if (balanceAmountMissing(row[debitCol]) && balanceAmountMissing(row[creditCol])) {
        throw new Error(`第 ${r + 1} 行借贷余额均为空或未确定，请核实并填写明确金额`)
      }
      if (balanceAmountMissing(row[debitCol]) || balanceAmountMissing(row[creditCol])) singleSideMissing += 1
      debit = debitCol >= 0 ? balanceToAmount(row[debitCol], r + 1) : 0
      credit = creditCol >= 0 ? balanceToAmount(row[creditCol], r + 1) : 0
    } else {
      if (balanceAmountMissing(row[balanceCol])) throw new Error(`第 ${r + 1} 行余额为空或未确定，请核实并填写明确金额`)
      const dir = balanceCellText(row[directionCol]).replace(/\s/g, '')
      const amt = balanceToAmount(row[balanceCol], r + 1)
      if (/^(贷|贷方)$/.test(dir)) credit = amt
      else if (/^(借|借方)$/.test(dir)) debit = amt
      else if (/^(平|平衡)$/.test(dir) && amt === 0) { /* 零余额平向 */ }
      else throw new Error(`第 ${r + 1} 行余额方向未知，请核实借贷方向`)
    }

    out.push({
      account_code: /^\d{4,20}$/.test(code) ? code : `ACC-${seq}`,
      account_name: balanceSafeName(name),
      debit_balance: debit,
      credit_balance: credit,
    })
  }

  if (!out.length) {
    throw new Error('未识别到科目数据行：表头下方需存在科目明细（含科目编码或科目名称）')
  }

  // 逐层核对直接子级的借贷净额，异向明细可在父级轧差；不一致不能静默漏数。
  const codes = out.map(r => String(r.account_code))
  const numericCodes = codes.filter(c => /^\d{4,20}$/.test(c))
  if (new Set(numericCodes).size !== numericCodes.length) throw new Error('科目编码重复，请核对后重试')
  const ancestors = new Set<number>()
  const cents = (row: Record<string, unknown>, key: string) => Math.round(Number(row[key]) * 100)
  for (let i = 0; i < out.length; i++) {
    const parent = codes[i]
    if (!/^\d{4,20}$/.test(parent)) continue
    const descendants = codes.map((c, j) => ({ c, j })).filter(({ c }) => /^\d{4,20}$/.test(c) && c.length > parent.length && c.startsWith(parent))
    if (!descendants.length) continue
    const children = descendants.filter(({ c }) => !descendants.some(other => other.c !== c && c.startsWith(other.c)))
    const net = (row: Record<string, unknown>) => cents(row, 'debit_balance') - cents(row, 'credit_balance')
    let sum = 0
    for (const child of children) {
      const childNet = net(out[child.j])
      sum += childNet
      if (!Number.isSafeInteger(childNet) || !Number.isSafeInteger(sum)) throw new Error('余额合计超出支持范围')
    }
    const parentNet = net(out[i])
    if (!Number.isSafeInteger(parentNet)) throw new Error('余额合计超出支持范围')
    if (sum !== parentNet) throw new Error(`科目 ${parent} 父子余额不一致，请核对明细后重试`)
    ancestors.add(i)
  }
  const leaves = out.filter((_, i) => !ancestors.has(i))
  const debitCents = leaves.reduce((sum, row) => sum + cents(row, 'debit_balance'), 0)
  const creditCents = leaves.reduce((sum, row) => sum + cents(row, 'credit_balance'), 0)
  if (!Number.isSafeInteger(debitCents) || !Number.isSafeInteger(creditCents)) throw new Error('余额合计超出支持范围')

  // 仅读取期间和计量单位，不把标题中的企业名称带入预览或上传。
  const metadata = grid.slice(0, headerIdx + (isTwoLevel ? 2 : 1)).flat().map(balanceCellText)
  const periodMatch = metadata.join(' ').match(/(?:期间|会计期间|日期)\s*[:：]\s*((?:\d{4}[年\-/]\d{1,2}(?:月|[\-/]\d{1,2}日?)?)(?:\s*[~～至—-]\s*(?:\d{4}[年\-/])?\d{1,2}(?:月|[\-/]\d{1,2}日?)?)?)/)
  const unitLabels = metadata.filter(t => /(?:金额)?单位\s*[:：]/.test(t) && !/核算单位/.test(t))
  if (unitLabels.some(t => /单位\s*[:：]\s*(?:万元|千元|亿元|美元|港元|元以外)/.test(t))) throw new Error('余额表金额单位不是元，请转换为元后重试')
  const unit: BalanceFilePreview['unit'] = unitLabels.some(t => /单位\s*[:：]\s*(?:人民币)?元(?:\s|$|[，,；;])/.test(t)) ? 'yuan' : 'unknown'
  const warnings: string[] = []
  if (kind === 'opening') warnings.push('当前使用期初余额，并非期末余额，请确认适用期间。')
  if (kind === 'unknown') warnings.push('余额期初或期末无法确定，请确认。')
  if (unit === 'unknown') warnings.push('金额单位未识别，请确认单位为元。')
  if (!periodMatch) warnings.push('未识别到期间，请确认报表期间。')
  if (singleSideMissing) warnings.push(`${singleSideMissing} 个科目行单侧余额为空或占位，已按零处理，请核对原表。`)
  if (ancestors.size) warnings.push(`已核验并排除 ${ancestors.size} 个与直接子级净余额一致的父级科目，避免重复计算。`)
  if (leaves.every(row => cents(row, 'debit_balance') === 0 && cents(row, 'credit_balance') === 0)) warnings.push('金额全部为 0，请确认不是空白模板或识别错误。')
  return {
    rows: leaves,
    preview: {
      periodLabel: periodMatch?.[1] || '', balanceType: kind || 'unknown', unit,
      debitTotal: debitCents / 100, creditTotal: creditCents / 100,
      warnings, hasAmbiguity: warnings.some(w => !w.startsWith('已核验并排除')),
      selectedColumns: { code: codeCol, name: nameCol, direction: directionCol, debit: debitCol, credit: creditCol, balance: balanceCol },
    },
  }
}
