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
 * 第三方制作的新版电子税务局仿真教学（浙江/杭州版）。
 * 仅作外链参考：内容与截图可能随税局改版滞后，界面以税局实际为准。
 */
export const VAT_DECLARATION_DEMO_URL = 'https://h5.hzdykj.cn/8769873297410287/vat_declare_b?v=9'

/** 电子税务局新版表单的两种入口形态：确认式（本期销售情况）/ 填表式（报表列表） */
export type VatFilingMode = 'confirm' | 'form'

export interface VatGuideAmountRow {
  label: string
  amount: number
  copyLabel: string
}

export interface VatGuideStep {
  title: string
  detail: string
  amounts?: VatGuideAmountRow[]
}

export interface VatFilingGuideData {
  periodType?: string
  totalSales?: number
  goodsLaborSales?: number
  serviceSales?: number
  isBelowThreshold?: boolean
  vatPayable?: number
  vatReduction?: number
  surchargeTotal?: number
  uninvoicedPositiveSales?: number
  uninvoicedNegativeSales?: number
  invoiceTotal?: number
}

export interface VatFilingGuide {
  steps: VatGuideStep[]
  warnings: string[]
}

function guideMoney(value: number | undefined | null): string {
  return (Number(value) || 0).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

/**
 * 有未开票收入（无论正数还是冲减）时，新版税局通常要走填表式：
 * 未开票金额只能在附列资料（一）附1 补录，确认式页面不一定提供入口。
 */
export function recommendVatFilingMode(data: VatFilingGuideData): VatFilingMode {
  const positive = Number(data.uninvoicedPositiveSales || 0)
  const negative = Number(data.uninvoicedNegativeSales || 0)
  return positive > 0 || negative < 0 ? 'form' : 'confirm'
}

/**
 * 小规模增值税「快速抄报手卡」：按本期数据生成分步填报路径。
 *
 * 两种入口共用同一张申报表，金额口径一致，只是填写位置不同：
 * - confirm：确认式页面，在「本期销售情况」弹窗按 1% 征收率行填报；
 * - form：填表式页面，走《业务信息表》→ 附列资料（一）附1（未开票收入）→
 *   附列资料（一）/减免税明细表 → 主表 → 附加税费。
 * 手卡只做指引，不提交申报；最终确认权始终在用户。
 */
export function buildVatFilingGuide(
  data: VatFilingGuideData,
  mode: VatFilingMode = 'confirm',
): VatFilingGuide {
  const totalSales = Number(data.totalSales || 0)
  const goodsSales = Number(data.goodsLaborSales || 0)
  const serviceSales = Number(data.serviceSales || 0)
  const vatPayable = Number(data.vatPayable || 0)
  const vatReduction = Number(data.vatReduction || 0)
  const surchargeTotal = Number(data.surchargeTotal || 0)
  const uninvoicedPositive = Number(data.uninvoicedPositiveSales || 0)
  const uninvoicedNegative = Number(data.uninvoicedNegativeSales || 0)
  const hasUninvoiced = uninvoicedPositive > 0 || uninvoicedNegative < 0
  const isBelowThreshold = data.isBelowThreshold !== false
  const isMonth = data.periodType === 'month'
  const periodText = isMonth ? '本月' : '本季'
  const thresholdText = isMonth ? '10 万元' : '30 万元'

  if (totalSales < 0) {
    // 负数总额通常来自跨期退款/红字发票，未核对清楚就抄报会触发税局比对异常
    return {
      steps: [{
        title: '本期销售额为负数，先核对再申报',
        detail: '负数通常来自跨期退款、红字发票或冲销凭证。请先回到账务/发票确认对应的原销售，再按填表式在附列资料（一）附1 填负数冲减并补「被冲减属期」；核对不清时不要提交申报。',
        amounts: [{ label: '本期账载销售额', amount: totalSales, copyLabel: '本期账载销售额' }],
      }],
      warnings: ['负数冲减必须能对应到前期已申报的正数销售额；系统提示「不存在有效申报记录」时先核对属期，别反复强制提交。'],
    }
  }

  if (totalSales === 0 && !hasUninvoiced) {
    if (mode === 'form') {
      return {
        steps: [
          {
            title: '业务信息表无需勾选业务标签',
            detail: '本期账载销售额为 0，新版表单的《业务信息表》按系统预填核对即可，直接点【确定】进入表单。',
          },
          {
            title: '主表全 0，提交零申报',
            detail: '主表销售额与「本期应补（退）税额」均应为 ¥0.00。点【提交申报】，在「信息确认」界面依次点击「真、实、责、任」后确定。',
          },
        ],
        warnings: [],
      }
    }
    return {
      steps: [
        { title: '无需填报销售额', detail: '本期账载销售额为 0，申报页各销售额入口保持 0 即可。' },
        { title: '直接「下一步」完成零申报', detail: '核对「本期应补（退）税额」为 ¥0.00 后，点「下一步」提交申报。' },
      ],
      warnings: [],
    }
  }

  if (mode === 'form') {
    return buildFormModeGuide({
      totalSales,
      goodsSales,
      serviceSales,
      vatPayable,
      vatReduction,
      surchargeTotal,
      uninvoicedPositive,
      uninvoicedNegative,
      hasUninvoiced,
      isBelowThreshold,
      periodText,
      thresholdText,
      invoiceTotal: Number(data.invoiceTotal ?? -1),
    })
  }

  const steps: VatGuideStep[] = []
  const warnings: string[] = []

  if (isBelowThreshold) {
    const dialogRows: VatGuideAmountRow[] = []
    if (goodsSales > 0) {
      dialogRows.push({ label: '「货物及劳务」弹窗 · 1%征收率行', amount: goodsSales, copyLabel: '本期销售额·货物及劳务' })
    }
    if (serviceSales > 0) {
      dialogRows.push({ label: '「服务、不动产和无形资产」弹窗 · 1%征收率行', amount: serviceSales, copyLabel: '本期销售额·服务、不动产和无形资产' })
    }
    steps.push(
      {
        title: '「本期销售情况」→ 点各销售额入口右侧编辑',
        detail: `在弹窗「1%征收率」行填入下方金额（本卡已按不含税口径算好）；${periodText}销售额未超 ${thresholdText}，「免税」行只读不用管，系统会自动归集为「免税销售额」。`,
        amounts: dialogRows,
      },
      {
        title: '「预览表单」核对归集结果',
        detail: '主表第 9/10 栏「免税销售额」应等于你填的金额（本年累计自动累加），第 1 栏「应征增值税不含税销售额」应保持 0.00。',
      },
      {
        title: '核对税额并申报',
        detail: `「本期应补（退）税额」应为 ¥${guideMoney(vatPayable)}，确认后点「下一步」完成申报；附加税费同步为 0，无需处理。`,
      },
    )
    warnings.push(
      '若预览表单中金额落在第 1 栏「应征增值税不含税销售额」（应税行）而非第 9/10 栏，点右上角「我要填表」进报表，直接改第 10 栏「其中：小微企业免税销售额」（第 9 栏自动合计，第 19/20 栏免税额自动带出）。',
    )
  } else {
    const goodsRate1 = goodsSales
    const serviceRate1 = serviceSales
    if (goodsRate1 > 0) {
      steps.push({
        title: '「本期销售情况」→ 点「货物及劳务销售额」右侧编辑',
        detail: '在弹窗「1%征收率」行填入下方金额（本卡已按不含税口径算好）；3%/5% 及其中项保持 0。',
        amounts: [{ label: '1%征收率行', amount: goodsRate1, copyLabel: '1%征收率·货物及劳务' }],
      })
    }
    if (serviceRate1 > 0) {
      steps.push({
        title: '→ 点「服务、不动产和无形资产销售额」右侧编辑',
        detail: '在弹窗「1%征收率」行填入下方金额（本卡已按不含税口径算好）；其余行保持 0。',
        amounts: [{ label: '1%征收率行', amount: serviceRate1, copyLabel: '1%征收率·服务、不动产和无形资产' }],
      })
    }
    steps.push({
      title: '核对税额并申报扣款',
      detail: `弹窗确定后「本期应补（退）税额」应为 ¥${guideMoney(vatPayable)}，点「下一步」完成申报并及时缴款；附加税费随征自动计算。`,
    })
  }

  if (hasUninvoiced) {
    warnings.push(
      uninvoicedNegative < 0
        ? `本期有未开票负数冲减 ¥${guideMoney(uninvoicedNegative)}。确认式通常填不了「被冲减属期」，请切到上方「填表式」手卡，在附列资料（一）附1 填报。`
        : `本期有未开票收入 ¥${guideMoney(uninvoicedPositive)}。确认式页面若没有补录入口，请切到上方「填表式」手卡，在附列资料（一）附1 填报；两种入口只能报一次。`,
    )
  }
  if (data.invoiceTotal === 0) {
    warnings.push('本期发票开具为 ¥0.00，税局「自动归集发票」预填会是 0.00——这不是数据没同步，按上述步骤手动填报即可，否则会漏报销售收入。')
  }

  return { steps, warnings }
}

function buildFormModeGuide(input: {
  totalSales: number
  goodsSales: number
  serviceSales: number
  vatPayable: number
  vatReduction: number
  surchargeTotal: number
  uninvoicedPositive: number
  uninvoicedNegative: number
  hasUninvoiced: boolean
  isBelowThreshold: boolean
  periodText: string
  thresholdText: string
  invoiceTotal: number
}): VatFilingGuide {
  const {
    totalSales, goodsSales, serviceSales, vatPayable, vatReduction, surchargeTotal,
    uninvoicedPositive, uninvoicedNegative, hasUninvoiced, isBelowThreshold,
    periodText, thresholdText, invoiceTotal,
  } = input
  const steps: VatGuideStep[] = []
  const warnings: string[] = []

  steps.push({
    title: '先确认入口：左侧有「报表列表」就是填表式',
    detail: '从【我要办税】→【税费申报及缴纳】→【增值税及附加税费申报（小规模纳税人）】进入。左上角显示「报表列表」、左侧能点开附列资料，就是填表式；只有「本期销售情况」入口的，请切回确认式手卡。',
  })

  steps.push({
    title: '业务信息表：先勾业务标签，再进表单',
    detail: hasUninvoiced
      ? '新版表单先过《业务信息表》。在「105 业务类型」勾选「未开具发票销售业务」并点【确定】，核对「申报表展示结果」后再点【确定】进入报表；有差额征税/简易计税的再勾对应标签。'
      : '新版表单先过《业务信息表》。本期没有未开票收入、扣除项目，按系统预填核对「申报表展示结果」后直接点【确定】进入报表。',
  })

  if (hasUninvoiced) {
    const amounts: VatGuideAmountRow[] = []
    if (uninvoicedPositive > 0) {
      amounts.push({ label: '附1 第 2 列 · 本期正数销售额', amount: uninvoicedPositive, copyLabel: '附1 未开票正数销售额' })
    }
    if (uninvoicedNegative < 0) {
      amounts.push({ label: '附1 第 4 列 · 本期负数销售额', amount: uninvoicedNegative, copyLabel: '附1 未开票负数销售额' })
    }
    let detail = '左侧点开「附列资料（一）附1 未开具发票销售情况明细」，税率（征收率）选「3%征收率减按1%」，第 2 列「本期正数销售额」填未开票收入，第 3 列税额系统自动带出。'
    if (uninvoicedNegative < 0) {
      detail += `本期还有负数冲减 ¥${guideMoney(uninvoicedNegative)}：第 4 列填负数，第 6、7 列选「被冲减的前期申报正数销售额属期起/止」；原属期享受过免税的，系统会提示改到「免征」行——增行、税率选「免征」、只填负数销售额。`
    }
    steps.push({
      title: '附列资料（一）附1：填未开票收入',
      detail,
      amounts,
    })
  }

  if (!isBelowThreshold) {
    steps.push({
      title: '附列资料（一）：核对开票与未开票合计',
      detail: '系统按开票数据自动预填，加上附1 的未开票金额后，「销售额合计」应等于本卡金额；有差异先回附1 核对，不要直接改合计。',
      amounts: [{ label: '附列资料（一）销售额合计', amount: totalSales, copyLabel: '附列资料（一）销售额合计' }],
    })
    if (vatReduction > 0) {
      steps.push({
        title: '减免税申报明细表：确认减按 1% 减税额',
        detail: '打开《增值税减免税申报明细表》→「减税项目」，选择系统预填的「小规模纳税人减按1%征收率征收增值税」，本期减税额填下方金额；没有预填就按 3%减按1% 手工补填。',
        amounts: [{ label: '本期减税额', amount: vatReduction, copyLabel: '减免税明细表本期减税额' }],
      })
    }
  }

  if (isBelowThreshold) {
    steps.push({
      title: '主表：确认免税销售额落在第 10 栏',
      detail: `主表销售额由系统归集。${periodText}销售额未超 ${thresholdText}，第 10 栏「其中：小微企业免税销售额」应等于本卡金额，第 1 栏「应征增值税不含税销售额」保持 0.00，第 22 栏「本期应补（退）税额」为 0.00。`,
      amounts: [{ label: '主表第 10 栏 · 小微企业免税销售额', amount: totalSales, copyLabel: '主表第10栏免税销售额' }],
    })
  } else {
    const amounts: VatGuideAmountRow[] = [
      { label: '主表第 1 栏 · 应征增值税不含税销售额', amount: totalSales, copyLabel: '主表第1栏销售额' },
    ]
    if (goodsSales > 0) amounts.push({ label: '其中：货物及劳务', amount: goodsSales, copyLabel: '主表货物及劳务销售额' })
    if (serviceSales > 0) amounts.push({ label: '其中：服务、不动产和无形资产', amount: serviceSales, copyLabel: '主表服务销售额' })
    amounts.push({ label: '主表第 22 栏 · 本期应补（退）税额', amount: vatPayable, copyLabel: '主表第22栏应补退税额' })
    steps.push({
      title: '主表：核对应税销售额与应补税额',
      detail: '主表「销售额」「税款计算」由附列资料自动归集：第 1 栏应等于本卡销售额，第 22 栏应等于本卡应缴税额；不一致先回附列资料查，不要在主表硬改。',
      amounts,
    })
  }

  steps.push({
    title: '附列资料（三）：核对附加税费',
    detail: isBelowThreshold
      ? '本期增值税为 0，城建税、教育费附加、地方教育附加三项都应为 0.00，无需手工填写。'
      : `城建税、教育费附加、地方教育附加由系统按增值税额自动计算，三项合计应为本卡金额（¥${guideMoney(surchargeTotal)}）。`,
    amounts: !isBelowThreshold && surchargeTotal > 0
      ? [{ label: '附加税费合计', amount: surchargeTotal, copyLabel: '附加税费合计' }]
      : undefined,
  })

  steps.push({
    title: '提交申报并确认真实责任',
    detail: '回到主表点【提交申报】，在「信息确认」界面依次点击「真、实、责、任」，再点【确定】。申报成功后如本期有应缴税额，请在征期内完成缴款。',
  })

  if (isBelowThreshold) {
    warnings.push('第 10 栏有金额、第 1 栏为 0 才是正确归集；若第 1 栏出现金额，先回《业务信息表》检查「107 适用政策类型」的免税标签，或用「调整业务信息表」重选，不要直接删数。')
  }
  if (hasUninvoiced) {
    warnings.push('未开票收入必须先在《业务信息表》勾选「105 业务类型 · 未开具发票销售业务」，否则附1 不显示，容易漏报。')
  }
  if (uninvoicedNegative < 0) {
    warnings.push('负数冲减必须填被冲减属期，且不能超过该属期已申报的未开票余额；系统提示「不存在有效申报记录」时先核对属期，别反复强制提交。')
  }
  if (vatReduction > 0) {
    warnings.push(`减免税明细表的本期减税额应为 ¥${guideMoney(vatReduction)}；主表第 22 栏应仍为 ¥${guideMoney(vatPayable)}，两者对不上就是减税项目没填对。`)
  }
  if (invoiceTotal === 0) {
    warnings.push('本期发票开具为 ¥0.00，税局「自动归集发票」预填会是 0.00——这不是数据没同步，按上述步骤手动填报即可，否则会漏报销售收入。')
  }
  warnings.push('确认式和填表式是同一张申报表的两个入口，金额口径一致，只能提交一次；不要在两边各报一遍。')

  return { steps, warnings }
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
}, mode: VatFilingMode = 'confirm'): TaxCopilotInsight {
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
      pathAdvice: mode === 'form'
        ? '进入申报后若左侧是「报表列表」：在《业务信息表》核对免税相关标签，未开票收入记入附列资料（一）附1，主表确认第 10 栏免税销售额 = 本期销售额、第 1 栏为 0.00、第 22 栏应补（退）税额为 0.00。'
        : '在「本期销售情况」对应入口弹窗的「1%征收率」行照实填写销售额即可（季度未超 30 万，税局会自动归集为「免税销售额」），用「预览表单」核对第 9/10 栏后下一步申报；弹窗里的 1% 行不是报表第 1 栏，放心填。',
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
    pathAdvice: mode === 'form'
      ? '填表式：业务信息表勾「未开具发票销售业务」（如有未开票收入），附1 填未开票销售额，减免税明细表确认减按1%减税额，主表第 1 栏与第 22 栏核对后提交。'
      : '在「本期销售情况」各入口的编辑弹窗「1%征收率」行填入不含税销售额（系统已换算），核对「本期应补（退）税额」后申报并及时扣款。',
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
