/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 期初科目余额导入与对齐工具
 * ponytail: 纯前端无依赖解析函数，基于 SheetJS，支持科目别名、客商核算及年中损益累计识别
 */
import * as XLSX from 'xlsx'
import { saveWorkbook } from '@/utils/export'

export interface MatchedOpeningItem {
  account_code: string
  account_name: string
  debit_balance: number
  credit_balance: number
  partner_name?: string
  matched: boolean
  is_expense_revenue?: boolean
  originalCode?: string
  originalName?: string
  warning?: string
}

export interface FixedAssetImportItem {
  name: string
  category?: string
  purchase_date?: string
  cost: number
  accumulated_depreciation: number
  useful_months?: number
  salvage_rate?: number
}

export interface UnmatchedOpeningRow {
  originalCode: string
  originalName: string
  debit: number
  credit: number
  /** 是否属于资产负债类（损益类未匹配只影响累计发生额，不影响期初平衡） */
  affects_balance?: boolean
}

export interface YearStartBalanceItem {
  account_code: string
  account_name?: string
  /** 客商维度：报表按「科目 + 客商」方向重分类往来科目时要靠它 */
  partner_name?: string
  debit_balance: number
  credit_balance: number
}

export interface ParseOpeningResult {
  items: MatchedOpeningItem[]
  ytdItems: MatchedOpeningItem[]
  fixedAssets: FixedAssetImportItem[]
  totalDebit: number
  totalCredit: number
  difference: number
  isBalanced: boolean
  matchedCount: number
  unmatchedCount: number
  warnings: string[]
  unmatchedRows?: UnmatchedOpeningRow[]
  rawRows?: Record<string, unknown>[]
  /** 表头识别出的报表期间；识别不到为 null，由调用方决定是否让用户手选 */
  period?: StatementPeriod | null
  /**
   * 年初（1 月 1 日）余额：取文件「年初余额」列，没有就用「期末 − 本年累计」推算。
   * 报表「年初余额」列用它，不能用期初余额顶替（年中建账两者不是一回事）。
   * 文件既无年初列也无本年累计列时为 undefined——宁可留空，不给错的年初数。
   */
  yearStartItems?: YearStartBalanceItem[]
  /** 年初数的来源：explicit=文件年初列，derived=期末减本年累计 */
  yearStartSource?: 'explicit' | 'derived' | null
}

/**
 * 损益/成本类判定——与后端 `services/account_classification.is_profit_loss_code`
 * 同口径（5xxx 全段损益；4301 费用化明细损益，4301002 资本化支出为资产）。
 *
 * 曾经这里是硬编码枚举，漏掉明细科目（5001001 咨询服务）后收入被分进资产负债
 * 期初桶，而页面 `isBalanceAccount` 又按 `startsWith('5')` 把它滤掉，金额凭空
 * 消失、试算差额对不上。枚举永远追不上 COA 新增，改用前缀判定。
 */
export function isProfitLossCode(code: string): boolean {
  const text = String(code || '').trim()
  if (!text) return false
  if (text.startsWith('4301002')) return false
  if (text === '4301' || text.startsWith('4301001')) return true
  return text.startsWith('5')
}

/**
 * 可挂客商的往来科目前缀——与后端 `core/chart_policy.PARTNER_ACCOUNT_PREFIXES` 同口径。
 * 后端用前缀匹配（1122001 也允许挂客商），前端曾经是精确 Set.has，两边漂移过。
 */
const PARTNER_ACCOUNT_PREFIXES = ['1122', '1123', '2202', '2203', '1221', '2241']

export function isPartnerAccountCode(code: string): boolean {
  const text = String(code || '').trim()
  if (!text) return false
  return PARTNER_ACCOUNT_PREFIXES.some(prefix => text.startsWith(prefix))
}

/**
 * 非往来单位后缀：社保费/工资/公积金这类是费用或税费明细，不是「欠谁钱」的
 * 对手方。给它们建客商档案只会让账龄表多出一堆假单位。
 */
const NON_PARTNER_SUFFIX_KEYWORDS = [
  '社保', '社会保险', '工资', '薪酬', '公积金', '个人所得税', '个税',
  '待付款', '暂估', '待认证', '待抵扣', '进项税额', '销项税额', '其他',
]

/**
 * 从第三方余额表的子级科目名后缀切出往来单位名。
 *
 * 第三方导出把单位名拼在科目名里（「应收账款-梧桐树保险经纪有限公司」），
 * 而本系统把客商存成独立档案，所以要在这里把单位名切出来。切不出实体
 * （无分隔符、或命中非往来单位关键词）时返回空串——宁可不挂，也不要把
 * 「社保费」变成一张假档案（与后端 `link_opening_partners` 的克制一致）。
 */
export function partnerFromAccountName(name: string): string {
  const text = String(name || '').trim()
  if (!text) return ''
  // 分隔符：半角连字符 / 全角连字符 / 长破折号
  const idx = text.search(/[-－—]/)
  if (idx < 0) return ''
  const suffix = text.slice(idx + 1).trim()
  if (!suffix) return ''
  if (NON_PARTNER_SUFFIX_KEYWORDS.some(keyword => suffix.includes(keyword))) return ''
  return suffix
}

const CODE_ALIASES = ['科目编码', '科目代码', '科目编号', '代码', '编码', 'account_code', 'code']
const NAME_ALIASES = ['科目名称', '科目', '名称', 'account_name', 'name']
// 复合表头（两层表头拼接，如「期末余额」+「借方」→「期末余额借方」）排在最前，
// 防止泛化别名「借方」先命中「本期发生额借方」等错误列。
//
// 期末列排在期初列之前：期初建账要的是「建账起点」的时点余额，而第三方导出的
// 「1 月～5 月」余额表里，期初列是 1 月 1 日、期末列才是 5 月 31 日。旧顺序取期初，
// 资产负债期初会整体少掉前几个月的变动，且年初数本身借贷平衡，试算平衡看不出来。
const DEBIT_ALIASES = ['期末余额借方', '期末借方', '借方期末', '期末借方余额', '借方余额', '期初余额借方', '期初借方', '借方期初', '期初借方余额', '借方', 'debit', 'debit_balance']
const CREDIT_ALIASES = ['期末余额贷方', '期末贷方', '贷方期末', '期末贷方余额', '贷方余额', '期初余额贷方', '期初贷方', '贷方期初', '期初贷方余额', '贷方', 'credit', 'credit_balance']
const YTD_DEBIT_ALIASES = ['本年累计发生额借方', '本年累计借方', '借方累计', '本年借方累计', '累计借方', '借方发生额', '本期借方']
const YTD_CREDIT_ALIASES = ['本年累计发生额贷方', '本年累计贷方', '贷方累计', '本年贷方累计', '累计贷方', '贷方发生额', '本期贷方']
const YEAR_START_DEBIT_ALIASES = ['年初余额借方', '年初借方余额', '年初借方', '借方年初', '年初数借方', '上年结转借方']
const YEAR_START_CREDIT_ALIASES = ['年初余额贷方', '年初贷方余额', '年初贷方', '贷方年初', '年初数贷方', '上年结转贷方']
const DIRECTION_ALIASES = ['方向', '借贷方向', '借/贷', 'direction']
const BALANCE_ALIASES = ['期末余额', '期末', '期初余额', '年初余额', '余额', '金额', 'balance', 'amount']
const PARTNER_ALIASES = ['客商', '客商名称', '往来单位', '往来客户', '客户', '供应商', '核算项目', '辅助核算', 'partner', 'partner_name']

