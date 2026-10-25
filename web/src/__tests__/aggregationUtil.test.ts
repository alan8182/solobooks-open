/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// 归集纯函数单测。覆盖三类最容易出错、且出错后静默无提示的逻辑：
//   ① 错误码映射（映射错了用户看到的是英文错误码而非中文原因）；
//   ② pattern 校验（前缀 <4 位若不在前端拦，用户提交后才吃 422）；
//   ③ 分配金额计算（浮点尾差会让「完成归集」按钮永远置灰或误放行）。

import { describe, expect, it } from 'vitest'
import {
  AGG_ERROR_TEXT,
  DERIVED_LINES,
  MIN_PREFIX_LENGTH,
  absoluteAmount,
  diffOf,
  directionLabel,
  diffCents,
  errorText,
  isBalanced,
  isAmountEqual,
  isDerivedLine,
  patternExpression,
  quantizeAmount,
  remainingPercent,
  splitEvenly,
  stateLabel,
  sumAllocation,
  toCents,
  validateAllocations,
  validatePattern,
  voucherStatusLabel,
} from '@/utils/aggregation'
import type { AllocationDraftRow } from '@/utils/aggregation'

function axiosError(detail: unknown) {
  return { response: { data: { detail } } }
}

describe('errorText', () => {
  it('prefers the backend message over the local mapping', () => {
    const err = axiosError({
      code: 'ALLOCATION_BASIS_REQUIRED',
      message: '第 2 条分配缺少分摊依据',
      context: {},
      err_sig: 'ERR_JZXK_ALLOCATION_BASIS_REQUIRED',
    })

    expect(errorText(err, 'fallback')).toBe('第 2 条分配缺少分摊依据')
  })

  it('falls back to the mapped text when the backend sends no message', () => {
    const err = axiosError({ code: 'ALLOCATION_TOTAL_MISMATCH', message: '' })

    expect(errorText(err, 'fallback')).toBe(AGG_ERROR_TEXT.ALLOCATION_TOTAL_MISMATCH)
    expect(errorText(err, 'fallback')).toContain('精确等于')
  })

  it('maps every real error code declared in backend/core/errors.py:49-64', () => {
    // 这些是代码里真实存在的码；任务书早期版本列的 INVALID_AGGREGATION_* 并不存在
    const codes = [
      'ALLOCATION_TOTAL_MISMATCH',
      'ALLOCATION_BASIS_REQUIRED',
      'INVALID_ALLOCATION_MEMBER',
      'INVALID_ALLOCATION',
      'ALLOCATION_RATE_OVERFLOW',
      'ALLOCATION_ON_REVERSED',
      'INVALID_DIMENSION',
      'INVALID_DIMENSION_MEMBER',
      'DUPLICATE_DIMENSION',
      'DUPLICATE_DIMENSION_MEMBER',
      'INVALID_REPORT_LINE_RULE',
      'REPORT_LINE_PATTERN_PREFIX_TOO_SHORT',
      'REPORT_LINE_DUAL_TRACK_DIFF',
      'REPORT_LINE_SWITCH_NOT_READY',
    ]
    codes.forEach((code) => {
      expect(AGG_ERROR_TEXT[code], `缺少错误码映射：${code}`).toBeTruthy()
      expect(errorText(axiosError({ code, message: '' }), 'x')).not.toBe('x')
    })
  })

  it('has no stale or invented codes beyond the authoritative set', () => {
    // 只断言「都映射了」抓不到反向漂移：有人照着早期任务书把
    // INVALID_AGGREGATION_DIMENSION / AGGREGATION_NOT_FOUND 这类**不存在**的码
    // 抄进表里，测试仍然全绿。故用集合相等双向锁定。
    // 权威来源：core/errors.py 的通用归集 14 码 + 通用 NOT_FOUND。
    const authoritative = new Set([
      'ALLOCATION_TOTAL_MISMATCH',
      'ALLOCATION_BASIS_REQUIRED',
      'INVALID_ALLOCATION_MEMBER',
      'INVALID_ALLOCATION',
      'ALLOCATION_RATE_OVERFLOW',
      'ALLOCATION_ON_REVERSED',
      'INVALID_DIMENSION',
      'INVALID_DIMENSION_MEMBER',
      'DUPLICATE_DIMENSION',
      'DUPLICATE_DIMENSION_MEMBER',
      'INVALID_REPORT_LINE_RULE',
      'REPORT_LINE_PATTERN_PREFIX_TOO_SHORT',
      'REPORT_LINE_DUAL_TRACK_DIFF',
      'REPORT_LINE_SWITCH_NOT_READY',
      'NOT_FOUND',
    ])
    const actual = new Set(Object.keys(AGG_ERROR_TEXT))

    expect([...actual].filter((code) => !authoritative.has(code)), '多出了后端不存在的错误码').toEqual([])
    expect([...authoritative].filter((code) => !actual.has(code)), '缺少后端真实存在的错误码').toEqual([])
  })

  it('uses the caller fallback for network errors and unmapped codes', () => {
    expect(errorText(new Error('Network Error'), '加载失败')).toBe('加载失败')
    expect(errorText(axiosError({ code: 'SOMETHING_ELSE', message: '' }), '加载失败')).toBe('加载失败')
    expect(errorText(null, '加载失败')).toBe('加载失败')
  })
})

