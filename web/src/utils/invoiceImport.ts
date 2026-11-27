/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { joinPayer, splitPayer, type PayerKind } from './fundsChannel'

/** 发票导入确认卡片的付款方选项：默认公司账户，只有用户选垫付才改凭证对手方。 */
export const INVOICE_PAYER_OPTIONS: Array<{ label: string; value: PayerKind }> = [
  { label: '公司账户（未付款）', value: 'company_account' },
  { label: '老板垫付', value: 'boss_personal' },
  { label: '员工垫付', value: 'employee_personal' },
]

export interface InvoicePayerState {
  kind: PayerKind
  employeeName: string
}

/** 行上初始付款方：沿用后端给的 payer，缺省公司账户（与导入前行为一致）。 */
export function initialInvoicePayer(payer?: unknown): InvoicePayerState {
  const { kind, employeeName } = splitPayer(payer)
  return { kind: kind === 'receivable' ? 'company_account' : kind, employeeName }
}

/**
 * 行上选择的付款方 → 后端 payer。
 * 收入票的结算方式仍由科目（1122 应收）决定，这里固定回公司账户，
 * 避免「老板垫付」把销项票的应收账款对手方抢走。
 */
export function invoiceRowPayer(
  direction: string,
  payer?: InvoicePayerState | null,
): string {
  if (direction !== 'expense') return 'company_account'
  return joinPayer(payer?.kind || 'company_account', payer?.employeeName)
}

export interface InvoicePreviewSource {
  category?: unknown
  suggestedCategory?: unknown
  suggestedCategoryReason?: unknown
  direction?: unknown
  payer?: unknown
  [key: string]: unknown
}

/**
 * 分类的来源：
 * - explicit：行上自带分类（用户确认过）
 * - suggested：后端按品名规则给出建议
 * - fallback：没有任何依据，仅按方向兜底（不能当成用户决定提交）
 */
export type InvoiceCategorySource = 'explicit' | 'suggested' | 'fallback'

export interface InvoicePreviewRow extends InvoicePreviewSource {
  _direction: string
  _category: string
  _categoryHint: string
  _categorySource: InvoiceCategorySource
  _payer: InvoicePayerState
}

/**
 * 导入行的分类：行自带分类 > 后端按品名的确定性建议 > 方向兜底。
 *
 * 电票 XML/PDF 管线与没有分类列的 Excel 都不产出 category，以前一律兜底
 * 「其他支出」：品名写着「软件测试服务」的服务费被记进 5602999 管理费用-其他
 * （2026-10 王翼 ¥300 即此形态），销项票还会因分类与方向不同侧被收支校验拒单。
 * 发票导入预览与结账向导批量导入共用这一份判断，避免两条路径分叉。
 */
export function resolveInvoiceCategory(
  category: unknown,
  suggestedCategory: unknown,
  direction?: unknown,
): string {
  const explicit = String(category ?? '').trim()
  if (explicit) return explicit
  const suggested = String(suggestedCategory ?? '').trim()
  if (suggested) return suggested
  return String(direction || '') === 'income' ? '主营业务收入' : '其他支出'
}

/**
 * 预览行初始化：分类规则同 {@link resolveInvoiceCategory}；
 * 建议来源会带成输入框下的说明，用户手改分类后由页面清空。
 */
export function invoicePreviewRow(row: InvoicePreviewSource): InvoicePreviewRow {
  const explicit = String(row.category ?? '').trim()
  const suggested = String(row.suggestedCategory ?? '').trim()
  const usedSuggestion = !explicit && Boolean(suggested)
  const categorySource: InvoiceCategorySource = explicit
    ? 'explicit'
    : usedSuggestion
      ? 'suggested'
      : 'fallback'
  return {
    ...row,
    _direction: String(row.direction || 'expense'),
    _category: resolveInvoiceCategory(row.category, row.suggestedCategory, row.direction),
    _categoryHint: usedSuggestion
      ? String(row.suggestedCategoryReason || '').trim() || '按发票品名建议'
      : categorySource === 'fallback'
        ? '未匹配到品名规则，导入时自动补分类'
        : '',
    _categorySource: categorySource,
    _payer: initialInvoicePayer(row.payer),
  }
}

