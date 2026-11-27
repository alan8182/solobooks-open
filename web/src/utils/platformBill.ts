/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 平台账单「解析成功但 0 笔交易」的提示文案。
 *
 * 解析成功但没有任何可入账交易 ≠ 格式错误：最常见是导出的期间里本来就没有
 * 交易（比如按日导出只导了一天，而那天没有收款）。把这种情况说成「请检查
 * 文件格式」会让人去核对一个根本没问题的文件。期间取自账单文件头的
 * 「起始日期/终止日期」，支付宝解析器已归一到 periodStart/periodEnd。
 */
export function emptyBillWarning(summary?: Record<string, any> | null): string {
  const periodStart = String(summary?.periodStart || '')
  const periodEnd = String(summary?.periodEnd || '')
  const period = periodStart && periodEnd
    ? (periodStart === periodEnd ? periodStart : `${periodStart} ~ ${periodEnd}`)
    : ''
  return period
    ? `账单解析成功，但 ${period} 期间没有任何交易记录（交易合计 0 笔），无需导入；请确认导出的日期范围是否正确`
    : '账单解析成功，但该文件里没有任何可入账的交易记录，请确认导出的账单期间是否正确'
}