describe('labels', () => {
  it('uses the unified three-state naming across pages', () => {
    expect(stateLabel('unallocated')).toBe('未归集')
    expect(stateLabel('partial')).toBe('部分归集')
    expect(stateLabel('allocated')).toBe('已归集')
  })

  it('returns the raw value for unknown states rather than undefined', () => {
    expect(stateLabel('weird_state')).toBe('weird_state')
    expect(directionLabel('weird')).toBe('weird')
  })

  it('maps direction and voucher status', () => {
    expect(directionLabel('debit')).toBe('借')
    expect(directionLabel('credit')).toBe('贷')
    expect(voucherStatusLabel('draft')).toBe('草稿')
    expect(voucherStatusLabel('pending_review')).toBe('待审核')
    expect(voucherStatusLabel('posted')).toBe('已过账')
  })
})

describe('validatePattern', () => {
  it('rejects a prefix shorter than 4 digits and accepts 4301', () => {
    const tooShort = validatePattern({ mode: 'prefix', value: '430' })
    expect(tooShort).not.toBeNull()
    expect(tooShort).toContain('4')
    expect(validatePattern({ mode: 'prefix', value: '4301' })).toBeNull()
  })

  it('accepts longer prefixes and rejects non-numeric ones', () => {
    expect(validatePattern({ mode: 'prefix', value: '4301002' })).toBeNull()
    expect(validatePattern({ mode: 'prefix', value: '43ab' })).not.toBeNull()
  })

  it('pins the minimum length to the backend constant', () => {
    expect(MIN_PREFIX_LENGTH).toBe(4)
    expect(validatePattern({ mode: 'prefix', value: '430' })).not.toBeNull()
    expect(validatePattern({ mode: 'prefix', value: '4301' })).toBeNull()
  })

  it('validates exact patterns with comma-separated codes', () => {
    expect(validatePattern({ mode: 'exact', value: '4301, 4302' })).toBeNull()
    expect(validatePattern({ mode: 'exact', value: ' , ' })).not.toBeNull()
  })

  it('validates range patterns and rejects reversed bounds', () => {
    expect(validatePattern({ mode: 'range', value: '560101-560199' })).toBeNull()
    expect(validatePattern({ mode: 'range', value: '560199-560101' })).toContain('顺序')
    expect(validatePattern({ mode: 'range', value: '560101' })).not.toBeNull()
  })

  it('rejects an empty value in every mode', () => {
    expect(validatePattern({ mode: 'prefix', value: '' })).toBeTruthy()
    expect(validatePattern({ mode: 'exact', value: '  ' })).toBeTruthy()
    expect(validatePattern({ mode: 'range', value: '' })).toBeTruthy()
  })
})

describe('DERIVED_LINES', () => {
  it('matches the backend constant for profit lines', () => {
    expect(DERIVED_LINES.profit).toContain('operating_profit')
    expect(DERIVED_LINES.profit).toContain('total_profit')
    expect(DERIVED_LINES.profit).toContain('net_profit')
  })

  it('matches the backend constant for balance lines', () => {
    expect(DERIVED_LINES.balance).toContain('fixed_asset_book_value')
    expect(DERIVED_LINES.balance).toContain('liability_equity_total')
    expect(DERIVED_LINES.balance).toHaveLength(9)
  })

  it('identifies derived lines so the UI can disable configuration', () => {
    expect(isDerivedLine('profit', 'net_profit')).toBe(true)
    expect(isDerivedLine('profit', 'rd_expense')).toBe(false)
    expect(isDerivedLine('balance', 'asset_total')).toBe(true)
  })
})

