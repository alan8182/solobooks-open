/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 银行流水文件解析的唯一口径：文件 → 标准流水行。
 * 月度结账向导与银行对账页共用；微信账单、后端 CSV 透传不在其列。
 */

import * as XLSX from 'xlsx'
import { buildBankStatementDescription } from '@/utils/bankStatementDescription'

export type BankColumnMapping = Record<string, string>

export interface BankRowDraft {
  transaction_date: string
  description: string
  amount: number
  balance: number
}

export interface BankRowsResult {
  rows: BankRowDraft[]
  /** 解析不出合法交易日期而被跳过的行数（重复表头、纯时间值等脏行） */
  skippedInvalidDate: number
}

// Convert various date formats (including XLSX serial numbers) to YYYY-MM-DD
export function formatBankDate(val: unknown): string {
  if (val === null || val === undefined || val === '') return ''
  if (typeof val === 'number') {
    const d = XLSX.SSF.parse_date_code(val)
    if (d) {
      const date = `${d.y}-${String(d.m).padStart(2, '0')}-${String(d.d).padStart(2, '0')}`
      const hasTime = Boolean(d.H || d.M || d.S)
      return hasTime
        ? `${date} ${String(d.H ?? 0).padStart(2, '0')}:${String(d.M ?? 0).padStart(2, '0')}:${String(d.S ?? 0).padStart(2, '0')}`
        : date
    }
  }
  const s = String(val).trim()
  if (/^\d{4}-\d{2}-\d{2}/.test(s)) return s.slice(0, 19)
  const m1 = s.match(/^(\d{4})[/](\d{1,2})[/](\d{1,2})(?:[ T](\d{1,2}):(\d{2})(?::(\d{2}))?)?/)
  if (m1) {
    const date = `${m1[1]}-${m1[2].padStart(2, '0')}-${m1[3].padStart(2, '0')}`
    if (m1[4] !== undefined) {
      return `${date} ${m1[4].padStart(2, '0')}:${(m1[5] || '00').padStart(2, '0')}:${(m1[6] || '00').padStart(2, '0')}`
    }
    return date
  }
  const m2 = s.match(/^(\d{4})(\d{2})(\d{2})/)
  if (m2) return `${m2[1]}-${m2[2]}-${m2[3]}`
  return s.slice(0, 19) || ''
}

export function parseMoney(val: unknown): number {
  if (val === undefined || val === null || val === '') return 0
  if (typeof val === 'number') return val
  const cleaned = String(val).replace(/,/g, '').trim()
  const num = parseFloat(cleaned)
  return isNaN(num) ? 0 : num
}

/**
 * 探测流水表的表头行并拆成对象数组：跳过合计/累计等汇总行与表尾，
 * 表头行之前的前置说明行一并丢弃。找不到结构化表头时退回整表对象数组。
 */
