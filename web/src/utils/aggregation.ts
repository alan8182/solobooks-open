/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// 通用归集纯函数层。放这里而不是组件里，是为了能被 vitest 直接覆盖，
// 且避免同一段文案/校验在 6 个页面里各写一遍（口径分裂的根源）。

import { accountingErrorDetail } from './errors'
import type {
  AggregationLineState,
  ReportKind,
  ReportLinePatternInput,
  ReportLineSign,
} from '@/api/types'

// ===== 错误码 → 文案 =====
// 权威来源 backend/core/errors.py:40-64（注意任务书早期版本列的
// INVALID_AGGREGATION_DIMENSION 等 8 个码在代码里**不存在**，勿用）。
// 优先级：后端 message > 本表 > 调用方 fallback。后端 message 更具体（带上下文）。

export const AGG_ERROR_TEXT: Record<string, string> = {
  // 通用归集
  INVALID_DIMENSION: '归集维度不存在或已归档',
  INVALID_DIMENSION_MEMBER: '归集对象不存在或已归档',
  DUPLICATE_DIMENSION: '维度编码已存在',
  DUPLICATE_DIMENSION_MEMBER: '该维度下归集对象编码已存在',
  INVALID_ALLOCATION: '分配数据不合法（金额需大于 0 且不超过行金额；同一对象不可重复分配）',
  INVALID_ALLOCATION_MEMBER: '规则或分配引用了不存在的归集对象',
  ALLOCATION_TOTAL_MISMATCH: '标记完成归集时，合计必须精确等于凭证行金额',
  ALLOCATION_BASIS_REQUIRED: '拆给多个归集对象时，每条都必须填写分摊依据',
  ALLOCATION_RATE_OVERFLOW: '同一维度内该凭证行的分配比例合计不能超过 100%',
  ALLOCATION_ON_REVERSED: '冲销凭证或状态不允许的凭证不能归集',
  NOT_FOUND: '记录不存在',
  // 报表口径规则（P2）
  INVALID_REPORT_LINE_RULE: '报表口径规则不合法',
  REPORT_LINE_PATTERN_PREFIX_TOO_SHORT: '科目前缀不得短于 4 位（否则会吞掉整个一级科目）',
  REPORT_LINE_DUAL_TRACK_DIFF: '双轨对比存在差异，未达切换门槛',
  // ⚠️ REPORT_LINE_DUAL_TRACK_DIFF 在 core/errors.py 定义了，但 compare_with_baseline
  // **从不抛它**：对比结果是通过返回体的 diffs 数组给出的，不走 AccountingError。
  // 保留仅为兜底文案。**T05 不要依赖这个码判断「对比失败」**——
  // 对比永远成功返回，有没有差异看 result.diffs.length，不看错误码。
  REPORT_LINE_SWITCH_NOT_READY: '未达切换门槛',
}

/**
 * 提取归集模块的可读错误文案。
 * 错误对象形状：err.response.data.detail = {code, message, context, err_sig}（AccountingError.detail()）。
 */
export function errorText(err: unknown, fallback = '操作失败'): string {
  const detail = accountingErrorDetail(err)
  if (!detail) return fallback
  // 后端 message 优先——它带具体上下文（如指出是第几个 pattern）
  return detail.message || AGG_ERROR_TEXT[detail.code] || fallback
}

// ===== 三态与方向标签 =====
// Shared Knowledge #8：三态命名全项目统一，各页不得自造。

const STATE_LABEL: Record<AggregationLineState, string> = {
  unallocated: '未归集',
  partial: '部分归集',
  allocated: '已归集',
}

/** 三态 → 中文。未知值原样返回，避免展示 undefined */
export function stateLabel(state: string): string {
  return STATE_LABEL[state as AggregationLineState] ?? state
}

const DIRECTION_LABEL: Record<string, string> = {
  debit: '借',
  credit: '贷',
}

/** 凭证行方向 → '借'/'贷'。金额展示必须配合方向标签，因为后端 amount 带符号（贷为负） */
export function directionLabel(direction: string): string {
  return DIRECTION_LABEL[direction] ?? direction
}