describe('allocation amount math', () => {
  const rows: AllocationDraftRow[] = [
    { memberId: 'm1', amount: 60, allocationMethod: 'direct', allocationBasis: '按面积' },
    { memberId: 'm2', amount: 40, allocationMethod: 'direct', allocationBasis: '按人数' },
  ]

  it('sums allocations and diffs against the absolute line amount', () => {
    expect(sumAllocation(rows)).toBe(100)
    // 后端 amount 带符号（贷行为负），比较时必须取绝对值
    expect(absoluteAmount(-100)).toBe(100)
    expect(diffOf(rows, -100)).toBe(0)
    expect(isBalanced(rows, -100)).toBe(true)
  })

  it('detects partial and over-allocated states', () => {
    const partial = [{ memberId: 'm1', amount: 45.2, allocationMethod: 'direct' }]
    expect(isBalanced(partial, 100)).toBe(false)
    expect(remainingPercent(partial, 100)).toBe(54.8)

    const over = [{ memberId: 'm1', amount: 150, allocationMethod: 'direct' }]
    expect(isBalanced(over, 100)).toBe(false)
    expect(remainingPercent(over, 100)).toBe(-50)
  })

  it('avoids dividing by zero when the line amount is 0', () => {
    expect(remainingPercent([], 0)).toBe(0)
  })

  it('treats an empty draft as unbalanced', () => {
    expect(sumAllocation([])).toBe(0)
    expect(isBalanced([], 100)).toBe(false)
  })
})

// ===== 金额量化（QA-7）=====
// 全模块唯一的金额容差入口。裸浮点比较会把 1000.005 误判为「已平」，
// 放行一个后端必然回 422 的请求（ALLOCATION_TOTAL_MISMATCH）。

describe('toCents / quantizeAmount / isAmountEqual', () => {
  it('量化到整数分', () => {
    expect(toCents(0)).toBe(0)
    expect(toCents(1)).toBe(100)
    expect(toCents(1234.56)).toBe(123456)
    expect(toCents(-1234.56)).toBe(-123456)
  })

  it('非有限值归零，不产生 NaN 污染下游判等', () => {
    expect(toCents(Number.NaN)).toBe(0)
    expect(toCents(Number.POSITIVE_INFINITY)).toBe(0)
    expect(toCents(Number.NEGATIVE_INFINITY)).toBe(0)
    expect(isAmountEqual(Number.NaN, 0)).toBe(true)
  })

  it('消掉经典浮点累加噪声：0.1+0.2 == 0.3', () => {
    expect(0.1 + 0.2).not.toBe(0.3)
    expect(quantizeAmount(0.1 + 0.2)).toBe(0.3)
  })

  it('1000.005 向上进位到 1000.01（与后端 ROUND_HALF_UP 一致），与 1000 判不等', () => {
    // 后端 money(1000.005) = Decimal('1000.01')；量化到分得 100001
    expect(quantizeAmount(1000.005)).toBe(1000.01)
    expect(isAmountEqual(1000.005, 1000)).toBe(false)
    expect(isBalanced([{ memberId: 'm1', amount: 1000.005, allocationMethod: 'direct' }], 1000)).toBe(false)
  })

  it('1.005 同样进位到 1.01（Math.round(1.005*100) 会错成 100，正数侧曾误放行）', () => {
    // JS: 1.005 * 100 === 100.49999999999999 → Math.round → 100（错）
    // 后端: Decimal('1.005') 精确 → 1.01
    expect(1.005 * 100).toBe(100.49999999999999)
    expect(quantizeAmount(1.005)).toBe(1.01)
    expect(toCents(1.005)).toBe(101)
    expect(isAmountEqual(1.005, 1)).toBe(false)
  })

  it('1000.004 落到 1000.00，与 1000 判等（与后端 Decimal(20,2) 同口径）', () => {
    expect(quantizeAmount(1000.004)).toBe(1000)
    expect(isAmountEqual(1000.004, 1000)).toBe(true)
    expect(isBalanced([{ memberId: 'm1', amount: 1000.004, allocationMethod: 'direct' }], 1000)).toBe(true)
  })

  it('负数同样量化（远离零进位）；贷方负行金额按绝对值判等', () => {
    // JS Math.round 的 .5 是向 +∞ 取整，Decimal ROUND_HALF_UP 是远离零，故 -1000.005
    // 旧实现得-100000（-1000.00），后端得 -1000.01。现在按十进制字面量量化后一致。
    expect(toCents(-1000.005)).toBe(-100001)
    expect(quantizeAmount(-1000.005)).toBe(-1000.01)
    expect(isBalanced([{ memberId: 'm1', amount: 1000, allocationMethod: 'direct' }], -1000)).toBe(true)
    expect(isBalanced([{ memberId: 'm1', amount: 1000.005, allocationMethod: 'direct' }], -1000)).toBe(false)
  })

  it('符号对称：toCents(-v) === -toCents(v)（v=0 除外，见下条）', () => {
    for (const v of [1000.005, 1.005, 2.675, 0.125, 0.615, 1000]) {
      expect(toCents(-v)).toBe(-toCents(v))
    }
  })

  it('零与负零统一归一为 +0（避免 Object.is 断言被 -0 绊倒）', () => {
    expect(Object.is(toCents(0), 0)).toBe(true)
    expect(Object.is(toCents(-0), 0)).toBe(true)
    expect(Object.is(toCents(-0.004), 0)).toBe(true)
    // 负零只可能出现在「绝对值不足半分」的负数上；归一后下游按分比较结果不变
    expect(toCents(-0.004)).toBe(toCents(0))
  })

  it('diffOf / diffCents 输出的是量化后的两位小数', () => {
    const rows = [{ memberId: 'm1', amount: 0.1, allocationMethod: 'direct' },
      { memberId: 'm2', amount: 0.2, allocationMethod: 'direct' }]
    expect(diffCents(rows, 0.3)).toBe(0)
    expect(diffOf(rows, 0.3)).toBe(0)
    expect(diffOf([{ memberId: 'm1', amount: 1000.005, allocationMethod: 'direct' }], 1000)).toBe(0.01)
  })
})