export function parseBankSheet(sheet: XLSX.WorkSheet): Record<string, unknown>[] {
  const rawRows = XLSX.utils.sheet_to_json(sheet, { header: 1, defval: '' }) as unknown[][]

  let headerIndex = -1
  for (let i = 0; i < rawRows.length; i++) {
    const row = rawRows[i]
    if (!row) continue

    let score = 0
    for (const cell of row) {
      const val = String(cell ?? '').trim()
      if (/交易日|交易日期|日期|时间|date|time/i.test(val)) score++
      else if (/交易金额|发生额|金额|借贷发生额|amount/i.test(val)) score++
      else if (/交易摘要|摘要|描述|用途|description|desc|remark/i.test(val)) score++
      else if (/账户余额|余额|balance/i.test(val)) score++
    }

    if (score >= 3) {
      headerIndex = i
      break
    }
  }

  if (headerIndex === -1) {
    return XLSX.utils.sheet_to_json(sheet, { defval: '' }) as Record<string, unknown>[]
  }

  const headers = rawRows[headerIndex].map(h => String(h ?? '').trim())

  // Find date column indices to filter out header/footer rows without date
  const dateColIndices: number[] = []
  for (let j = 0; j < headers.length; j++) {
    const h = headers[j].toLowerCase()
    if (/交易日|记账日|入账日|起息日|日期|时间|date|time/i.test(h)) {
      dateColIndices.push(j)
    }
  }

  const json: Record<string, unknown>[] = []

  for (let i = headerIndex + 1; i < rawRows.length; i++) {
    const row = rawRows[i]
    if (!row || row.length === 0) continue

    // Skip empty or summary rows
    const firstCell = String(row[0] ?? '').trim()
    const hasSummaryText = row.some(cell => /累计|合计|总计|总记录数/.test(String(cell ?? '').trim()))
    if (/累计|合计|总计|总记录数/.test(firstCell) || hasSummaryText) continue

    // If a date column is defined but this row has no date, it is likely a footer or summary row
    if (dateColIndices.length > 0) {
      const hasDate = dateColIndices.some(idx => String(row[idx] ?? '').trim() !== '')
      if (!hasDate) continue
    }

    const obj: Record<string, unknown> = {}
    let hasValue = false
    for (let j = 0; j < headers.length; j++) {
      const header = headers[j]
      if (header) {
        obj[header] = row[j] !== undefined ? row[j] : ''
        if (obj[header] !== '') hasValue = true
      }
    }
    if (hasValue) {
      json.push(obj)
    }
  }

  return json
}

/** 解析上传文件的字节内容（xlsx/xls/csv 均可，SheetJS 自动嗅探），取第一个工作表 */
export function parseBankWorkbookData(data: Uint8Array): Record<string, unknown>[] {
  const workbook = XLSX.read(data, { type: 'array' })
  const sheet = workbook.Sheets[workbook.SheetNames[0]]
  return parseBankSheet(sheet)
}

/**
 * 自动列映射。日期列只选一个：优先 交易日期/记账日期/入账日期；与时间列并存时
 * 纯时间列不作日期源，否则起息日期会覆盖交易日期。但杭州银行等只导出「交易时间」
 * 一列，时间列就是唯一日期来源，须兜底选用（formatBankDate 会截掉时分秒），
 * 否则日期全空、后端整批拒绝。
 * 支出/收入列必须同时是金额类表头：付款人名称/名义付款人名称 等对手方列含
 * 「付款」，不限定金额列会把它们误判成金额列，导致金额全为 0。
 */
export function detectBankColumnMapping(columns: string[]): BankColumnMapping {
  const map: BankColumnMapping = {}
  for (const col of columns) map[col] = ''

  const isTimeOnlyCol = (col: string) => {
    const lc = col.toLowerCase()
    return /时间|time/i.test(lc) && !/日期|交易日/i.test(lc)
  }
  const dateCandidates = columns.filter((col) => {
    const lc = col.toLowerCase()
    return !/对方/.test(lc) && !isTimeOnlyCol(col) && /交易日期|记账日期|入账日期|起息日期|日期|交易日|date/i.test(lc)
  })
  const timeCandidates = columns.filter((col) => {
    const lc = col.toLowerCase()
    return !/对方/.test(lc) && isTimeOnlyCol(col)
  })
  const dateCol = dateCandidates.find((col) => /交易日期|记账日期|入账日期|交易日/.test(col))
    || dateCandidates[0]
    || timeCandidates[0]
    || ''

  for (const col of columns) {
    const lc = col.toLowerCase()
    if (col === dateCol) map[col] = 'transaction_date'
    else if (/附言|用途|备注|附加信息|备注信息|附加备注|comment|purpose/i.test(lc)) map[col] = ''
    else if (/账户余额|余额|balance/i.test(lc)) map[col] = 'balance'
    else if (/金额|发生额|amount/i.test(lc) && /支出|借方|支取|付款/i.test(lc) && !/商户支出/i.test(lc)) map[col] = 'expense_amount'
    else if (/金额|发生额|amount/i.test(lc) && /收入|贷方|存入|收款/i.test(lc) && !/商户收入/i.test(lc)) map[col] = 'income_amount'
    else if (/摘要|描述|说明|description|desc/i.test(lc)) map[col] = 'description'
    else if (/金额|amount|交易额|发生额/i.test(lc)) map[col] = 'amount'
  }
  return map
}

