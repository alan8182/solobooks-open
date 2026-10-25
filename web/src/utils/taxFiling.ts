/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 税报 · 申报作业包核心辅助计算与智能诊断
 * ponytail: 纯函数封装，零外部重型依赖，覆盖免税额度监控、AI税情简报提炼、字段过滤与可切换卡片流转
 */

export type FilingCardKey = 'overview' | 'vat' | 'cit' | 'fin_statements' | 'other_taxes'

export const FILING_CARD_ORDER: FilingCardKey[] = [
  'overview',
  'vat',
  'cit',
  'fin_statements',
  'other_taxes',
]

export const FILING_TASK_CARD_ORDER: FilingCardKey[] = [
  'vat',
  'cit',
  'fin_statements',
  'other_taxes',
]

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
  cit?: { totalProfit?: number; preferentialTax?: number } | null
  citNotice?: { citApplicable?: boolean; incomeTaxLabel?: string }
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
  // 个人独资企业/个体工商户不缴企业所得税，简报口径同步切换
  const citApplicable = pkg.citNotice?.citApplicable !== false
  const incomeTaxLabel = pkg.citNotice?.incomeTaxLabel ?? '企业所得税'

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
      detail: citApplicable
        ? '本期账载销售额、发票开具与银行流水均无应税收入，增值税与企业所得税预计应纳税额均为 ¥0.00。'
        : `本期账载销售额、发票开具与银行流水均无应税收入，增值税预计应纳税额为 ¥0.00；${incomeTaxLabel}请按经营所得自行申报。`,
      badgeText: '建议零申报',
      badgeType: 'success',
      pathAdvice: '增值税申报页无需填报销售额，直接「下一步」确认零申报即可快速办结。',
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
      pathAdvice:
        '在「本期销售情况」对应入口弹窗的「1%征收率」行照实填写销售额即可（季度未超 30 万，税局会自动归集为「免税销售额」），用「预览表单」核对第 9/10 栏后下一步申报；弹窗里的 1% 行不是报表第 1 栏，放心填。',
    }
  }

  // 4. 超免税额度需缴税
  return {
    headline: `本期应纳增值税预估 ¥${vatPayable.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}`,
    detail: `销售额已超小规模季度 30 万元免税门槛，适用 1% 征收率；${
      citApplicable
        ? `企业所得税按小型微利优惠 5% 预缴 ¥${citPayable.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}。`
        : `${incomeTaxLabel}按经营所得（5%–35%）另行申报，当前作业包不测算。`
    }`,
    badgeText: '正常缴税申报',
    badgeType: 'warning',
    pathAdvice:
      '在「本期销售情况」各入口的编辑弹窗「1%征收率」行填入不含税销售额（系统已换算），核对「本期应补（退）税额」后申报并及时扣款。',
  }
}

/**
 * 格式化纯数字供剪贴板一键复制
 */
export function formatCopyAmount(amount: number | string | undefined | null): string {
  const n = Number(amount ?? 0)
  return n.toFixed(2)
}

/**
 * 把期次归一到季度格式（YYYY-Qn）。
 * 首页征期卡等外部入口会带来月度税种期次（如个税扣缴的 2026-01），
 * 而申报作业台按季度组织，跳转/加载前需换算到所在季度；非月度格式原样返回。
 */
export function periodToQuarter(period: string): string {
  const text = (period || '').trim()
  const m = /^(\d{4})-(\d{2})$/.exec(text)
  if (!m) return text
  const month = parseInt(m[2], 10)
  if (month < 1 || month > 12) return text
  return `${m[1]}-Q${Math.ceil(month / 3)}`
}

/** 季度期次的展示名；超出近 6 季下拉窗口的深链期次也能得到规范标签 */
export function quarterLabel(period: string): string {
  const m = /^(\d{4})-Q([1-4])$/.exec(period)
  return m ? `${m[1]} 年第 ${m[2]} 季度` : period
}

export interface DeadlineSummary {
  nearestDaysLeft: number | null
  overdueCount: number
  pendingCount: number
  isAllFiled: boolean
}

/**
 * 计算征期看板汇总（最紧迫优先：有逾期必须亮逾期，不让远期征期冒充最近倒计时）
 */
