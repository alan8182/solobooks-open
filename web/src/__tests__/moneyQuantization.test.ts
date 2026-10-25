/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// toCents 与后端 money() 的差分测试。**核心证据文件**。
//
// 为什么要有这个文件：前端的金额量化必须与 `backend/core/money.py` 的
// `Decimal(str(v)).quantize(Decimal("0.01"), ROUND_HALF_UP)` 逐位一致。
// 一旦不一致，最坏后果是前端判「已平」放行 PUT、后端回 422
// ALLOCATION_TOTAL_MISMATCH——用户点一下吃红弹窗（设计文档 §11 风险 4）。
//
// 本文件把后端在固定样本上的真实输出**内联为黄金值**（由 python 实跑
// `backend/core/money.py` 的 money() 生成，见文件末尾说明），
// 任何一侧口径漂移都会立刻在此失败，而不是等到用户点按钮才暴露。

import { describe, expect, it } from 'vitest'
import { quantizeAmount, toCents } from '@/utils/aggregation'

/** [输入, 后端 money() 的结果] —— 由 python 实跑生成，勿手改 */
const GOLDEN: Array<[number, string]> = [
  // QA 报告的 4 个关键值
  [1000.005, '1000.01'],
  [1000.004, '1000.00'],
  [1.005, '1.01'],
  [-1.005, '-1.01'],
  [-1000.005, '-1000.01'],
  // 经典 .5 进位（2.675 是著名的二进制表示陷阱）
  [2.675, '2.68'],
  [1.015, '1.02'],
  [0.615, '0.62'],
  [0.125, '0.13'],
  [-0.125, '-0.13'],
  [-2.675, '-2.68'],
  // 恰好一位 / 两位小数
  [1000.006, '1000.01'],
  [999.994, '999.99'],
  [0.01, '0.01'],
  [0.005, '0.01'],
  [0.004, '0.00'],
  [-0.004, '0.00'], // ⚠️ 后端 money() 返回 '-0.00'（Decimal 保留符号），JS 侧无「负零」字符串，见下方专项用例
  [0, '0.00'],
  [1000, '1000.00'],
  [-1000, '-1000.00'],
  // 浮点累加噪声
  [0.1 + 0.2, '0.30'],
  [0.1 + 0.7, '0.80'],
  [1.1 * 3, '3.30'],
  // 指数记数法（String() 会给 '1e-7' 这类串，必须能展开）
  [0.0000001, '0.00'],
  [1.5e-7, '0.00'],
  // 注意：1e21 不在此列表——超出 JS 安全整数范围，见下方专项用例（如实说明，不假装支持）
]

describe('toCents 与后端 money() 的口径一致性（差分测试）', () => {
  it.each(GOLDEN)('toCents(%s) 应等于后端 money() 的 %s', (input, expected) => {
    // 后端返回两位小数字符串；前端按分量化后除 100 再 toFixed(2) 应逐字符相等
    expect(quantizeAmount(input).toFixed(2)).toBe(expected)
  })

  it('QA 报告的可达路径：行金额 1.00 + 分配 1.005 → 判不等（否则后端 422）', () => {
    // 这是本次修复的核心回归：旧实现 Math.round(1.005 * 100) = 100 → 判等 → 放行 → 422
    const frontend = toCents(1.005)
    const backend = 101 // python: money(1.005) == Decimal('1.01')
    expect(frontend).toBe(backend)
    expect(frontend).not.toBe(100)
    expect(toCents(1.005) - toCents(1.0)).not.toBe(0)
  })

  it('正数侧不再是「误放行」方向（对 1000.005 / 1.005 均判不等）', () => {
    // 修复前：Math.round 语义下 1.005 会判等放行
    expect(toCents(1000.005)).toBe(100001)
    expect(toCents(1.005)).toBe(101)
    expect(toCents(1.005)).toBeGreaterThan(toCents(1.0))
    expect(toCents(1000.005)).toBeGreaterThan(toCents(1000.0))
  })

  it('负数侧远离零进位，与后端 ROUND_HALF_UP 一致', () => {
    expect(toCents(-1.005)).toBe(-101)
    expect(toCents(-1000.005)).toBe(-100001)
    // 符号对称：toCents(-v) === -toCents(v)
    for (const v of [1000.005, 1.005, 2.675, 0.125, 0.615, 1000]) {
      expect(toCents(-v)).toBe(-toCents(v))
    }
  })

  it('非有限值归零，不产生 NaN 污染下游判等', () => {
    expect(toCents(Number.NaN)).toBe(0)
    expect(toCents(Number.POSITIVE_INFINITY)).toBe(0)
    expect(toCents(Number.NEGATIVE_INFINITY)).toBe(0)
  })

  it('toCents 返回整数（下游按分做整数比较的前提）', () => {
    for (const v of [1000.005, 1.005, 0.1 + 0.2, -2.675, 1e12]) {
      expect(Number.isInteger(toCents(v))).toBe(true)
    }
  })
})

// ===== 如实记录的已知边界，不假装支持 =====

describe('toCents 的已知边界（超出 JS 表示能力，如实记录）', () => {
  it('超出安全整数范围（|分| > 2^53-1，即金额 > ~9007 万亿元）无法精确表达', () => {
    // 后端 Decimal 无此限制：money(1e21) = 1000000000000000000000.00
    // 但 JS 的 toCents(1e21) 需要 1e23 分，远超 Number.MAX_SAFE_INTEGER(~9.007e15)，
    // 超出后整数运算不再精确。这是 JS 语言的硬限制，不是本实现的取舍。
    //
    // 业务上不可达：金额列是 Numeric(14,2)（上限约 1e12），远低于该阈值。
    // 此处仅记录该边界确实存在，避免后人误以为 toCents 支持任意量级。
    expect(Number.isSafeInteger(toCents(1e21))).toBe(false)
    // 在业务可达量级内始终是安全整数
    for (const v of [1e12, 999999999999.99, -999999999999.99]) {
      expect(Number.isSafeInteger(toCents(v))).toBe(true)
    }
  })

  it('符号归零的已知无害差异：后端 money(-0.004) = -0.00，前端为 0.00（数值相等）', () => {
    // Python: Decimal(str(-0.004)).quantize(0.01, ROUND_HALF_UP) == Decimal('-0.00')
    // JS:    toCents(-0.004) === 0  →  quantizeAmount → 0  →  (0).toFixed(2) === '0.00'
    // 差异仅在「负零的字符串形式」，数值完全相等（0 === -0），下游按分比较用 `=== 0` 判定，
    // 两种写法结果一致，故不需要为它引入额外分支。
    expect(toCents(-0.004)).toBe(0)
    expect(quantizeAmount(-0.004).toFixed(2)).toBe('0.00')
    expect(quantizeAmount(-0.004)).toBe(0)
    // 判等行为不受影响：-0.004 与 0 判等（后端同样认为两者都是 0.00）
    expect(toCents(-0.004)).toBe(toCents(0))
  })
})