/**
 * 发给后端的分类字段：只有用户确认/规则建议过的分类才提交。
 *
 * 兜底值（其他支出 / 主营业务收入）是展示用的占位，不是用户决定。把它当
 * category 发过去会让后端误以为「用户已确认」，从而跳过补分类——2026-06
 * 「微信平台服务」300 元就是这样静默进了 5602999 管理费用-其他。
 */
export function invoiceCategoryPayload(
  row: Pick<InvoicePreviewRow, '_category' | '_categorySource'>,
): { category?: string } {
  return row._categorySource === 'fallback' ? {} : { category: row._category }
}

/** 员工垫付必须选到人：裸 employee_personal 不会建个人往来，2241 会两头落空。 */
export function hasUnnamedEmployeePayer(
  rows: Array<{ direction: string; payer?: InvoicePayerState | null }>,
): boolean {
  return rows.some(
    (row) =>
      row.direction === 'expense' &&
      row.payer?.kind === 'employee_personal' &&
      !String(row.payer.employeeName || '').trim(),
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// 发票导入文件识别
//
// 结账向导和发票台账都允许从 Excel 导入发票，但两条路径都有"模糊列名映射"：
// 只要表头里出现「金额」就当成发票金额。银行流水恰好有「支出金额/收入金额/
// 账户余额」，被映射后能通过后端旧校验，批量生成无号码、无开票日期的草稿
// 发票（2026-11 真实发生）。文件级拦截是第一道闸，后端 parse 的发票要素校验
// 是第二道，两道都要有。
// ─────────────────────────────────────────────────────────────────────────────

/** 资金流水/对账单的特征表头：命中两条以上就认定不是发票明细。 */
const STATEMENT_HEADER_PATTERNS: RegExp[] = [
  /交易时间|交易日期|记账日期|入账日期/,
  /交易金额|支出金额|收入金额|发生额|借方金额|贷方金额/,
  /账户余额|余额（元）|余额\(元\)|^余额$/,
  /对方账号|对手方.*账号|己方.*账号|收付款方账号/,
  /对方户名|对手方.*户名|己方.*户名|收款方户名|付款方户名/,
  /摘要|附言|用途|交易类型/,
]

/** 发票明细表头：税务发票导出、代账软件发票清单至少会命中一条。 */
const INVOICE_HEADER_PATTERNS: RegExp[] = [
  /发票号码|发票代码|发票编号|数电(发票)?号码|数电号码/,
  /开票日期|开票时间|发票日期/,
  /销方名称|销售方名称|销方识别号|销售方识别号/,
  /购买方名称|购方名称|购方识别号|购买方识别号/,
  /价税合计|不含税金额|税额|税率/,
  /invoice.*(no|num|date|amount)|seller|buyer/i,
]

function normalizeColumns(columns: string[]): string[] {
  return columns.map((column) => String(column ?? '').trim()).filter(Boolean)
}

/** 表头像银行/支付流水（至少两条特征）。 */
export function looksLikeStatementColumns(columns: string[]): boolean {
  const cols = normalizeColumns(columns)
  return STATEMENT_HEADER_PATTERNS.filter((pattern) => cols.some((column) => pattern.test(column))).length >= 2
}

/** 表头里是否至少有一个发票字段。 */
export function hasInvoiceColumns(columns: string[]): boolean {
  const cols = normalizeColumns(columns)
  return INVOICE_HEADER_PATTERNS.some((pattern) => cols.some((column) => pattern.test(column)))
}

/**
 * 判断文件能否进入发票导入，返回拒绝原因；null 表示可以继续。
 *
 * @param allowManualMapping 发票台账的映射向导允许用户手动指定列，此时只拦
 *   明确是流水的文件；结账向导没有映射步骤，还要求表头确实像发票。
 */
export function invoiceFileRejection(
  columns: string[],
  opts: { allowManualMapping?: boolean } = {},
): string | null {
  if (looksLikeStatementColumns(columns)) {
    return '这是银行流水/资金明细，不是发票明细。请到「银行流水」上传，系统会按流水自动对账。'
  }
  if (!opts.allowManualMapping && !hasInvoiceColumns(columns)) {
    return '表头里没有发票号码、开票日期、购销方等发票字段，这不像税务发票明细导出文件。'
  }
  return null
}