/** 凭证状态 → 中文（待归集清单含 draft/pending_review/posted 三种） */
export function voucherStatusLabel(status: string): string {
  switch (status) {
    case 'draft':
      return '草稿'
    case 'pending_review':
      return '待审核'
    case 'posted':
      return '已过账'
    default:
      return status
  }
}

// ===== 派生行（与后端常量同步）=====
// 与 `backend/services/report_line_rule_service.py` 的 `DERIVED_LINES` **同步**；
// 本期后端不下发（改 `/meta` 契约会牵动已交付的 P1/P2 端到端验证）；
// 未来若 `/meta` 下发派生行清单，需同步替换此常量。
//
// 派生行由基础行在代码里加减算出，**不允许配置化**——后端碰到会抛
// INVALID_REPORT_LINE_RULE + context.derived=true（422）。
// 前端靠这份常量提前禁用入口，不依赖后端 422。


export const DERIVED_LINES: Record<ReportKind, string[]> = {
  profit: ['operating_profit', 'total_profit', 'net_profit'],
  balance: [
    'fixed_asset_book_value',
    'current_asset_total',
    'non_current_asset_total',
    'asset_total',
    'current_liability_total',
    'non_current_liability_total',
    'liability_total',
    'equity_total',
    'liability_equity_total',
  ],
}

/** 派生行的中文名。后端未暴露这份展示文案，故前端维护并在 DERIVED_LINES 处注明对应关系 */
export const DERIVED_LINE_LABELS: Record<string, string> = {
  operating_profit: '营业利润',
  total_profit: '利润总额',
  net_profit: '净利润',
  fixed_asset_book_value: '固定资产账面价值',
  current_asset_total: '流动资产合计',
  non_current_asset_total: '非流动资产合计',
  asset_total: '资产总计',
  current_liability_total: '流动负债合计',
  non_current_liability_total: '非流动负债合计',
  liability_total: '负债合计',
  equity_total: '所有者权益合计',
  liability_equity_total: '负债和所有者权益总计',
}

/** 该 lineCode 是否是派生行（不可配置化） */
export function isDerivedLine(report: ReportKind, lineCode: string): boolean {
  return (DERIVED_LINES[report] ?? []).includes(lineCode)
}

// ===== pattern 校验 =====
// 与后端 report_line_rule_service._parse_patterns 镜像，
// 让用户在 @blur 时就看到红字，而不是提交后吃 422。

/** 与后端 report_line_rule_service.MIN_PREFIX_LENGTH 同步 */
export const MIN_PREFIX_LENGTH = 4

/**
 * 校验单个声明式 pattern。返回 null 表示通过。
 * ⚠️ mode='prefix' 的 4 位下限是本模块存在的核心理由：
 * 短前缀会把 4301 连同所有 4xxx 科目一起吞掉。
 */
export function validatePattern(pattern: ReportLinePatternInput): string | null {
  const raw = pattern?.value ?? ''
  const value = raw.trim()
  if (!value) return '请填写科目值'

  if (pattern.mode === 'prefix') {
    if (!/^\d+$/.test(value)) return '科目前缀必须是数字科目码'
    if (value.length < MIN_PREFIX_LENGTH) {
      return `科目前缀不得短于 ${MIN_PREFIX_LENGTH} 位（${value} 会把所有 ${value[0]}xxx 科目一起吞掉）`
    }
  }

  if (pattern.mode === 'exact') {
    const codes = value.split(',').map((part) => part.trim()).filter(Boolean)
    if (!codes.length) return '精确科目至少填一个'
  }

  if (pattern.mode === 'range') {
    const matched = /^(\d+)-(\d+)$/.exec(value)
    if (!matched) return '科目区间形如 560101-560199'
    if (matched[1] > matched[2]) return '科目区间起止顺序颠倒'
  }

  return null
}

// ===== 金额计算 =====

/** 凭证行金额基数：后端 amount 带符号（贷行为负），比较时一律取绝对值 */
export function absoluteAmount(amount: number): number {
  return Math.abs(amount)
}

