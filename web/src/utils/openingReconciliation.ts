/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 期初对账视图的派生数据（只读，不参与任何报表取数）。
 *
 * 科目余额表把年初余额、本年累计发生额、期末余额放在同一张表里；建账时
 * 「期末」就是系统的期初。三组数的关系是：
 *
 *   期初净额 = 年初净额 + 本年累计净额
 *
 * 系统只保存年初快照与损益累计，资产负债类的本年累计不落库——这里按
 * 「期初 − 年初」推算净变动，供财务与原表逐行核对。
 *
 * ponytail: 只推算净额。原表的「本年累计发生额」是借贷毛额（应收年内既有
 * 新增也有回款），两个时点数还原不出毛额；需要毛额请直接对照原科目余额表。
 */

export interface ReconciliationBalanceLike {
  account_code: string
  account_name?: string
  debit_balance?: number | string
  credit_balance?: number | string
}

export interface OpeningReconciliationRow {
  account_code: string
  account_name: string
  yearStartDebit: number
  yearStartCredit: number
  /** 本年净变动：借方（= 期初净额较年初净额的增加） */
  movementDebit: number
  /** 本年净变动：贷方 */
  movementCredit: number
  openingDebit: number
  openingCredit: number
  /** 年初与期初净额不一致，说明启用前该科目有变动 */
  hasMovement: boolean
}

export interface OpeningReconciliationTotals {
  yearStartDebit: number
  yearStartCredit: number
  movementDebit: number
  movementCredit: number
  openingDebit: number
  openingCredit: number
  /** 年初合计借方 − 贷方；两列平衡时为 0 */
  yearStartDiff: number
  /** 期初合计借方 − 贷方 */
  openingDiff: number
}

export interface OpeningReconciliation {
  rows: OpeningReconciliationRow[]
  totals: OpeningReconciliationTotals
  /** 年初数是否可信：年中建账且没有年初快照时为 false，页面应显示「—」而不是 0 */
  yearStartKnown: boolean
  /** 有变动的科目数 */
  movementCount: number
}

const round2 = (n: number) => Math.round((Number(n) || 0) * 100) / 100
const isZero = (n: number) => Math.abs(n) < 0.005

interface Bucket {
  name: string
  yearStartDebit: number
  yearStartCredit: number
  openingDebit: number
  openingCredit: number
}

function bucketFor(map: Map<string, Bucket>, code: string, name?: string): Bucket {
  let bucket = map.get(code)
  if (!bucket) {
    bucket = { name: '', yearStartDebit: 0, yearStartCredit: 0, openingDebit: 0, openingCredit: 0 }
    map.set(code, bucket)
  }
  if (!bucket.name && name) bucket.name = name
  return bucket
}

/**
 * 按科目汇总客商行后生成对账行。
 *
 * @param openingItems     系统期初余额（account + partner 维度）
 * @param yearStartItems   年初快照（account + partner 维度，导入时落库）
 * @param options.yearStartKnown 年中建账但快照缺失时传 false
 */
export function buildOpeningReconciliation(
  openingItems: ReconciliationBalanceLike[],
  yearStartItems: ReconciliationBalanceLike[],
  options: { yearStartKnown?: boolean } = {},
): OpeningReconciliation {
  const buckets = new Map<string, Bucket>()

  for (const item of openingItems || []) {
    const code = String(item?.account_code || '').trim()
    if (!code) continue
    const bucket = bucketFor(buckets, code, item.account_name)
    bucket.openingDebit += Number(item.debit_balance) || 0
    bucket.openingCredit += Number(item.credit_balance) || 0
  }

  for (const item of yearStartItems || []) {
    const code = String(item?.account_code || '').trim()
    if (!code) continue
    const bucket = bucketFor(buckets, code, item.account_name)
    bucket.yearStartDebit += Number(item.debit_balance) || 0
    bucket.yearStartCredit += Number(item.credit_balance) || 0
  }

  const rows: OpeningReconciliationRow[] = []
  let yearStartDebit = 0
  let yearStartCredit = 0
  let movementDebit = 0
  let movementCredit = 0
  let openingDebit = 0
  let openingCredit = 0
  let movementCount = 0

  for (const [code, bucket] of buckets) {
    const ysD = round2(bucket.yearStartDebit)
    const ysC = round2(bucket.yearStartCredit)
    const opD = round2(bucket.openingDebit)
    const opC = round2(bucket.openingCredit)
    // 科目表里大量零余额科目只用于录入，不为它们生成对账行
    if (isZero(ysD) && isZero(ysC) && isZero(opD) && isZero(opC)) continue
    const movementNet = round2((opD - opC) - (ysD - ysC))
    const movementRowDebit = movementNet > 0 ? movementNet : 0
    const movementRowCredit = movementNet < 0 ? -movementNet : 0
    const hasMovement = Math.abs(movementNet) >= 0.005

    if (hasMovement) movementCount += 1
    rows.push({
      account_code: code,
      account_name: bucket.name || code,
      yearStartDebit: ysD,
      yearStartCredit: ysC,
      movementDebit: movementRowDebit,
      movementCredit: movementRowCredit,
      openingDebit: opD,
      openingCredit: opC,
      hasMovement,
    })

    yearStartDebit += ysD
    yearStartCredit += ysC
    movementDebit += movementRowDebit
    movementCredit += movementRowCredit
    openingDebit += opD
    openingCredit += opC
  }

  rows.sort((a, b) => a.account_code.localeCompare(b.account_code))

  const totals: OpeningReconciliationTotals = {
    yearStartDebit: round2(yearStartDebit),
    yearStartCredit: round2(yearStartCredit),
    movementDebit: round2(movementDebit),
    movementCredit: round2(movementCredit),
    openingDebit: round2(openingDebit),
    openingCredit: round2(openingCredit),
    yearStartDiff: round2(yearStartDebit - yearStartCredit),
    openingDiff: round2(openingDebit - openingCredit),
  }

  return {
    rows,
    totals,
    yearStartKnown: options.yearStartKnown !== false,
    movementCount,
  }
}
