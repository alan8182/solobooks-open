/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 期初「把一批带客商的行并进科目表」的合并规则——只有这一份。
 *
 * 两个来源都要用它：期初工作台的 Excel 解析结果（parseOpeningWorkbook）与
 * AI CFO 对话卡片交接的草稿（sessionStorage `solobooks:ai_opening_draft`）。
 * 它们此前各写了一份，且规则不同：工作台按「科目+客商」拆行，草稿那份按
 * `Map(account_code)` 合并 —— 同一科目下多个往来单位只留最后一条，其余静默
 * 丢弃。实测一张 23 行的期初卡经草稿通道只剩 12 行，丢的 11 行全是往来单位
 * （1122 ×2、1123 ×3、2202 ×7、2241 ×3），正好把「欠谁钱、哪些款没收回来」
 * 抹掉。规则收敛到这里，避免再分叉。
 */

export interface OpeningRowLike {
  account_code: string
  account_name?: string
  debit_balance: number | string
  credit_balance: number | string
  partner_name?: string
  /** 文件里的原始科目编码（如 1122001），仅用于核对展示，不参与入账 */
  originalCode?: string
}

/** 行的唯一键：同科目多客商各自成行，无客商行按空客商归一条 */
export function openingRowKey(accountCode: string, partnerName?: string): string {
  return `${accountCode}\u0000${partnerName || ''}`
}

function normalizeIncoming(row: OpeningRowLike) {
  return {
    account_code: String(row.account_code || ''),
    account_name: String(row.account_name || ''),
    debit_balance: parseFloat(String(row.debit_balance ?? 0)) || 0,
    credit_balance: parseFloat(String(row.credit_balance ?? 0)) || 0,
    partner_name: String(row.partner_name || ''),
    originalCode: String(row.originalCode || ''),
  }
}

/**
 * 把 incoming 并进 existing（通常是后端返回的整张科目表，多为空行）。
 *
 * 规则：
 * - 某科目若有带客商的 incoming 行，其无客商的原有行写回无客商金额，但若该金额
 *   与客商行净额相等（= 纯汇总），清零，避免同一金额算两遍；
 * - 无客商的 incoming 行回填到该科目原有行；
 * - 带客商的行按「科目+客商」追加为新行；已存在则更新金额。
 */
export function mergeOpeningRows<T extends OpeningRowLike>(
  existing: T[],
  incoming: OpeningRowLike[],
): T[] {
  const rows = (incoming || []).map(normalizeIncoming).filter((r) => r.account_code)
  const partnerRows = rows.filter((r) => r.partner_name)
  const plainMap = new Map(rows.filter((r) => !r.partner_name).map((r) => [r.account_code, r]))
  const partnerCovered = new Set(partnerRows.map((r) => r.account_code))

  // 客商行净额：判断无客商行是不是「同一笔的纯汇总」用
  const partnerNet = new Map<string, number>()
  for (const row of partnerRows) {
    const net = Math.round((row.debit_balance - row.credit_balance) * 100) / 100
    partnerNet.set(
      row.account_code,
      Math.round(((partnerNet.get(row.account_code) || 0) + net) * 100) / 100,
    )
  }

  const merged: T[] = existing.map((item) => {
    // 已有的客商行是明细本身，不是汇总，不能动：incoming 没提到的单位
    // （比如分两次导入）应当原样保留，而不是被静默抹成 0。
    if (item.partner_name) return item
    const hit = plainMap.get(item.account_code)
    if (partnerCovered.has(item.account_code)) {
      // 该科目拆出了客商行。无客商行净额与客商行净额相等 → 它是纯汇总，清零避免
      // 算两遍；不相等 → 是不挂单位的明细（如 2241 下公积金），必须保留，否则
      // 页面合计比导入弹窗少这一截。
      const hitNet = hit
        ? Math.round((hit.debit_balance - hit.credit_balance) * 100) / 100
        : 0
      const coveredNet = partnerNet.get(item.account_code) || 0
      if (hit && Math.abs(hitNet - coveredNet) >= 0.005) {
        return {
          ...item,
          debit_balance: hit.debit_balance,
          credit_balance: hit.credit_balance,
          originalCode: hit.originalCode || '',
        }
      }
      return { ...item, debit_balance: 0, credit_balance: 0 }
    }
    if (hit && (hit.debit_balance > 0 || hit.credit_balance > 0)) {
      return {
        ...item,
        debit_balance: hit.debit_balance,
        credit_balance: hit.credit_balance,
        originalCode: hit.originalCode || '',
      }
    }
    return item
  })

  // 客商行可能是系统科目表里没有的名字，追加为新行（同科目可多行）
  const seen = new Set(
    merged
      .filter((i) => i.partner_name)
      .map((i) => openingRowKey(i.account_code, i.partner_name)),
  )
  for (const row of partnerRows) {
    if (row.debit_balance === 0 && row.credit_balance === 0) continue
    const key = openingRowKey(row.account_code, row.partner_name)
    if (seen.has(key)) {
      const hit = merged.find(
        (i) => i.account_code === row.account_code && i.partner_name === row.partner_name,
      )
      if (hit) {
        hit.debit_balance = row.debit_balance
        hit.credit_balance = row.credit_balance
        hit.originalCode = row.originalCode
      }
      continue
    }
    merged.push({
      account_code: row.account_code,
      account_name: row.account_name,
      debit_balance: row.debit_balance,
      credit_balance: row.credit_balance,
      partner_name: row.partner_name,
      originalCode: row.originalCode,
    } as unknown as T)
    seen.add(key)
  }

  // 系统科目表里没有的无客商行也不能静默丢：金额会凭空消失、合计对不上。
  // 正常情况下解析器只产出表内科目，走到这里是科目表在解析与合并之间变了。
  const knownCodes = new Set(merged.map((i) => i.account_code))
  for (const row of rows) {
    if (row.partner_name || knownCodes.has(row.account_code)) continue
    if (row.debit_balance === 0 && row.credit_balance === 0) continue
    merged.push({ ...row } as unknown as T)
    knownCodes.add(row.account_code)
  }

  return merged
}

/**
 * 年中损益本年累计发生额：损益科目不挂客商，按科目一条即可。
 * 与 mergeOpeningRows 分开，是因为这里**不能**拆行——拆了本年利润就重复计。
 */
export function mergeYtdRows<T extends OpeningRowLike>(
  existing: T[],
  incoming: OpeningRowLike[],
): { rows: T[]; matchedCount: number } {
  const rows = (incoming || []).map(normalizeIncoming).filter((r) => r.account_code)
  const byCode = new Map(rows.map((r) => [r.account_code, r]))
  let matchedCount = 0
  const merged: T[] = existing.map((item) => {
    const hit = byCode.get(item.account_code)
    if (hit && (hit.debit_balance > 0 || hit.credit_balance > 0)) {
      matchedCount++
      return {
        ...item,
        debit_balance: hit.debit_balance,
        credit_balance: hit.credit_balance,
        originalCode: hit.originalCode || '',
      }
    }
    return item
  })
  return { rows: merged, matchedCount }
}
