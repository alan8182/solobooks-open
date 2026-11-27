/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司.
 * Licensed under the SoloBooks License v2.0.
 */

export type LaborTaxMode = 'gross_to_net' | 'net_to_gross'

export interface LaborTaxOptions {
  /** 是否属于 2026 年第 28 号公告规定的七类服务（研发/软件/设计/咨询/影视/文化/教育） */
  isSevenServices: boolean
  /** 自然人增值税征收率（1% 减按 或 3%） */
  vatRate: 0.01 | 0.03
  /** 城建税税率（市区 7% / 县镇 5% / 其他 1%） */
  urbanRate: 0.07 | 0.05 | 0.01
  /** 小微/自然人六税两费减半征收 */
  halfSurcharge: boolean
}

export interface LaborTaxResult {
  mode: LaborTaxMode
  grossAmount: number
  exclVatAmount: number
  vatOverThreshold: boolean
  vatAmount: number
  surchargeAmount: number
  iitBase: number
  iitAmount: number
  iitBracket: string
  totalTax: number
  netAmount: number
  effectiveTaxRate: number
  actionSummary: string
}

export const DEFAULT_LABOR_TAX_OPTIONS: LaborTaxOptions = {
  isSevenServices: true,
  vatRate: 0.01,
  urbanRate: 0.07,
  halfSurcharge: true,
}

export function round2(n: number): number {
  if (!Number.isFinite(n)) return 0
  return Math.round((n + Number.EPSILON) * 100) / 100
}

function resolveOptions(opts?: Partial<LaborTaxOptions>): LaborTaxOptions {
  return {
    isSevenServices: opts?.isSevenServices ?? DEFAULT_LABOR_TAX_OPTIONS.isSevenServices,
    vatRate: opts?.vatRate ?? DEFAULT_LABOR_TAX_OPTIONS.vatRate,
    urbanRate: opts?.urbanRate ?? DEFAULT_LABOR_TAX_OPTIONS.urbanRate,
    halfSurcharge: opts?.halfSurcharge ?? DEFAULT_LABOR_TAX_OPTIONS.halfSurcharge,
  }
}

function computeLaborIit(iitBase: number): { iit: number; bracket: string } {
  if (iitBase <= 800) {
    return { iit: 0, bracket: '800 元以下（免预扣）' }
  }
  if (iitBase <= 4000) {
    return {
      iit: round2((iitBase - 800) * 0.2),
      bracket: '800–4,000 元（减 800 后按 20%）',
    }
  }
  const taxable = round2(iitBase * 0.8)
  if (taxable <= 20000) {
    return {
      iit: round2(taxable * 0.2),
      bracket: '应纳税所得额 ≤ 2 万（按 20%）',
    }
  }
  if (taxable <= 50000) {
    return {
      iit: round2(taxable * 0.3 - 2000),
      bracket: '应纳税所得额 2–5 万（按 30% 减 2,000）',
    }
  }
  return {
    iit: round2(taxable * 0.4 - 7000),
    bracket: '应纳税所得额 > 5 万（按 40% 减 7,000）',
  }
}

function buildActionSummary(
  grossAmount: number,
  netAmount: number,
  vatAmount: number,
  surchargeAmount: number,
  iitAmount: number,
  vatOverThreshold: boolean,
  options: LaborTaxOptions,
): string {
  if (grossAmount <= 0) {
    return '请输入付款或到手金额以查看代扣明细与申报指引。'
  }
  const fmt = (v: number) => `¥${v.toFixed(2)}`
  if (!options.isSevenServices) {
    if (iitAmount <= 0) {
      return `非七类服务且单次 ≤ 800 元：实际打款 ${fmt(netAmount)}，次月 15 日前在扣缴端做个税零申报即可。`
    }
    return `非七类服务无需代扣增值税：先扣个税 ${fmt(iitAmount)}，实际打款 ${fmt(netAmount)}，次月 15 日前完成个税扣缴申报。`
  }
  if (!vatOverThreshold && iitAmount <= 0) {
    return `未达起征点：增值税与个税均无需扣款，实际打款 ${fmt(netAmount)}；次月 15 日前做个税与增值税零/免税申报后由对方代开发票。`
  }
  if (!vatOverThreshold) {
    return `增值税未达 1,000 元起征点免征（做零/免税申报）；需先扣个税 ${fmt(iitAmount)}，实际打款 ${fmt(netAmount)}，次月 15 日前完成申报。`
  }
  const vatTotal = round2(vatAmount + surchargeAmount)
  return `已达 1,000 元起征点：先扣增值税及附加 ${fmt(vatTotal)} 与个税 ${fmt(iitAmount)}，实际打款 ${fmt(netAmount)}；次月 15 日前完成双税扣缴申报后再通知对方代开发票。`
}