/**
 * 把十进制字符串按 HALF_UP（远离零）量化到「分」，返回整数。
 *
 * ⚠️ 与后端 `backend/core/money.py:19` 的 `money()` **逐位对齐**：
 *   后端是 `Decimal(str(v)).quantize(Decimal("0.01"), ROUND_HALF_UP)`。
 *   两个关键点必须同时满足才能对齐：
 *     ① 入参是 **`str(v)`**（十进制字面量），不是 float 的二进制值；
 *     ② ROUND_HALF_UP 是**远离零**，负数侧也要进位。
 *
 * 为什么不能用 `Math.round(value * 100)`：JS 的 `* 100` 会在**乘法当场**丢精度，
 * 而 Python 拿到的是从未丢过精度的十进制字符串。实测反例：
 *   `1.005 * 100 === 100.49999999999999` → Math.round 得 100（1.00）
 *   但 Python `Decimal("1.005")` 精确 → 1.01 → **前端判等放行、后端 422**。
 *
 * JS 的 `String(v)`（等价于 Python `str(v)`）输出的是**最短往返表示**，
 * 两者对同一个 double 一定得到同一串十进制字符——这正是可以对齐的前提。
 *
 * 一次性展开指数记数法（后端 `str(1e-7)` 同样给 `'1e-07'`），避免误判。
 */
function decimalStringToCents(text: string): number {
  // 1) 分离符号
  let rest = text
  let negative = false
  if (rest[0] === '-') {
    negative = true
    rest = rest.slice(1)
  } else if (rest[0] === '+') {
    rest = rest.slice(1)
  }

  // 2) 展开指数记数法： mantissa × 10^exp （JS 只会产出 e+NN / e-NN）
  let exponent = 0
  const eIndex = rest.search(/e/i)
  if (eIndex >= 0) {
    exponent = Number(rest.slice(eIndex + 1))
    rest = rest.slice(0, eIndex)
  }

  // 3) 去掉小数点，记住原小数位数
  const dotIndex = rest.indexOf('.')
  let digits: string
  let fractionLength: number
  if (dotIndex >= 0) {
    digits = rest.slice(0, dotIndex) + rest.slice(dotIndex + 1)
    fractionLength = rest.length - dotIndex - 1
  } else {
    digits = rest
    fractionLength = 0
  }
  // 移位后的小数位数：小数点右移 exponent 位
  const scale = fractionLength - exponent

  // 4) 把 digits 量化到 scale === 2 位小数；scale<2 需补位，scale>2 需 HALF_UP 进位
  let cents: number
  if (scale <= 2) {
    cents = Number(`${digits}${'0'.repeat(2 - scale)}`)
  } else {
    const keep = digits.length - (scale - 2)
    if (keep <= 0) {
      // 被截掉的部分全在第三位及之后，按「绝对值不足半厘则舍去」处理
      const firstDropped = digits.charAt(0)
      cents = firstDropped !== undefined && Number(firstDropped) >= 5 ? 1 : 0
    } else {
      cents = Number(digits.slice(0, keep))
      const firstDropped = digits.charAt(keep)
      // HALF_UP：正好一半或超出则进位（远离零由末尾 negative 处理）
      if (firstDropped !== undefined && Number(firstDropped) >= 5) cents += 1
    }
  }

  // 归一化负零：`negative ? -cents : cents` 在 cents===0 时会产出 -0，
  // 而 -0 会让 `Object.is(x, 0)` 判false（vitest / 部分相等性断言会踩）。
  // 数值上 -0 === 0，但统一成+0 可避免下游出现难以定位的断言失败。
  const signed = negative ? -cents : cents
  return signed === 0 ? 0 : signed
}