interface DescriptionParts {
  typeName: string
  summaryText: string
  commentText: string
  counterpartyName: string
  payeeName: string
  payerName: string
}

/**
 * 从标准化流水描述中提取对方户名（格式为 "(对方: xxx)"）
 */
export function extractStatementCounterparty(description: string): string {
  const match = String(description || '').match(/\(对方\s*[:：]\s*(.+?)\)\s*$/)
  return match ? match[1].trim() : ''
}

/**
 * 单列在「描述增强」里的自动角色（与 collectDescriptionParts 同一套判别，
 * 供映射界面展示「这列会被自动并入描述」。返回空串表示该列纯忽略）。
 */
export function bankColumnDescriptionRole(colName: string): string {
  const name = String(colName).trim()
  if (/己方|本方/i.test(name)) return ''
  if (/交易类型|业务名称|业务类型|类型|type/i.test(name)) return '交易类型'

  const isBranchCol = /开户名|开户行/i.test(name)
  const isAcctCol = /账号|卡号/i.test(name) && !/名称/i.test(name)
  if (isBranchCol || isAcctCol) return ''

  if (/收款人|收款方|收款单位/i.test(name)) return '收款人'
  if (/付款人|付款方|付款单位/i.test(name)) return '付款人'
  if (/对手方|对方|收付方|收\(付\)方|交易对手/i.test(name)) return '对方户名'

  if (/交易摘要|摘要|描述|description|desc|remark/i.test(name)) return '摘要'
  if (/附言|用途|备注|附加信息|备注信息|附加备注|comment|purpose/i.test(name)) return '附言'
  return ''
}

/**
 * 从原始行里收集描述增强列（交易类型/摘要/附言/对手方/收款人/付款人）。独立于列映射：
 * 用户在映射弹窗里把这些列设为「忽略」也不影响描述的完整性。
 *
 * 对手方户名甄别：只有「户名/名称/单位」列（且不含开户名/开户行）才是对方
 * 账户名。账号列是号码不能并入；杭州银行把对方开户行命名为「对手方开户名」，
 * 若照单全收会把银行行名当户名（如「(对方: 中国工商银行总行清算中心)」）。
 * 没有户名列的银行退化用裸「对手方/对方」列；同类多列以后出现的为准。
 */
function collectDescriptionParts(row: Record<string, unknown>): DescriptionParts {
  const parts: DescriptionParts = {
    typeName: '',
    summaryText: '',
    commentText: '',
    counterpartyName: '',
    payeeName: '',
    payerName: '',
  }
  for (const [col, val] of Object.entries(row)) {
    const colName = String(col).trim()
    const strVal = String(val ?? '').trim()
    if (!strVal) continue
    switch (bankColumnDescriptionRole(colName)) {
      case '交易类型': parts.typeName = strVal; break
      case '对方户名': parts.counterpartyName = strVal; break
      case '收款人': parts.payeeName = strVal; break
      case '付款人': parts.payerName = strVal; break
      case '摘要': parts.summaryText = strVal; break
      case '附言': parts.commentText = strVal; break
    }
  }
  return parts
}

/**
 * 原始行 → 标准流水行。金额语义：存入为正、支取为负（两列并存时取非零的
 * 那列，同银行导出习惯一致）；描述统一走 buildBankStatementDescription 管道
 * 口径（类型 | 摘要 | 附言: x (对方: y)），它是后端去重键（日期+描述+金额）
 * 的组成部分，所有入口必须产出同一种格式，否则同一文件重复导入会重复入账。
 * 解析不出合法日期的行不返回——后端对坏日期整行报错，一行脏数据会把失败数
 * 刷成 N 条且看不出原因。
 */