function findKey(keys: string[], aliases: string[]): string | undefined {
  const normalized = keys.map(k => ({ raw: k, clean: k.trim().toLowerCase() }))
  for (const alias of aliases) {
    const target = alias.toLowerCase()
    const found = normalized.find(n => n.clean === target || n.clean.includes(target))
    if (found) return found.raw
  }
  return undefined
}

function parseNumber(val: unknown): number {
  if (val == null) return 0
  if (typeof val === 'number') return isNaN(val) ? 0 : val
  const str = String(val).replace(/[,¥\s]/g, '')
  const n = parseFloat(str)
  return isNaN(n) ? 0 : n
}

/**
 * 解析从 Excel 转换而来的行数据，匹配到系统的科目体系中
 */
export function parseOpeningRows(
  rows: Record<string, unknown>[],
  allAccounts: { account_code: string; account_name?: string }[],
  mapping?: Record<string, string>,
): ParseOpeningResult {
  if (!rows || rows.length === 0) {
    return {
      items: [],
      ytdItems: [],
      fixedAssets: [],
      totalDebit: 0,
      totalCredit: 0,
      difference: 0,
      isBalanced: false,
      matchedCount: 0,
      unmatchedCount: 0,
      warnings: ['文件为空或无有效数据行'],
    }
  }

  // 聚合所有行中的键名，防止首行键稀疏
  const allKeys = Array.from(new Set(rows.flatMap(r => Object.keys(r))))

  const codeKey = findKey(allKeys, CODE_ALIASES)
  const nameKey = findKey(allKeys, NAME_ALIASES)
  const debitKey = findKey(allKeys, DEBIT_ALIASES)
  const creditKey = findKey(allKeys, CREDIT_ALIASES)
  const ytdDebitKey = findKey(allKeys, YTD_DEBIT_ALIASES)
  const ytdCreditKey = findKey(allKeys, YTD_CREDIT_ALIASES)
  const directionKey = findKey(allKeys, DIRECTION_ALIASES)
  const balanceKey = findKey(allKeys, BALANCE_ALIASES)
  const partnerKey = findKey(allKeys, PARTNER_ALIASES)
  const yearStartDebitKey = findKey(allKeys, YEAR_START_DEBIT_ALIASES)
  const yearStartCreditKey = findKey(allKeys, YEAR_START_CREDIT_ALIASES)
  // 只有「期末借贷列」不是年初列时，才能用「期末 − 本年累计」推算年初；
  // 若表里只有年初列（老模板），它本身就是年初，不能再减一次累计。
  const endingIsYearStartColumn = /年初|上年/.test(debitKey || '') || /年初|上年/.test(creditKey || '')

  const warnings: string[] = []
  if (!codeKey && !nameKey) {
    warnings.push('未能识别科目代码或科目名称列')
    return {
      items: [],
      ytdItems: [],
      fixedAssets: [],
      totalDebit: 0,
      totalCredit: 0,
      difference: 0,
      isBalanced: false,
      matchedCount: 0,
      unmatchedCount: rows.length,
      warnings,
    }
  }

  // 建立现有科目的快速索引
  const codeMap = new Map<string, { account_code: string; account_name: string }>()
  const nameMap = new Map<string, { account_code: string; account_name: string }>()
  for (const acc of allAccounts) {
    const code = String(acc.account_code).trim()
    const name = String(acc.account_name || '').trim()
    const item = { account_code: code, account_name: name }
    codeMap.set(code, item)
    if (name) nameMap.set(name, item)
  }

  // ── 文件内父子科目层级去重 ──────────────────────────────────────────
  // 第三方软件导出的余额表常是「上级科目合计行 + 下级明细行」（如 1002 银行存款
  // 与 1002004 银行存款-招商银行…）。上级行金额 = 下级行之和，两层都入账会双重
  // 计数（期初建账的金额事故）。规则：同一棵科目树下只保留一层——
  //   · 规则一：下级里有能匹配系统科目的 → 丢弃上级合计行，保留下级；
  //   · 规则二：上级自身能匹配系统科目、且无让位情形 → 保留上级，跳过全部下级；
  //   · 规则三：上级匹配不上系统、下级也没有能匹配的 → 若下级带非零金额，跳过
  //     上级聚合行、让下级明细行进映射列表逐个映射（无损拆分）；下级全为零则
  //     保留上级（聚合是唯一金额载体）。
  const rowCodes = rows.map(r => (codeKey ? String(r[codeKey] ?? '').trim() : ''))
  const rowNames = rows.map(r => (nameKey ? String(r[nameKey] ?? '').trim() : ''))
  const fileCodeSet = new Set(rowCodes.filter(Boolean))

  /** 行内借贷金额提取（主循环与层级预扫描共用同一套取数口径） */
  function rowDebitCredit(row: Record<string, unknown>, code: string): { debit: number; credit: number } {
    // 损益科目优先取「本年累计发生额」列：第三方表若不结转损益，期末余额 =
    // 年初 + 本年累计（非 0），先取期末列会把余额当成发生额。累计列存在但本行
    // 为 0 时再回退余额列（单表模板、无累计列的老格式）。
    if (isProfitLossCode(code) && (ytdDebitKey || ytdCreditKey)) {
      const ytdDebit = ytdDebitKey ? parseNumber(row[ytdDebitKey]) : 0
      const ytdCredit = ytdCreditKey ? parseNumber(row[ytdCreditKey]) : 0
      if (ytdDebit !== 0 || ytdCredit !== 0) {
        return { debit: ytdDebit, credit: ytdCredit }
      }
    }
    let debit = debitKey ? parseNumber(row[debitKey]) : 0
    let credit = creditKey ? parseNumber(row[creditKey]) : 0
    // 单列金额 + 方向模式
    if (balanceKey && directionKey && debit === 0 && credit === 0) {
      const dir = String(row[directionKey] ?? '').trim()
      const bal = parseNumber(row[balanceKey])
      if (dir.includes('借')) {
        debit = bal
      } else if (dir.includes('贷')) {
        credit = bal
      }
    }
    // 损益类科目的期初余额常为空，从「本年累计/发生额」列提取
    if (isProfitLossCode(code) && debit === 0 && credit === 0) {
      if (ytdDebitKey) debit = parseNumber(row[ytdDebitKey])
      if (ytdCreditKey) credit = parseNumber(row[ytdCreditKey])
    }
    return { debit, credit }
  }

  const round2 = (n: number) => Math.round(n * 100) / 100

  /**
   * 单行的年初（1 月 1 日）余额。损益科目年初恒为 0（年初还没结转），跳过。
   *
   * 优先文件「年初余额」列；没有就用 期末净额 − 本年累计净额 推算——这正是科目
   * 余额表的恒等式：期末 = 年初 + 本年累计。两个来源都没有时返回 null（unknown），
   * 调用方据此判定「年初数未知」，绝不能用期初余额顶替：年中建账时期初是建账
   * 前一月末的数，不是 1 月 1 日。
   */
  function rowYearStart(row: Record<string, unknown>, code: string): { debit: number; credit: number } | null {
    if (isProfitLossCode(code)) return { debit: 0, credit: 0 }
    let debit = 0
    let credit = 0
    if (yearStartDebitKey || yearStartCreditKey) {
      debit = yearStartDebitKey ? parseNumber(row[yearStartDebitKey]) : 0
      credit = yearStartCreditKey ? parseNumber(row[yearStartCreditKey]) : 0
    } else if (ytdDebitKey || ytdCreditKey) {
      // 非损益科目：rowDebitCredit 返回的就是期末借贷
      const ending = rowDebitCredit(row, code)
      const ytdDebit = ytdDebitKey ? parseNumber(row[ytdDebitKey]) : 0
      const ytdCredit = ytdCreditKey ? parseNumber(row[ytdCreditKey]) : 0
      debit = endingIsYearStartColumn ? ending.debit : ending.debit - ytdDebit
      credit = endingIsYearStartColumn ? ending.credit : ending.credit - ytdCredit
    } else {
      return null
    }
    const net = round2(debit - credit)
    return net >= 0 ? { debit: net, credit: 0 } : { debit: 0, credit: -net }
  }

  const amountByCode = new Map<string, number>()
  // 净值（借 − 贷）：判断「上级合计行是否是子级的纯汇总」必须用净值。
  // 用发生额毛值会误判——2202 父级期初贷 4469 是净值，子行里有借有贷，
  // 毛值相加是 22574.32 对不上，会错判成「不平」而把父行和子行同时保留（双计）。
  const netByCode = new Map<string, number>()
  for (let i = 0; i < rows.length; i++) {
    const code = rowCodes[i]
    if (!code) continue
    const { debit, credit } = rowDebitCredit(rows[i], code)
    amountByCode.set(code, (amountByCode.get(code) || 0) + Math.abs(debit) + Math.abs(credit))
    netByCode.set(code, Math.round(((netByCode.get(code) || 0) + debit - credit) * 100) / 100)
  }

  function matchesSystem(code: string, name: string): boolean {
    if (!code && !name) return false
    const mapped = mapping?.[code] || mapping?.[name]
    if (mapped && codeMap.has(mapped)) return true
    const cleanCode = code.replace(/[.\-_]/g, '')
    if (code && (codeMap.has(code) || codeMap.has(cleanCode))) return true
    const prefixCode = code.includes('.') ? code.split('.')[0] : code.includes('-') ? code.split('-')[0] : ''
    if (prefixCode && codeMap.has(prefixCode)) return true
    if (name && nameMap.has(name)) return true
    return false
  }

  /** 文件内是否存在以 code 为前缀的更长科目编码（即 code 是文件内的上级科目） */
  function hasFileChildren(code: string): boolean {
    if (!code) return false
    return rowCodes.some(c => c.length > code.length && c.startsWith(code))
  }

  /** code 的最长真前缀若存在于文件编码中，返回该上级编码 */
  function longestFileParent(code: string): string {
    for (let i = code.length - 1; i >= 1; i--) {
      const p = code.slice(0, i)
      if (fileCodeSet.has(p)) return p
    }
    return ''
  }

  // 上级科目是否因「存在匹配系统科目的下级」而让位（保留更细的下级层级）
  const parentYieldsToChildren = new Set<string>()
  const nameByCode = new Map<string, string>()
  rowCodes.forEach((c, i) => {
    if (c && !nameByCode.has(c)) nameByCode.set(c, rowNames[i])
  })
  for (const code of fileCodeSet) {
    if (!hasFileChildren(code)) continue
    const anyChildMatches = rowCodes.some(
      (c, i) => c.length > code.length && c.startsWith(code) && matchesSystem(c, rowNames[i]),
    )
    if (anyChildMatches) parentYieldsToChildren.add(code)
  }

  // 规则一补：往来科目（1122/2202 等）的上级合计行，若其文件内子级行的**净额之和**
  // 恰等于上级净额，则上级让位、保留子级——单位名只挂在子级行上，不让位就会把
  // 「欠谁钱」整段丢掉（只剩一个「未指定往来单位」的大组）。
  // 只在等式成立时才让位，金额因此不重不漏；对不上说明文件层级本身不平，
  // 仍按原规则保留上级聚合行（并由后面的对账告警兜住）。
  for (const code of fileCodeSet) {
    if (parentYieldsToChildren.has(code)) continue
    if (!isPartnerAccountCode(code)) continue
    if (!hasFileChildren(code)) continue
    const parentNet = netByCode.get(code) || 0
    const childNet = Math.round(
      rowCodes.reduce((sum, c) => {
        if (c.length <= code.length || !c.startsWith(code)) return sum
        return sum + (netByCode.get(c) || 0)
      }, 0) * 100,
    ) / 100
    if (Math.abs(parentNet - childNet) < 0.005) {
      parentYieldsToChildren.add(code)
    }
  }

  // 规则三：上级自身匹配不上系统、下级也没有能匹配系统的——若下级带非零金额，
  // 跳过上级聚合行、保留下级明细行进映射列表（下级逐个映射无损；把聚合映射到
  // 任何单一子级都失真）。下级全为零时聚合是唯一载体，保留上级。
  const skipUnmatchableParent = new Set<string>()
  for (const code of fileCodeSet) {
    if (parentYieldsToChildren.has(code)) continue // 规则一已让位
    if (matchesSystem(code, nameByCode.get(code) || '')) continue // 规则二：自身可匹配，保留
    if (!hasFileChildren(code)) continue
    const hasNonzeroChild = rowCodes.some(
      (c) => c.length > code.length && c.startsWith(code) && (amountByCode.get(c) || 0) > 0,
    )
    if (hasNonzeroChild) skipUnmatchableParent.add(code)
  }

  const isSkippedParent = (code: string) =>
    parentYieldsToChildren.has(code) || skipUnmatchableParent.has(code)

  /** 该行编码的某条上级链路仍保留合计行时，本行金额已含于上级，应跳过 */
  function isCoveredByKeptAncestor(code: string): boolean {
    // 往来科目的子级行带单位名，是「欠谁钱」的唯一来源，不能被上级合计行覆盖掉。
    // 这类行的金额由「子级之和 == 上级才让位」的等式保证不重复计入。
    if (isPartnerAccountCode(code)) return false
    let cur = code
    while (cur) {
      const parent = longestFileParent(cur)
      if (!parent) return false
      if (!isSkippedParent(parent)) return true // 该上级保留合计行
      cur = parent // 上级已被跳过（让位/由更细层级承载），继续向上检查
    }
    return false
  }

  let skippedRollupRowCount = 0
  /** 收到过「上级合计行」金额的系统科目：只有这些科目才需要和客商行对账 */
  const rollupCodes = new Set<string>()
  // 年初余额按「科目 + 客商」累计，与期末金额同一套去重口径：
  // 合计行被判为纯汇总清零时，它的年初贡献也一并清零（见下方对账式丢弃）。
  const yearStartNet = new Map<string, number>()
  let yearStartDerivable = false

  const resultMap = new Map<string, MatchedOpeningItem>()
  for (const acc of allAccounts) {
    resultMap.set(acc.account_code, {
      account_code: acc.account_code,
      account_name: acc.account_name || acc.account_code,
      debit_balance: 0,
      credit_balance: 0,
      partner_name: '',
      matched: false,
      is_expense_revenue: isProfitLossCode(acc.account_code),
    })
  }

  let matchedCount = 0
  let unmatchedCount = 0
  const unmatchedRows: UnmatchedOpeningRow[] = []

  for (const row of rows) {
    const rawCode = codeKey ? String(row[codeKey] ?? '').trim() : ''
    const rawName = nameKey ? String(row[nameKey] ?? '').trim() : ''
    let rawPartner = partnerKey ? String(row[partnerKey] ?? '').trim() : ''
    if (!rawPartner) {
      for (const k of Object.keys(row)) {
        if (PARTNER_ALIASES.some(alias => k.includes(alias))) {
          const v = String(row[k] ?? '').trim()
          if (v) {
            rawPartner = v
            break
          }
        }
      }
    }
    // 无独立客商列时（第三方科目余额表通常没有），从子级科目名后缀切出单位名
    if (!rawPartner && isPartnerAccountCode(rawCode)) {
      rawPartner = partnerFromAccountName(rawName)
    }

    // 过滤掉总计行/空行
    if (rawCode.includes('合计') || rawName.includes('合计') || rawName.includes('总计')) {
      continue
    }
    if (!rawCode && !rawName) continue

    // 文件内层级去重：见上方「父子科目层级去重」说明
    if (rawCode && parentYieldsToChildren.has(rawCode)) {
      continue // 上级合计行已让位给匹配系统科目的下级明细
    }
    if (rawCode && isCoveredByKeptAncestor(rawCode)) {
      skippedRollupRowCount++ // 下级明细金额已含于保留的上级合计行
      continue
    }
    if (rawCode && skipUnmatchableParent.has(rawCode)) {
      skippedRollupRowCount++ // 上级无法匹配系统科目，由其下级明细行进映射列表
      continue
    }

    // 计算借贷数值：优先取借贷分列，单列金额+方向、损益累计列为备选口径
    let { debit, credit } = rowDebitCredit(row, rawCode)

    // 冲抵同一科目同时填写借贷的情况（损益科目保留借贷两侧的累计发生额，不冲抵）
    if (debit > 0 && credit > 0 && !isProfitLossCode(rawCode)) {
      if (debit >= credit) {
        debit = Math.round((debit - credit) * 100) / 100
        credit = 0
      } else {
        credit = Math.round((credit - debit) * 100) / 100
        debit = 0
      }
    }

    const yearStart = rowYearStart(row, rawCode)
    const yearStartEmpty = !yearStart || (yearStart.debit === 0 && yearStart.credit === 0)
    // 期末已清零的科目仍可能有年初余额（年内收清的应收、付清的应付）：这类行
    // 期末金额为 0，但年初数不能跟着丢，否则报表年初列两侧对不平。
    if (debit === 0 && credit === 0 && yearStartEmpty) {
      continue
    }

    // 匹配系统科目（映射优先 -> 精准 -> 清洗 -> 前缀 -> 中文名称）
    const mappedCode = mapping?.[rawCode] || mapping?.[rawName]
    let target = mappedCode ? codeMap.get(mappedCode) : undefined
    if (!target) {
      const cleanCode = rawCode.replace(/[.\-_]/g, '')
      const prefixCode = rawCode.includes('.') ? rawCode.split('.')[0] : rawCode.includes('-') ? rawCode.split('-')[0] : ''
      target = codeMap.get(rawCode) || codeMap.get(cleanCode)
      if (!target && prefixCode) {
        target = codeMap.get(prefixCode)
      }
      // 往来科目子码回退到 4 位父科目：第三方导出的是 1122001（单位名在科目名里），
      // 本系统只有一级 1122。单位身份由客商承载，科目落回 1122——
      // 这正是不建二级科目也能按单位出账龄的落点。
      if (!target && isPartnerAccountCode(rawCode) && rawCode.length > 4) {
        target = codeMap.get(rawCode.slice(0, 4))
      }
      if (!target && rawName) {
        target = nameMap.get(rawName)
      }
    }

    if (target) {
      // 按「科目 + 客商」拆行：同一科目下多个往来单位各自成行，账龄才能按单位分开。
      // 不挂客商的行仍归并到该科目的无客商行（沿用原有单行语义）。
      const existing = resultMap.get(target.account_code)!
      const partnerName = rawPartner || ''
      if (yearStart) {
        // 损益科目恒为 0，不能拿它证明「年初数可推算」
        if (!isProfitLossCode(rawCode)) yearStartDerivable = true
        const key = `${target.account_code}\u0000${partnerName}`
        yearStartNet.set(
          key,
          round2((yearStartNet.get(key) || 0) + yearStart.debit - yearStart.credit),
        )
      }
      if (!partnerName) {
        existing.debit_balance = Math.round((existing.debit_balance + debit) * 100) / 100
        existing.credit_balance = Math.round((existing.credit_balance + credit) * 100) / 100
        existing.matched = true
        existing.originalCode = rawCode
        existing.originalName = rawName
        // 只有「本身还有下级」的合计行才需要和客商行对账；纯明细行（如 2241 下
        // 不挂单位的公积金）金额本来就该和客商行共存，拿来比较只会误报。
        if (hasFileChildren(rawCode)) rollupCodes.add(target.account_code)
      } else {
        const partnerKeyOfRow = `${target.account_code}\u0000${partnerName}`
        const samePartner = resultMap.get(partnerKeyOfRow)
        if (samePartner) {
          samePartner.debit_balance = Math.round((samePartner.debit_balance + debit) * 100) / 100
          samePartner.credit_balance = Math.round((samePartner.credit_balance + credit) * 100) / 100
          samePartner.matched = true
          samePartner.originalCode = rawCode
          samePartner.originalName = rawName
        } else {
          const row: MatchedOpeningItem = {
            account_code: target.account_code,
            account_name: target.account_name || target.account_code,
            debit_balance: debit,
            credit_balance: credit,
            partner_name: partnerName,
            matched: true,
            is_expense_revenue: isProfitLossCode(target.account_code),
            originalCode: rawCode,
            originalName: rawName,
          }
          resultMap.set(partnerKeyOfRow, row)
        }
        existing.matched = true
      }
      matchedCount++
    } else {
      unmatchedCount++
      warnings.push(`未匹配到系统科目：[${rawCode || '-'}] ${rawName || '-'} (借: ${debit}, 贷: ${credit})`)
      unmatchedRows.push({
        originalCode: rawCode,
        originalName: rawName,
        debit,
        credit,
        affects_balance: !isProfitLossCode(rawCode),
      })
    }
  }

  if (skippedRollupRowCount > 0) {
    warnings.push(`已跳过 ${skippedRollupRowCount} 行冗余层级科目行：同组科目的上级合计与下级明细只入账一次，防止重复入账`)
  }

  const allItems = Array.from(resultMap.values())

  // 对账式丢弃：往来科目若既留下了「无客商合计行」又拆出了带客商的子行，
  // 且两者**净额**相等，说明合计行是子行的纯汇总（冗余），丢掉它避免重复计入；
  // 不相等则保留合计行并告警——宁可在界面上多一行让用户看见，也不能静默丢钱。
  for (const item of allItems) {
    if (item.partner_name) continue
    if (item.debit_balance === 0 && item.credit_balance === 0) continue
    if (!rollupCodes.has(item.account_code)) continue // 只剩明细行，没有汇总可对账
    const partnerRows = allItems.filter(
      i => i.partner_name && i.account_code === item.account_code,
    )
    if (partnerRows.length === 0) continue
    const parentNet = Math.round((item.debit_balance - item.credit_balance) * 100) / 100
    const partnerNet = Math.round(
      partnerRows.reduce((s, i) => s + (i.debit_balance || 0) - (i.credit_balance || 0), 0) * 100,
    ) / 100
    if (Math.abs(parentNet - partnerNet) < 0.005) {
      item.debit_balance = 0
      item.credit_balance = 0
      yearStartNet.delete(`${item.account_code}\u0000`)
      item.warning = '上级合计行已由带往来单位的明细行承载，本行不计入'
    } else {
      warnings.push(
        `科目 ${item.account_code} 的合计行金额与往来单位明细行之和不一致，已同时保留，请核对`,
      )
    }
  }

  const items = allItems.filter(i => !i.is_expense_revenue)
  const ytdItems = allItems.filter(i => i.is_expense_revenue)

  // 年初余额（只给报表「年初余额」列用，不进凭证）：按科目汇总，与期末同一套
  // 去重口径；合计行被清零的科目只保留客商行那部分。
  const yearStartItems: YearStartBalanceItem[] = []
  if (yearStartDerivable) {
    for (const [key, net] of yearStartNet) {
      if (Math.abs(net) < 0.005) continue
      const [code, partnerName] = key.split('\u0000')
      yearStartItems.push({
        account_code: code,
        account_name: resultMap.get(code)?.account_name || code,
        ...(partnerName ? { partner_name: partnerName } : {}),
        debit_balance: net > 0 ? net : 0,
        credit_balance: net < 0 ? -net : 0,
      })
    }
    yearStartItems.sort((a, b) =>
      a.account_code.localeCompare(b.account_code)
      || (a.partner_name || '').localeCompare(b.partner_name || ''))
  } else if (items.some(i => i.debit_balance !== 0 || i.credit_balance !== 0)) {
    warnings.push('文件里没有「年初余额」或「本年累计发生额」列，推不出年初数；报表「年初余额」列将留空')
  }

  // 仅资产负债科目核算期初借贷平衡
  const totalDebit = Math.round(items.reduce((s, i) => s + (i.debit_balance || 0), 0) * 100) / 100
  const totalCredit = Math.round(items.reduce((s, i) => s + (i.credit_balance || 0), 0) * 100) / 100
  const difference = Math.round(Math.abs(totalDebit - totalCredit) * 100) / 100
  const isBalanced = difference === 0 && totalDebit > 0

  return {
    items,
    ytdItems,
    fixedAssets: [],
    totalDebit,
    totalCredit,
    difference,
    isBalanced,
    matchedCount,
    unmatchedCount,
    warnings,
    unmatchedRows,
    rawRows: rows,
    yearStartItems: yearStartDerivable ? yearStartItems : undefined,
    yearStartSource: yearStartDerivable
      ? (yearStartDebitKey || yearStartCreditKey ? 'explicit' : 'derived')
      : null,
  }
}