/**
 * 金额量化到「分」，返回整数。
 *
 * ⚠️ 这是全模块**唯一**的金额容差入口，不要在别处写裸浮点比较，
 * 也不要写 `Math.round(value * 100)`（见 decimalStringToCents 的反例）。
 *
 * 为什么必须量化：裸浮点会让前端「判等放行、后端 422」，
 * 用户点一下吃红弹窗，正是设计文档 §11 风险 4 要避免的体验。
 *
 * 口径与后端 `money()` 对齐（node + python 双跑逐行核对过）：
 *   1000.005 → 100001（1000.01）  ✅ 与后端一致
 *   1000.004 → 100000（1000.00）  ✅ 与后端一致
 *   1.005    →   101（   1.01）  ✅ 与后端一致（`Math.round(1.005*100)` 会错成 100）
 *   -1.005   →  -101（  -1.01）  ✅ 与后端一致（负数侧远离零）
 *   -1000.005→ -100001（-1000.01）✅ 与后端一致
 *   0.1+0.2  →    30（   0.30）  ✅ 消掉浮点累加噪声
 *
 * 残留差异（如实说明）：`value * 100` 之外无浮点参与，**只要该值本身
 * 来自用户十进制输入或后端两位小数字符串，就与后端完全一致**。
 * 唯一可能的分歧是「前端先做了浮点累加再量化」——例如三行各 0.335 累加成
 * 1.0049999999999999，而用户心里的值是 1.005；此时 `String()` 给出
 * `'1.0049999999999999'`，会判 1.00，而后端若拿到 1.005 会判 1.01。
 * 该分歧的方向是**前端判未平、后端判已平**，最坏是多提示一句，绝不误放行。
 */
export function toCents(value: number): number {
  if (!Number.isFinite(value)) return 0
  return decimalStringToCents(String(value))
}

/**
 * 量化到两位小数的「干净」金额，用于**展示**（合计条、清单列）。
 *
 * 为什么展示也要量化：后端 money_text 输出两位小数，但前端把三行相加时会出现
 * `0.1 + 0.2 = 0.30000000000000004` 这类噪声，直接交给 formatMoney 会打印出
 * 多余小数位，用户会以为账算错了。量化后与后端显示完全一致。
 */
export function quantizeAmount(value: number): number {
  return toCents(value) / 100
}

/** 量化到分后的差额（整数，单位：分）。正数超出、负数还差 */
export function diffCents(rows: AllocationDraftRow[], lineAmount: number): number {
  return toCents(sumAllocation(rows)) - toCents(absoluteAmount(lineAmount))
}

/**
 * 金额是否判等（按分比较）。
 * 用整数相等而非 `Math.abs(diff) < EPSILON`，理由见 toCents 的注释。
 */
export function isAmountEqual(a: number, b: number): boolean {
  return toCents(a) === toCents(b)
}

/** 分配草稿行（与后端 AggregationAllocationInput 对齐） */
export interface AllocationDraftRow {
  memberId: string
  amount: number
  allocationMethod: string
  allocationBasis?: string | null
}

/** 合计已分配金额 */
export function sumAllocation(rows: AllocationDraftRow[]): number {
  return rows.reduce((sum, row) => sum + (Number(row.amount) || 0), 0)
}

/**
 * 差额 = 合计 - 行金额（元）。负数表示还差，正数表示超出。
 * ⚠️ 已按分量化（diffCents / 100），因此结果一定是两位小数，
 * 不会把1000.005 - 1000 这类表示误差漏给调用方。
 */
export function diffOf(rows: AllocationDraftRow[], lineAmount: number): number {
  return diffCents(rows, lineAmount) / 100
}

/** 判等（按分比较，与后端 Decimal(20,2) 同口径）。见 toCents 的注释 */
export function isBalanced(rows: AllocationDraftRow[], lineAmount: number): boolean {
  return diffCents(rows, lineAmount) === 0
}

/** 未分配占比（0~100），用于「还差 45.2%」这类提示。行金额为 0 时返回 0 避免除零 */
export function remainingPercent(rows: AllocationDraftRow[], lineAmount: number): number {
  const expected = toCents(absoluteAmount(lineAmount))
  if (expected === 0) return 0
  const remainingCents = expected - toCents(sumAllocation(rows))
  return Math.round((remainingCents / expected) * 1000) / 10
}

/**
 * 校验分配草稿（与后端 replace_line_allocations 的校验镜像）。
 * 返回 null 表示通过。
 */