export function buildBankRows(
  rawRows: Record<string, unknown>[],
  mapping: BankColumnMapping,
): BankRowsResult {
  const rows: BankRowDraft[] = []
  let skippedInvalidDate = 0

  for (const row of rawRows) {
    const out: BankRowDraft = { transaction_date: '', description: '', amount: 0, balance: 0 }
    let singleAmount = 0
    let incomeAmount = 0
    let expenseAmount = 0
    let hasSingleAmount = false
    let hasIncomeAmount = false
    let hasExpenseAmount = false

    let mappedCounterparty = ''
    let mappedPayee = ''
    let mappedPayer = ''
    let mappedComment = ''
    let mappedType = ''

    for (const [col, field] of Object.entries(mapping)) {
      if (!field) continue
      const val = row[col]
      if (field === 'transaction_date') {
        out.transaction_date = formatBankDate(val)
      } else if (field === 'description') {
        const strVal = String(val ?? '').trim()
        if (strVal) out.description = out.description ? `${out.description} | ${strVal}` : strVal
      } else if (field === 'balance') {
        out.balance = parseMoney(val)
      } else if (field === 'amount') {
        singleAmount = parseMoney(val)
        hasSingleAmount = true
      } else if (field === 'income_amount') {
        incomeAmount = parseMoney(val)
        hasIncomeAmount = true
      } else if (field === 'expense_amount') {
        expenseAmount = parseMoney(val)
        hasExpenseAmount = true
      } else if (field === 'counterparty_name') {
        mappedCounterparty = String(val ?? '').trim()
      } else if (field === 'payee_name') {
        mappedPayee = String(val ?? '').trim()
      } else if (field === 'payer_name') {
        mappedPayer = String(val ?? '').trim()
      } else if (field === 'comment') {
        mappedComment = String(val ?? '').trim()
      } else if (field === 'transaction_type') {
        mappedType = String(val ?? '').trim()
      }
    }

    if (hasIncomeAmount || hasExpenseAmount) {
      if (incomeAmount > 0) {
        out.amount = Math.abs(incomeAmount)
      } else if (expenseAmount > 0) {
        out.amount = -Math.abs(expenseAmount)
      } else {
        out.amount = 0
      }
    } else if (hasSingleAmount) {
      out.amount = singleAmount
    }

    const parts = collectDescriptionParts(row)
    const effectivePayee = mappedPayee || parts.payeeName
    const effectivePayer = mappedPayer || parts.payerName
    const effectiveCounterparty = mappedCounterparty || parts.counterpartyName

    let resolvedCounterparty = ''
    if (out.amount < 0) {
      // 支出：钱付给谁？优先取收款人，其次取通用对方户名；绝不能回退到付款人（付款人是本方账户）
      resolvedCounterparty = effectivePayee || effectiveCounterparty
    } else if (out.amount > 0) {
      // 收入：钱谁付来的？优先取付款人，其次取通用对方户名；绝不能回退到收款人（收款人是本方账户）
      resolvedCounterparty = effectivePayer || effectiveCounterparty
    } else {
      // 0 或未决方向：优先取通用对方户名，其次收款人/付款人
      resolvedCounterparty = effectiveCounterparty || effectivePayee || effectivePayer
    }

    out.description = buildBankStatementDescription({
      typeName: mappedType || parts.typeName,
      summaryText: parts.summaryText || out.description,
      commentText: mappedComment || parts.commentText,
      counterpartyName: resolvedCounterparty,
    })

    if (/^\d{4}-\d{2}-\d{2}/.test(out.transaction_date)) {
      rows.push(out)
    } else {
      skippedInvalidDate++
    }
  }

  return { rows, skippedInvalidDate }
}