describe('validateAllocations', () => {
  it('accepts a valid single-row full allocation', () => {
    expect(validateAllocations([{ memberId: 'm1', amount: 100, allocationMethod: 'direct' }], 100)).toBeNull()
  })

  it('requires at least one row and a member on every row', () => {
    expect(validateAllocations([], 100)).toContain('至少')
    expect(validateAllocations([{ memberId: '', amount: 10, allocationMethod: 'direct' }], 100)).toContain('归集对象')
  })

  it('rejects duplicate members, mirroring backend INVALID_ALLOCATION', () => {
    const duplicated: AllocationDraftRow[] = [
      { memberId: 'm1', amount: 50, allocationMethod: 'direct', allocationBasis: 'a' },
      { memberId: 'm1', amount: 50, allocationMethod: 'direct', allocationBasis: 'b' },
    ]
    expect(validateAllocations(duplicated, 100)).toContain('重复')
  })

  it('rejects non-positive amounts and amounts above the line amount', () => {
    expect(validateAllocations([{ memberId: 'm1', amount: 0, allocationMethod: 'direct' }], 100)).toContain('大于 0')
    expect(validateAllocations([{ memberId: 'm1', amount: 101, allocationMethod: 'direct' }], 100)).toContain('超过')
  })

  it('requires a basis when splitting across multiple members', () => {
    const missingBasis: AllocationDraftRow[] = [
      { memberId: 'm1', amount: 50, allocationMethod: 'direct', allocationBasis: '按面积' },
      { memberId: 'm2', amount: 50, allocationMethod: 'direct' },
    ]
    expect(validateAllocations(missingBasis, 100)).toContain('分摊依据')
  })
})

describe('splitEvenly', () => {
  it('makes the parts sum exactly to the total so no rounding gap remains', () => {
    const parts = splitEvenly(100, 3)
    expect(parts).toHaveLength(3)
    expect(Math.round(parts.reduce((a, b) => a + b, 0) * 100) / 100).toBe(100)
  })

  it('absorves the tail difference into the last part', () => {
    // 100 / 3 = 33.33…，前两条取 33.33，最后一条吃剩余 33.34
    expect(splitEvenly(100, 3)).toEqual([33.33, 33.33, 33.34])
  })

  it('handles a single part and a non-positive count', () => {
    expect(splitEvenly(100, 1)).toEqual([100])
    expect(splitEvenly(100, 0)).toEqual([])
  })

  it('uses the absolute value of a signed line amount', () => {
    expect(splitEvenly(-100, 2)).toEqual([50, 50])
  })
})

describe('patternExpression', () => {
  it('renders plus and minus terms into a readable formula', () => {
    const text = patternExpression([
      { mode: 'prefix', value: '4301', sign: 1 },
      { mode: 'prefix', value: '4301002', sign: -1 },
    ])
    expect(text).toContain('前缀 4301')
    expect(text).toContain('前缀 4301002')
    expect(text).toContain('−')
  })

  it('handles an empty pattern list', () => {
    expect(patternExpression([])).toBe('（未配置科目）')
  })
})