/**
 * 旧软件科目 → 本系统科目的名称关键词映射建议。
 *
 * 只做「预填」不做决定：建议值写进映射下拉由用户确认（PRODUCT.md 确认权）。
 * 解析按「系统科目名」而非科目码——科目表调整后规则自动跟随，不用改代码。
 * 命中不了标准名的返回 undefined，留给用户手选。
 */
export function suggestMappingTarget(
  sourceCode: string,
  sourceName: string,
  allAccounts: { account_code: string; account_name?: string }[],
  direction?: 'debit' | 'credit',
): string | undefined {
  const name = sourceName.trim()
  if (!name && !sourceCode.trim()) return undefined

  // 同名叶子分布在多个科目家族（如「单位社保」同时存在于 221102 与 5602016），
  // 按来源编码前缀推断家族名前缀消歧
  const familyNamePrefix = (() => {
    if (sourceCode.startsWith('2211')) return '应付职工薪酬-'
    if (/^(50|51|53|54|56|57|58|4301)/.test(sourceCode)) return '管理费用-'
    if (sourceCode.startsWith('2221')) return '应交'
    return ''
  })()

  const findByNameToken = (token: string): string | undefined => {
    const hits = allAccounts.filter(a => (a.account_name || '').includes(token))
    if (hits.length === 0) return undefined
    if (hits.length === 1) return hits[0].account_code
    // 多候选时：精确同名 > 家族前缀 > 首个（子串命中可能带出「待转销项税额」
    // 这类近名叶子，精确同名必须优先）
    const exact = hits.find(a => (a.account_name || '') === token)
    if (exact) return exact.account_code
    const scoped = familyNamePrefix
      ? hits.find(a => (a.account_name || '').startsWith(familyNamePrefix))
      : undefined
    return (scoped || hits[0]).account_code
  }

  // 增值税明细：名称带方向线索优先，其次按余额方向语义（借=进项 / 贷=销项）
  if (/进项/.test(name) && !/(转出|待抵扣|待认证|留抵)/.test(name)) return findByNameToken('进项税额')
  if (/销项/.test(name) && !/待转/.test(name)) return findByNameToken('销项税额')
  if (/增值税/.test(name)) {
    if (direction === 'debit') return findByNameToken('进项税额')
    if (direction === 'credit') return findByNameToken('销项税额')
    return undefined // 无方向线索，不猜
  }

  if (/公积金/.test(name)) return findByNameToken('住房公积金')
  if (/社保|社会保险/.test(name)) return findByNameToken('单位社保')
  if (/所得税/.test(name)) return findByNameToken('应交所得税')
  if (/城市维护建设税|城建税/.test(name)) return findByNameToken('城市维护建设税')
  if (/教育费附加/.test(name)) return findByNameToken('教育费附加')
  if (/印花税/.test(name)) return findByNameToken('印花税')
  if (/房产税/.test(name)) return findByNameToken('应交房产税')
  if (/个人所得税/.test(name)) return findByNameToken('个人所得税')
  if (/福利/.test(name)) return findByNameToken('福利费')
  if (/技术服务/.test(name)) return findByNameToken('信息技术服务费')
  if (/工资|薪酬/.test(name)) return findByNameToken('工资')
  return undefined
}

