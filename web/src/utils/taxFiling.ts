/**
 * 税报 · 申报作业包核心辅助计算与智能诊断
 * ponytail: 纯函数封装，零外部重型依赖，覆盖免税额度监控、AI税情简报提炼与字段过滤
 */

export interface ExemptionGauge {
  threshold: number
  totalSales: number
  percent: number
  remaining: number
  isExempt: boolean
  status: 'safe' | 'warning' | 'danger'
  tip: string
}

/**
 * 计算小规模增值税免税额度进度
 * 季度免税标准 30 万元，月度免税标准 10 万元
 */
export function calculateExemptionGauge(totalSales: number, period: string, periodType = 'quarter'): ExemptionGauge {
  const isMonth = periodType === 'month' || (!period.includes('Q') && /-\d{2}$/.test(period))
  const threshold = isMonth ? 100000 : 300000
  const sales = Math.max(0, Number(totalSales) || 0)
  const remaining = Math.max(0, threshold - sales)
  const percent = Math.min(100, Math.round((sales / threshold) * 1000) / 10)

  let status: 'safe' | 'warning' | 'danger' = 'safe'
  let tip = ''

  if (sales === 0) {
    status = 'safe'
    tip = `本期暂无计税销售额，享受小规模全额免征增值税（限额 ¥${threshold.toLocaleString('zh-CN')}）`
  } else if (sales <= threshold) {
    status = percent > 85 ? 'warning' : 'safe'
    tip = `已使用 ${percent}% 免税额度，剩余免税额度 ¥${remaining.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}，本期免缴增值税`
  } else {
    status = 'danger'
    const exceeded = sales - threshold
    tip = `本期销售额已超免税限额 ¥${exceeded.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}，需按 1% 征收率全额申报增值税`
  }

  return {
    threshold,
    totalSales: sales,
    percent,
    remaining,
    isExempt: sales <= threshold,
    status,
    tip,
  }
}

export interface TaxCopilotInsight {
  headline: string
  detail: string
  badgeText: string
  badgeType: 'success' | 'warning' | 'info' | 'error'
  pathAdvice: string
}

/**
 * 智能税情简报生成（面向无财务背景的小微企业主）
 */
export function generateTaxCopilotInsight(pkg: {
  vat?: { totalSales?: number; isBelowThreshold?: boolean; totalVatPayable?: number }
  cit?: { totalProfit?: number; preferentialTax?: number }
  crossCheck?: { consistent: boolean; differences?: Array<{ label: string }> }
  summary?: { vatPayable?: number; citPayable?: number; blockingIssues?: string[]; warnings?: string[] }
  period?: string
}): TaxCopilotInsight {
  const sales = Number(pkg.vat?.totalSales ?? 0)
  const vatPayable = Number(pkg.summary?.vatPayable ?? pkg.vat?.totalVatPayable ?? 0)
  const citPayable = Number(pkg.summary?.citPayable ?? pkg.cit?.preferentialTax ?? 0)
  const isConsistent = pkg.crossCheck?.consistent ?? true
  const hasBlockers = (pkg.summary?.blockingIssues?.length ?? 0) > 0
  const isBelow = pkg.vat?.isBelowThreshold !== false && sales <= 300000

  // 1. 存在账目阻断异常
  if (hasBlockers) {
    return {
      headline: '检测到申报阻塞项，请先修正账目',
      detail: `当前存在 ${pkg.summary?.blockingIssues?.length} 项阻断问题，数据尚未达到可申报标准。`,
      badgeText: '申报受阻',
      badgeType: 'error',
      pathAdvice: '优先根据阻断提示修正记账或流水，再执行申报抄报。',
    }
  }

  // 2. 零业务月份/季度
  if (sales === 0 && vatPayable === 0 && citPayable === 0) {
    return {
      headline: '本期无经营入账，符合一键零申报条件',
      detail: '本期账载销售额、发票开具与银行流水均无应税收入，增值税与企业所得税预计应纳税额均为 ¥0.00。',
      badgeText: '建议零申报',
      badgeType: 'success',
      pathAdvice: '登录电子税务局直接确认各税种「零申报」即可快速办结。',
    }
  }

  // 3. 小规模免税期（销售额 <= 30万）
  if (isBelow && vatPayable === 0) {
    const diffHint = !isConsistent
      ? '（提醒：账票流水存在差额，请核对是否含未开票收入或跨期账目）'
      : '（账·票·流水三方完全一致）'

    return {
      headline: '销售额未超 30 万红线，享受增值税全额免征',
      detail: `本期账载销售额 ¥${sales.toLocaleString('zh-CN', { minimumFractionDigits: 2 })} 未达起征点，增值税应缴 ¥0.00 ${diffHint}。`,
      badgeText: '享受免税',
      badgeType: 'success',
      pathAdvice: '在电子税务局增值税申报表中将销售额填入「免税销售额」对应行，不可填错至应税栏。',
    }
  }

  // 4. 超免税额度需缴税
  return {
    headline: `本期应纳增值税预估 ¥${vatPayable.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}`,
    detail: `销售额已超小规模季度 30 万元免税门槛，适用 1% 征收率；企业所得税按小型微利优惠 5% 预缴 ¥${citPayable.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}。`,
    badgeText: '正常缴税申报',
    badgeType: 'warning',
    pathAdvice: '请核对第1行开票销售额及减免税减征栏，按测算金额在税局申报并及时扣款。',
  }
}

/**
 * 格式化纯数字供剪贴板一键复制
 */
export function formatCopyAmount(amount: number | string | undefined | null): string {
  const n = Number(amount ?? 0)
  return n.toFixed(2)
}

export interface DeadlineSummary {
  nearestDaysLeft: number | null
  overdueCount: number
  pendingCount: number
  isAllFiled: boolean
}

/**
 * 计算征期看板汇总（防呆防护：优先取未逾期主税种倒计时，避免个别历史负数直接覆盖全局）
 */
export function calculateDeadlineSummary(
  items: Array<{ status?: string; daysLeft?: number | null }>
): DeadlineSummary {
  const pending = items.filter(d => d.status !== 'filed' && d.daysLeft != null)
  const overdueCount = pending.filter(d => (d.daysLeft as number) < 0).length
  const pendingCount = items.filter(d => d.status !== 'filed').length
  const isAllFiled = pendingCount === 0

  if (!pending.length) {
    return {
      nearestDaysLeft: null,
      overdueCount: 0,
      pendingCount,
      isAllFiled,
    }
  }

  // ponytail: 优先展示当前未到期的最近倒计时；若全项皆逾期，取最贴近当前日期的逾期天数
  const upcoming = pending.filter(d => (d.daysLeft as number) >= 0)
  const nearestDaysLeft = upcoming.length > 0
    ? Math.min(...upcoming.map(d => d.daysLeft as number))
    : Math.max(...pending.map(d => d.daysLeft as number))

  return {
    nearestDaysLeft,
    overdueCount,
    pendingCount,
    isAllFiled,
  }
}