/**
 * 税前正算：已知约定含税金额，计算代扣增值税及附加、劳务报酬个税与实际应打款净额。
 */
export function calcLaborTaxFromGross(
  grossAmount: number,
  opts?: Partial<LaborTaxOptions>,
  mode: LaborTaxMode = 'gross_to_net',
): LaborTaxResult {
  const options = resolveOptions(opts)
  const gross = Math.max(0, round2(Number(grossAmount) || 0))

  let exclVat = gross
  let vatOverThreshold = false
  let vat = 0
  let surcharge = 0

  if (options.isSevenServices && gross > 0) {
    exclVat = round2(gross / (1 + options.vatRate))
    vatOverThreshold = exclVat >= 1000
    if (vatOverThreshold) {
      vat = round2(exclVat * options.vatRate)
      // 教育费附加 3% + 地方教育附加 2% = 5%
      const surchargeRate = (options.urbanRate + 0.05) * (options.halfSurcharge ? 0.5 : 1)
      surcharge = round2(vat * surchargeRate)
    }
  }

  const iitBase = vatOverThreshold ? exclVat : gross
  const { iit, bracket } = computeLaborIit(iitBase)
  const totalTax = round2(vat + surcharge + iit)
  const netAmount = round2(gross - totalTax)
  const effectiveTaxRate = gross > 0 ? round2((totalTax / gross) * 100) : 0

  return {
    mode,
    grossAmount: gross,
    exclVatAmount: exclVat,
    vatOverThreshold,
    vatAmount: vat,
    surchargeAmount: surcharge,
    iitBase,
    iitAmount: iit,
    iitBracket: bracket,
    totalTax,
    netAmount,
    effectiveTaxRate,
    actionSummary: buildActionSummary(
      gross,
      netAmount,
      vat,
      surcharge,
      iit,
      vatOverThreshold,
      options,
    ),
  }
}

function binarySearchGross(
  targetNet: number,
  loCents: number,
  hiCents: number,
  options: LaborTaxOptions,
): LaborTaxResult | null {
  let lo = loCents
  let hi = hiCents
  let best: LaborTaxResult | null = null
  while (lo <= hi) {
    const mid = Math.floor((lo + hi) / 2)
    const res = calcLaborTaxFromGross(mid / 100, options, 'net_to_gross')
    if (res.netAmount >= targetNet) {
      best = res
      hi = mid - 1
    } else {
      lo = mid + 1
    }
  }
  return best
}

/**
 * 税后反算：已知承诺给个人的到手净额，反算公司实际需承担的含税总支出及各项代扣税额。
 */
export function calcLaborTaxFromNet(
  targetNet: number,
  opts?: Partial<LaborTaxOptions>,
): LaborTaxResult {
  const options = resolveOptions(opts)
  const target = Math.max(0, round2(Number(targetNet) || 0))
  if (target <= 0) {
    return calcLaborTaxFromGross(0, options, 'net_to_gross')
  }

  const loCents = Math.round(target * 100)
  let hiCents = Math.max(loCents * 2 + 100000, 100000)
  while (calcLaborTaxFromGross(hiCents / 100, options, 'net_to_gross').netAmount < target) {
    hiCents *= 2
  }

  // 1,000 元不含税起征点附近存在税收悬崖，先分别在阈值下段和全区间检索后取最小总支出
  const candidates: LaborTaxResult[] = []
  if (options.isSevenServices) {
    const cliffUpper = Math.min(hiCents, Math.round(1000 * (1 + options.vatRate) * 100))
    if (cliffUpper >= loCents) {
      const below = binarySearchGross(target, loCents, cliffUpper, options)
      if (below) candidates.push(below)
    }
    const aboveStart = Math.max(loCents, Math.round(1000 * (1 + options.vatRate) * 100))
    if (hiCents >= aboveStart) {
      const above = binarySearchGross(target, aboveStart, hiCents, options)
      if (above) candidates.push(above)
    }
  } else {
    const res = binarySearchGross(target, loCents, hiCents, options)
    if (res) candidates.push(res)
  }

  if (candidates.length === 0) {
    return calcLaborTaxFromGross(target, options, 'net_to_gross')
  }
  candidates.sort((a, b) => a.grossAmount - b.grossAmount)
  return candidates[0]
}