export function calculateDeadlineSummary(
  items: Array<{ status?: string; daysLeft?: number | null }>
): DeadlineSummary {
  // 豁免（waived）与已办结（filed）同为终态，不计入待办与逾期
  const pending = items.filter(
    d => d.status !== 'filed' && d.status !== 'waived' && d.daysLeft != null,
  )
  const overdueCount = pending.filter(d => (d.daysLeft as number) < 0).length
  const pendingCount = items.filter(d => d.status !== 'filed' && d.status !== 'waived').length
  const isAllFiled = pendingCount === 0

  if (!pending.length) {
    return {
      nearestDaysLeft: null,
      overdueCount: 0,
      pendingCount,
      isAllFiled,
    }
  }

  // ponytail: 原「优先取未逾期项」的防呆分支在混合场景（季度税已逾期 + 残保金
  // 次年 9-30 共 340 天）会让远期年项冒充最近倒计时：红标签报 5 项逾期、大字
  // 却显示「剩 340 天截止」（2026-10-25 真实账套复现）。统一取全部待办的最小
  // daysLeft：有逾期 → 最严重逾期天数；无逾期 → 最近截止日。
  const nearestDaysLeft = Math.min(...pending.map(d => d.daysLeft as number))

  return {
    nearestDaysLeft,
    overdueCount,
    pendingCount,
    isAllFiled,
  }
}

export interface FilingChecklistLite {
  taxKind: string
  status?: string
  dueDate?: string | null
  daysLeft?: number | null
}

export interface FilingCardPackageState {
  vat?: { deadline?: { status?: string; dueDate?: string | null; daysLeft?: number | null } }
  cit?: { deadline?: { status?: string; dueDate?: string | null; daysLeft?: number | null } } | null
  checklist?: FilingChecklistLite[]
}

export interface OtherTaxesSummary {
  status: 'pending' | 'filed' | 'waived' | 'untracked'
  totalCount: number
  pendingCount: number
  resolvedCount: number
}

/**
 * 汇总第 5 张卡片「其他税费确认」（除财务报表外的印花税、个税扣缴、年报等）的整体状态
 */
export function summarizeOtherTaxes(checklist?: FilingChecklistLite[] | null): OtherTaxesSummary {
  const others = (checklist ?? []).filter(item => item.taxKind !== 'fin_statements')
  const tracked = others.filter(item => item.status && item.status !== 'untracked')
  if (tracked.length === 0) {
    return {
      status: 'untracked',
      totalCount: others.length,
      pendingCount: 0,
      resolvedCount: 0,
    }
  }

  const pendingCount = tracked.filter(item => item.status === 'pending').length
  const filedCount = tracked.filter(item => item.status === 'filed').length
  const waivedCount = tracked.filter(item => item.status === 'waived').length
  const resolvedCount = filedCount + waivedCount

  let status: 'pending' | 'filed' | 'waived' = 'filed'
  if (pendingCount > 0) {
    status = 'pending'
  } else if (filedCount === 0 && waivedCount > 0) {
    status = 'waived'
  }

  return {
    status,
    totalCount: tracked.length,
    pendingCount,
    resolvedCount,
  }
}

/**
 * 判断指定申报卡片是否仍处于「待申报 (pending)」状态
 */
export function isFilingCardPending(
  pkg: FilingCardPackageState | null | undefined,
  cardKey: FilingCardKey,
): boolean {
  if (!pkg) return false
  if (cardKey === 'overview') return false
  if (cardKey === 'vat') return pkg.vat?.deadline?.status === 'pending'
  if (cardKey === 'cit') return pkg.cit?.deadline?.status === 'pending'
  if (cardKey === 'fin_statements') {
    const fin = (pkg.checklist ?? []).find(item => item.taxKind === 'fin_statements')
    return fin?.status === 'pending'
  }
  if (cardKey === 'other_taxes') {
    return summarizeOtherTaxes(pkg.checklist).status === 'pending'
  }
  return false
}

/**
 * 当用户在某张卡片完成「标记办结 / 本期不适用」或从「报前核对」点击开始申报时，
 * 自动查找下一张仍处于「待申报 (pending)」的卡片：
 * 1. 若当前卡片仍有未办结子项（例如「其他税费」里还有另一项待申报），继续留在当前卡片；
 * 2. 否则从当前卡片往后（并环绕到前面）顺次寻找第一张 pending 的申报卡片；
 * 3. 若全部 4 张申报卡均已办结/豁免，返回 null。
 */
export function resolveNextPendingCard(
  pkg: FilingCardPackageState | null | undefined,
  currentCard: FilingCardKey,
): FilingCardKey | null {
  if (!pkg) return null

  if (currentCard !== 'overview' && isFilingCardPending(pkg, currentCard)) {
    return currentCard
  }

  const taskOrder = FILING_TASK_CARD_ORDER
  const currentIdx = taskOrder.indexOf(currentCard)
  const searchSequence = currentIdx === -1
    ? taskOrder
    : [...taskOrder.slice(currentIdx + 1), ...taskOrder.slice(0, currentIdx)]

  for (const key of searchSequence) {
    if (isFilingCardPending(pkg, key)) {
      return key
    }
  }

  return null
}