export function validateAllocations(
  rows: AllocationDraftRow[],
  lineAmount: number,
): string | null {
  if (!rows.length) return '请至少添加一条分配'
  if (rows.some((row) => !row.memberId)) return '请为每条分配选择归集对象'
  const ids = rows.map((row) => row.memberId)
  if (new Set(ids).size !== ids.length) return '同一归集对象不能重复分配'
  if (rows.some((row) => !(Number(row.amount) > 0))) return '分配金额必须大于 0'
  const expected = quantizeAmount(absoluteAmount(lineAmount))
  // 比较前量化到分，与后端 Decimal(20,2) 同口径，避免浮点噪声让「本该合法」的行被前端误拦
  if (rows.some((row) => quantizeAmount(Number(row.amount)) > expected)) return '单条分配不能超过行金额'
  if (rows.length > 1 && rows.some((row) => !String(row.allocationBasis ?? '').trim())) {
    return '拆给多个归集对象时，每条都必须填写分摊依据'
  }
  return null
}

/**
 * 等额拆分为 n 份，**最后一条吃剩余**，保证合计精确等于 total。
 * 按比例分摊必然尾差（rate 是 Numeric(12,6)），所以批量分配只提供等额拆分。
 */
export function splitEvenly(total: number, count: number): number[] {
  const targetCents = toCents(absoluteAmount(total))
  if (count <= 0) return []
  if (count === 1) return [targetCents / 100]
  // 全程整数分运算：先向下取整到分，再把尾差并入最后一条，保证 toCents(合计) 精确等于 targetCents
  const base = Math.floor(targetCents / count)
  const amounts: number[] = []
  for (let i = 0; i < count - 1; i += 1) amounts.push(base / 100)
  amounts.push((targetCents - base * (count - 1)) / 100)
  return amounts
}

// ===== 规则 sign 文案 =====
// 注意：规则的 sign 是 'debit'|'credit'|'net'，pattern.sign 是 ±1 数值，两者别混。

export interface SignOption {
  value: ReportLineSign
  label: string
  tooltip: string
}

export const REPORT_LINE_SIGN_OPTIONS: SignOption[] = [
  {
    value: 'net',
    label: '按科目方向（默认）',
    tooltip: '取科目的正常余额方向：借方科目为正、贷方科目为负。多数费用/资产行用这个。',
  },
  {
    value: 'debit',
    label: '只取借方发生',
    tooltip: '无论科目方向，只统计借方发生额。',
  },
  {
    value: 'credit',
    label: '只取贷方发生',
    tooltip: '无论科目方向，只统计贷方发生额。',
  },
]

/**
 * 把 patterns 拼成中文表达式预览，如
 * 「研发支出-费用化支出 = 前缀 4301 − 前缀 4301002」。
 * 这是让用户理解「声明式加减组合」最有效的形式。
 */
export function patternExpression(patterns: ReportLinePatternInput[]): string {
  if (!patterns.length) return '（未配置科目）'
  const modeLabel: Record<string, string> = {
    prefix: '前缀',
    exact: '科目',
    range: '区间',
  }
  return patterns
    .map((pattern) => {
      const prefix = pattern.sign === -1 ? '−' : '+'
      return `${prefix} ${modeLabel[pattern.mode] ?? pattern.mode} ${pattern.value}`
    })
    .join(' ')
    .replace(/^\+/, '')
}

// ===== 归集口径说明文案 =====
// 清单与报表口径不同是刻意设计，必须在两个页面各自说明，否则会被当成 bug 报上来。

export const PENDING_SCOPE_NOTE =
  '待归集清单包含草稿、待审核与已过账凭证，与总账余额口径不同。'

export const REPORT_SCOPE_NOTE =
  '归集报表只统计已过账（posted）凭证，与总账余额可勾稽；待归集清单还包含草稿凭证，两者数字不同是刻意的。'

export const OCCUR_DATE_WARNING =
  '按业务日期筛选时，跨月补记的凭证会出现在其业务日期所在月份，可能与「本月」账套视图不一致。'

/** 规则保存成功后的提示必须带这句——本模块不接管对外报表 */
export const RULE_SAVED_NOTE =
  '规则已保存。对外报表口径未变，请到本页运行双轨对比确认零差异。'
