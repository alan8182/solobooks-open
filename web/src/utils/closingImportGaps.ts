/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 月度结账第 1 步「单据未上传」提示的派生口径。
 * 所有判断只看向导当前所选月份（本月口径），与卡片标签、顶部状态条共用同一份真相。
 */

export interface ClosingImportCounts {
  /** 本月发票行数（发票台账本月口径） */
  invoiceCount: number
  /** 本月微信/平台账单笔数（服务端已导入记录） */
  wechatBillCount: number
  /** 本月银行流水笔数（月度口径，不是账套全量） */
  bankStatementCount: number
}

export type ClosingImportCategoryKey = 'invoice' | 'wechat' | 'bank'

export interface ClosingImportGap {
  key: ClosingImportCategoryKey
  label: string
}

/** 三类单据的展示名与判定顺序固定：发票 → 微信账单 → 银行流水，与第 1 步卡片编号一致 */
const IMPORT_CATEGORY_LABELS: ReadonlyArray<{ key: ClosingImportCategoryKey; label: string }> = [
  { key: 'invoice', label: '发票' },
  { key: 'wechat', label: '微信账单' },
  { key: 'bank', label: '银行流水' },
]

/**
 * 推导本月还没上传的单据类别。数量为 0 即视为「没上传」；
 * 零账目月的豁免由调用方处理（isZeroRecordMonth 不在此判断）。
 */
export function deriveMissingClosingImports(counts: ClosingImportCounts): ClosingImportGap[] {
  const countOf: Record<ClosingImportCategoryKey, number> = {
    invoice: counts.invoiceCount,
    wechat: counts.wechatBillCount,
    bank: counts.bankStatementCount,
  }
  return IMPORT_CATEGORY_LABELS.filter(({ key }) => !(countOf[key] > 0))
}

/** 「发票、银行流水」形式的缺失清单文案，用于状态条与确认弹窗 */
export function formatMissingImportLabels(gaps: ClosingImportGap[]): string {
  return gaps.map((gap) => gap.label).join('、')
}

/**
 * 本月口径的银行流水过滤：按交易日期前缀匹配所选月份。
 * 空日期/解析失败的流水不归属任何月份（与 unmatched 列表的口径一致）。
 */
export function filterStatementsInMonth<T extends { transaction_date?: string | null }>(
  statements: T[],
  month: string,
): T[] {
  return statements.filter((statement) => String(statement.transaction_date || '').startsWith(month))
}