const FA_NAME_ALIASES = ['资产名称', '设备名称', '固定资产名称', '固定资产', '名称', 'name', 'asset_name']
const FA_CATEGORY_ALIASES = ['资产类别', '类别', '分类', '资产分类', 'category']
const FA_DATE_ALIASES = ['购入日期', '购置日期', '购买日期', '入账日期', '日期', 'purchase_date', 'date']
const FA_COST_ALIASES = ['原值', '资产原值', '固定资产原值', '金额', '原价', 'cost', 'original_value']
const FA_DEPR_ALIASES = ['累计折旧', '已提折旧', '期初累计折旧', '折旧', '已提金额', 'accumulated_depreciation']
const FA_MONTHS_ALIASES = ['预计使用月数', '使用月数', '折旧期数', '年限(月)', '月份', 'useful_months', 'months']
const FA_SALVAGE_ALIASES = ['预计净残值率', '残值率', '净残值率', '残值率(%)', 'salvage_rate']

/**
 * 解析固定资产卡片行数据
 */
export function parseFixedAssetRows(rows: Record<string, unknown>[]): FixedAssetImportItem[] {
  if (!rows || rows.length === 0) return []
  const allKeys = Array.from(new Set(rows.flatMap(r => Object.keys(r))))
  const nameKey = findKey(allKeys, FA_NAME_ALIASES)
  const catKey = findKey(allKeys, FA_CATEGORY_ALIASES)
  const dateKey = findKey(allKeys, FA_DATE_ALIASES)
  const costKey = findKey(allKeys, FA_COST_ALIASES)
  const deprKey = findKey(allKeys, FA_DEPR_ALIASES)
  const monthsKey = findKey(allKeys, FA_MONTHS_ALIASES)
  const salvageKey = findKey(allKeys, FA_SALVAGE_ALIASES)

  if (!nameKey && !costKey) return []

  const assets: FixedAssetImportItem[] = []
  for (const row of rows) {
    const name = nameKey ? String(row[nameKey] ?? '').trim() : ''
    if (!name || name.includes('合计') || name.includes('总计')) continue

    const cost = costKey ? parseNumber(row[costKey]) : 0
    if (cost <= 0) continue

    const depr = deprKey ? parseNumber(row[deprKey]) : 0
    const cat = catKey ? String(row[catKey] ?? '').trim() : '电子设备'
    let pDate = dateKey ? String(row[dateKey] ?? '').trim() : ''
    // 兼容 Excel 日期数字或标准字符串
    if (/^\d{4}-\d{2}-\d{2}$/.test(pDate)) {
      // 保持原有格式
    } else if (pDate.includes('/')) {
      pDate = pDate.replace(/\//g, '-')
    }

    let months = monthsKey ? parseInt(String(row[monthsKey] ?? '36'), 10) : 36
    if (isNaN(months) || months <= 0) months = 36

    let salvage = salvageKey ? parseNumber(row[salvageKey]) : 5
    if (salvage > 1) salvage = salvage / 100 // 如用户填 5 视为 5%
    if (salvage < 0 || salvage >= 1) salvage = 0.05

    assets.push({
      name,
      category: cat || '电子设备',
      purchase_date: pDate || undefined,
      cost,
      accumulated_depreciation: depr,
      useful_months: months,
      salvage_rate: salvage,
    })
  }
  return assets
}

const SUB_HEADER_CELLS = new Set(['借方', '贷方'])

/**
 * 读取 Sheet 为行对象数组，带表头行探测与两层表头拼接。
 *
 * 第三方软件（自记账 / 用友 / 金蝶等）导出的科目余额表顶部常有标题、期间、
 * 核算单位行，真实表头在第 4-5 行，且往往是两层结构：上层「期初余额 / 本年
 * 累计发生额 / 期末余额」合并跨列，下层才是「借方 / 贷方」。sheet_to_json
 * 默认把第 1 行当表头，所有列名会变成 __EMPTY_*，导致 0 匹配。
 *
 * 探测规则：前 10 行内找同时含科目编码类与科目名称类单元格的行作为表头；
 * 其下一行若全是「借方/贷方」则视为第二层表头，列名拼接为「期初余额借方」
 * 这类复合名（别名表已相应前置）。探测不到时回退 sheet_to_json 默认行为，
 * 兼容模板文件（第 1 行即表头）。
 */
export function sheetToRows(
  ws: XLSX.WorkSheet,
  options: { raw?: boolean } = {},
): Record<string, unknown>[] {
  const raw = options.raw ?? true
  const grid = XLSX.utils.sheet_to_json(ws, { header: 1, defval: '', blankrows: false, raw }) as unknown[][]
  if (grid.length === 0) return []

  const cell = (v: unknown) => String(v ?? '').trim()
  const aliasHit = (v: unknown, aliases: string[]) => {
    const s = cell(v)
    return !!s && aliases.some(a => s.includes(a))
  }

  let headerIdx = -1
  for (let i = 0; i < Math.min(10, grid.length); i++) {
    const row = grid[i] || []
    if (row.some(v => aliasHit(v, CODE_ALIASES)) && row.some(v => aliasHit(v, NAME_ALIASES))) {
      headerIdx = i
      break
    }
  }

  if (headerIdx === -1) {
    return XLSX.utils.sheet_to_json(ws, { defval: '', raw }) as Record<string, unknown>[]
  }

  const headerRow = grid[headerIdx] || []
  const subRow = headerIdx + 1 < grid.length ? grid[headerIdx + 1] || [] : []
  const subRowNonEmpty = subRow.filter(v => cell(v))
  const twoTier = subRowNonEmpty.length > 0 && subRowNonEmpty.every(v => SUB_HEADER_CELLS.has(cell(v)))

  // 上层表头的合并单元格只在左上角有值（如「期初余额」跨借/贷两列），
  // 必须向后补齐，否则拼出来的列名是孤立的「贷方」，多列同名会在行对象里
  // 互相覆盖（最后一次赋值是「期末余额贷方」，导致期初被读成期末）。
  let lastTop = ''
  const headers = headerRow.map((h, col) => {
    const top = cell(h)
    if (top) lastTop = top
    if (twoTier) {
      const sub = cell(subRow[col])
      if (sub) return `${lastTop}${sub}`
    }
    return top
  })

  const bodyStart = twoTier ? headerIdx + 2 : headerIdx + 1
  const out: Record<string, unknown>[] = []
  for (let r = bodyStart; r < grid.length; r++) {
    const row = grid[r] || []
    if (row.every(v => !cell(v))) continue
    const obj: Record<string, unknown> = {}
    headers.forEach((h, col) => {
      if (h) obj[h] = row[col] ?? ''
    })
    out.push(obj)
  }
  return out
}

// ── 报表期间识别与建账期间校验 ─────────────────────────────────────────
//
// 期初建账只接受一种输入形态：资产负债取「建账前一月末」的时点余额（期末列），
// 损益取「当年 1 月至建账前一月」的本年累计发生额。第三方导出的余额表若期间
// 不符，金额再平衡也不能用——期初会整体错位。这里识别表头里的期间并校验。

export interface StatementPeriod {
  startYear: number
  startMonth: number
  endYear: number
  endMonth: number
  /** 命中的原始文本，用于在界面上告诉用户「我认成了什么」 */
  label: string
}

export interface ExpectedPeriod {
  startYear: number
  startMonth: number
  endYear: number
  endMonth: number
  /** 年中建账要求从 1 月起（损益累计完整性）；年初建账只要求终点是上年 12 月 */
  requiresFullYear: boolean
}

export type OpeningPeriodCheck =
  | { ok: true; period: StatementPeriod }
  | {
      ok: false
      reason: 'unknown' | 'end_mismatch' | 'start_mismatch'
      message: string
      expected: ExpectedPeriod
    }

const PERIOD_DATE_PATTERN =
  '(20\\d{2})\\s*[年\\-/.]\\s*(\\d{1,2})(?:\\s*月|[/\\-.]\\s*\\d{1,2}(?!\\d)\\s*日?)?'
const PERIOD_RANGE_RE = new RegExp(
  `${PERIOD_DATE_PATTERN}\\s*(?:[~～至到]|--?|—|－)\\s*(?:(20\\d{2})\\s*[年\\-/.]\\s*)?(\\d{1,2})(?:\\s*月|[/\\-.]\\s*\\d{1,2}(?!\\d)\\s*日?)?`,
)
const PERIOD_SINGLE_RE = new RegExp(`(?:期间|会计期间|所属期)\\s*[:：]\\s*${PERIOD_DATE_PATTERN}`)

function validYearMonth(year: number, month: number): boolean {
  return Number.isFinite(year) && year >= 2000 && year <= 2100 && month >= 1 && month <= 12
}

/**
 * 从 sheet 前 10 行（表头及其上方的标题/期间/单位行）识别报表期间。
 *
 * 覆盖：`2026年1月-2026年5月`、`2026-01至2026-05`、`2026/01/01-2026/05/31`、
 * `2026年9月~9月`（结束年省略）、`期间：2026年9月`（单月）。识别不到返回 null，
 * 由页面让用户手选截止月，不猜。
 */
export function detectStatementPeriod(ws: XLSX.WorkSheet): StatementPeriod | null {
  const grid = XLSX.utils.sheet_to_json(ws, { header: 1, defval: '', blankrows: false, raw: false }) as unknown[][]
  const text = grid
    .slice(0, 10)
    .flat()
    .map(v => String(v ?? '').trim())
    .filter(Boolean)
    .join(' ')
  if (!text) return null

  const range = text.match(PERIOD_RANGE_RE)
  if (range) {
    const startYear = Number(range[1])
    const startMonth = Number(range[2])
    const endYear = Number(range[3] || range[1])
    const endMonth = Number(range[4])
    if (validYearMonth(startYear, startMonth) && validYearMonth(endYear, endMonth)) {
      return {
        startYear,
        startMonth,
        endYear,
        endMonth,
        label: range[0].replace(/\s+/g, ' ').trim(),
      }
    }
  }

  const single = text.match(PERIOD_SINGLE_RE)
  if (single) {
    const year = Number(single[1])
    const month = Number(single[2])
    if (validYearMonth(year, month)) {
      return {
        startYear: year,
        startMonth: month,
        endYear: year,
        endMonth: month,
        label: single[0].replace(/\s+/g, ' ').trim(),
      }
    }
  }
  return null
}

function formatYearMonth(year: number, month: number): string {
  return `${year}-${String(month).padStart(2, '0')}`
}

/** 建账期间对应的应有报表期间 */
export function expectedOpeningPeriod(fiscalYear: number, startMonth: string): ExpectedPeriod {
  const month = parseInt(startMonth, 10) || 1
  if (month > 1) {
    return {
      startYear: fiscalYear,
      startMonth: 1,
      endYear: fiscalYear,
      endMonth: month - 1,
      requiresFullYear: true,
    }
  }
  return {
    startYear: fiscalYear - 1,
    startMonth: 1,
    endYear: fiscalYear - 1,
    endMonth: 12,
    requiresFullYear: false,
  }
}

/** 期望期间的可读文案，如「2026-01 ～ 2026-05」或「2025-12 月末」 */
export function describeExpectedPeriod(expected: ExpectedPeriod): string {
  if (expected.requiresFullYear) {
    return `${formatYearMonth(expected.startYear, expected.startMonth)} ～ ${formatYearMonth(expected.endYear, expected.endMonth)}`
  }
  return `${formatYearMonth(expected.endYear, expected.endMonth)} 月末`
}

/**
 * 校验导入表期间是否与建账期间匹配。
 *
 * 年初建账（1 月）只校验终点为上一年 12 月：资产负债要上年末余额，损益不生成
 * YTD，起点不重要。年中建账额外要求起点为当年 1 月，否则损益累计会缺月份。
 */
export function checkOpeningPeriod(
  period: StatementPeriod | null,
  fiscalYear: number,
  startMonth: string,
): OpeningPeriodCheck {
  const expected = expectedOpeningPeriod(fiscalYear, startMonth)
  const wanted = describeExpectedPeriod(expected)
  const month = parseInt(startMonth, 10) || 1

  if (!period) {
    return {
      ok: false,
      reason: 'unknown',
      expected,
      message: `未能识别这份表的期间。请确认表内有「期间：${wanted}」这类文字，或在下方手动选择截止月份。`,
    }
  }

  const gotEnd = formatYearMonth(period.endYear, period.endMonth)
  const endOrder = (period.endYear - expected.endYear) * 12 + (period.endMonth - expected.endMonth)

  if (endOrder !== 0) {
    const hitsStartMonth =
      month > 1 && period.endYear === fiscalYear && period.endMonth === month
    let reason: string
    if (hitsStartMonth) {
      reason = `这份表截止 ${gotEnd}，账套从 ${month} 月开始记账，${month} 月会被算两遍。`
    } else if (month === 1 && endOrder > 0) {
      reason = `年初建账需要上年 12 月末的余额，这份表截止 ${gotEnd}。`
    } else if (endOrder > 0) {
      reason = `这份表截止 ${gotEnd}，超出了建账范围。`
    } else {
      reason = `这份表只到 ${gotEnd}，还缺后序月份。`
    }
    return {
      ok: false,
      reason: 'end_mismatch',
      expected,
      message: `${reason}请重新导出 ${wanted} 的科目余额表。`,
    }
  }

  if (expected.requiresFullYear) {
    const startOrder =
      (period.startYear - expected.startYear) * 12 + (period.startMonth - expected.startMonth)
    if (startOrder !== 0) {
      const gotStart = formatYearMonth(period.startYear, period.startMonth)
      const reason =
        startOrder > 0
          ? `这份表从 ${gotStart} 开始，缺前序月份，损益本年累计会少算。`
          : `这份表从 ${gotStart} 开始，含了建账年度之前的月份，损益本年累计会多算。`
      return {
        ok: false,
        reason: 'start_mismatch',
        expected,
        message: `${reason}请重新导出 ${wanted} 的科目余额表。`,
      }
    }
  }

  return { ok: true, period }
}

/**
 * 解析完整 Excel 工作簿（支持多 Sheet：资产负债、损益累计、固定资产卡片）
 */
export function parseOpeningWorkbook(
  workbook: XLSX.WorkBook,
  allAccounts: { account_code: string; account_name?: string }[],
  mapping?: Record<string, string>,
): ParseOpeningResult {
  const sheetNames = workbook.SheetNames
  if (!sheetNames || sheetNames.length === 0) {
    return parseOpeningRows([], allAccounts, mapping)
  }

  // 1. 定位各个 Sheet
  const balanceSheetName = sheetNames.find(n => n.includes('资产负债') || n.includes('科目余额') || n.includes('期初余额')) || sheetNames[0]
  const ytdSheetName = sheetNames.find(n => n.includes('损益') || n.includes('累计发生'))
  const faSheetName = sheetNames.find(n => n.includes('固定资产') || n.includes('资产卡片') || n.includes('卡片'))

  // 解析资产负债 Sheet
  const balanceSheet = workbook.Sheets[balanceSheetName]
  const balanceRows = sheetToRows(balanceSheet)
  const baseResult = parseOpeningRows(balanceRows, allAccounts, mapping)
  baseResult.period = balanceSheet ? detectStatementPeriod(balanceSheet) : null

  // 如果有独立的损益 Sheet，合并损益科目数据
  if (ytdSheetName && ytdSheetName !== balanceSheetName) {
    const ytdRows = sheetToRows(workbook.Sheets[ytdSheetName])
    const ytdParsed = parseOpeningRows(ytdRows, allAccounts, mapping)
    if (ytdParsed.ytdItems && ytdParsed.ytdItems.length > 0) {
      const existingYtdMap = new Map(baseResult.ytdItems.map(y => [y.account_code, y]))
      for (const yi of ytdParsed.ytdItems) {
        if (yi.debit_balance > 0 || yi.credit_balance > 0) {
          existingYtdMap.set(yi.account_code, yi)
        }
      }
      baseResult.ytdItems = Array.from(existingYtdMap.values())
    }
  }

  // 解析固定资产 Sheet
  let fixedAssets: FixedAssetImportItem[] = []
  if (faSheetName) {
    const faRows = sheetToRows(workbook.Sheets[faSheetName])
    fixedAssets = parseFixedAssetRows(faRows)
  }
  baseResult.fixedAssets = fixedAssets

  return baseResult
}

/**
 * 构造标准期初科目余额导入模板工作簿。
 *
 * 资产负债列名用「期末余额」：模板要填的是建账前一月末的时点余额，与第三方
 * 导出的「1 月至建账前一月」余额表口径一致。拆出来是为了单测能直接检查列名，
 * 不触发浏览器下载。
 */
export function buildOpeningTemplateWorkbook(
  accounts: { account_code: string; account_name?: string }[],
  fiscalYear: number,
  startPeriod?: string,
): XLSX.WorkBook {
  const isMidYear = Boolean(startPeriod && !startPeriod.endsWith('-01'))

  const balanceAccounts = accounts.filter(a => !isProfitLossCode(a.account_code))
  const expenseAccounts = accounts.filter(a => isProfitLossCode(a.account_code))

  const balanceData = balanceAccounts.map(a => ({
    '科目代码': a.account_code,
    '科目名称': a.account_name || a.account_code,
    '期末余额借方': '',
    '期末余额贷方': '',
    '往来客商名称(可选)': '',
    '填写指引': '填写建账前一月末的余额（新账套的起点）：资产类通常在借方，负债/权益类通常在贷方，应收应付可填客商',
  }))

  const ytdData = expenseAccounts.map(a => ({
    '科目代码': a.account_code,
    '科目名称': a.account_name || a.account_code,
    '本年累计借方发生额': '',
    '本年累计贷方发生额': '',
    '填写指引': '成本费用在借方，收入在贷方；填当年 1 月至建账前一月的累计发生额',
  }))

  const faData = [
    {
      '资产名称': '办公电脑 (示例)',
      '资产类别': '电子设备',
      '购入日期': `${fiscalYear - 1}-06-15`,
      '原值(元)': 12000,
      '已提累计折旧(元)': 4000,
      '预计使用月数': 36,
      '预计净残值率(%)': 5,
      '填写指引': '原值合计应与1601科目借方一致，折旧合计应与1602科目贷方一致',
    },
  ]

  const wb = XLSX.utils.book_new()
  const ws1 = XLSX.utils.json_to_sheet(balanceData)
  ws1['!cols'] = [{ wch: 14 }, { wch: 22 }, { wch: 16 }, { wch: 16 }, { wch: 20 }, { wch: 45 }]
  XLSX.utils.book_append_sheet(wb, ws1, '资产负债期初余额')

  if (isMidYear) {
    const ws2 = XLSX.utils.json_to_sheet(ytdData)
    ws2['!cols'] = [{ wch: 14 }, { wch: 22 }, { wch: 20 }, { wch: 20 }, { wch: 45 }]
    XLSX.utils.book_append_sheet(wb, ws2, '损益本年累计发生额')
  }

  const ws3 = XLSX.utils.json_to_sheet(faData)
  ws3['!cols'] = [{ wch: 22 }, { wch: 16 }, { wch: 16 }, { wch: 16 }, { wch: 18 }, { wch: 14 }, { wch: 16 }, { wch: 45 }]
  XLSX.utils.book_append_sheet(wb, ws3, '固定资产期初卡片')

  return wb
}

/**
 * 导出标准期初科目余额导入模板 Excel
 */
export function exportOpeningTemplate(
  accounts: { account_code: string; account_name?: string }[],
  fiscalYear: number,
  startPeriod?: string,
) {
  const pName = startPeriod || `${fiscalYear}-01`
  saveWorkbook(buildOpeningTemplateWorkbook(accounts, fiscalYear, startPeriod), `梭罗记账_期初建账导入模板_${pName}.xlsx`)
}
