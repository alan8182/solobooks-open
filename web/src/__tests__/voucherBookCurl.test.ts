/**
 * Copyright (c) 2026 芷子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'

import {
  computePageCurl,
  computePageCurlFromTip,
  computeStackThickness,
  constrainCurlTip,
  curlEase,
  decideDragOutcome,
} from '@/utils/voucherBookCurl'

function parseMatrix(matrixStr: string): [number, number, number, number, number, number] {
  const nums = matrixStr.match(/-?[\d.]+/g) || []
  return nums.map(Number) as [number, number, number, number, number, number]
}

function applyMatrix(m: [number, number, number, number, number, number], x: number, y: number) {
  return { x: m[0] * x + m[2] * y + m[4], y: m[1] * x + m[3] * y + m[5] }
}

describe('voucherBookCurl（左装订约束真实书本卷页与书叠厚度）', () => {
  it('computePageCurl：边界 0 / 0.5 / 1 与越界夹取', () => {
    const start = computePageCurl(0, 1000, 700, 28, 'br')
    expect(start.isFlat).toBe(true)
    expect(start.progress).toBe(0)
    expect(start.shadowOpacity).toBe(0)
    expect(start.ridgeStrength).toBe(0)
    expect(start.peeledClip).toBe('polygon(0% 0%, 0% 0%, 0% 0%)')

    const mid = computePageCurl(0.5, 1000, 700, 28, 'br')
    expect(mid.isFlat).toBe(false)
    expect(mid.progress).toBe(0.5)
    expect(mid.flapOpacity).toBe(1)
    expect(mid.shadowOpacity).toBeCloseTo(1)
    expect(mid.ridgeStrength).toBeGreaterThan(0)
    expect(mid.peeledClip).not.toBe('polygon(0% 0%, 0% 0%, 0% 0%)')

    const end = computePageCurl(1, 1000, 700, 28, 'br')
    expect(end.progress).toBe(1)
    expect(end.flapOpacity).toBe(0)
    expect(end.shadowOpacity).toBe(0)
    expect(end.ridgeStrength).toBe(0)

    expect(computePageCurl(-0.5, 1000, 700, 28, 'br')).toEqual(start)
    expect(computePageCurl(1.5, 1000, 700, 28, 'br')).toEqual(end)
  })

  it('constrainCurlTip & backMatrix：装订枢纽半径约束与正交镜像精确映射', () => {
    const W = 1000
    const H = 700
    const S = 28
    // 故意把角点拖出页面很远，必须被约束在同侧枢纽半径 W-S 与对侧枢纽半径 hypot(W-S, H) 内
    const constrained = constrainCurlTip(-800, -400, W, H, S, 'br')
    expect(Math.hypot(constrained.x - S, constrained.y - H)).toBeLessThanOrEqual(W - S + 1e-3)
    expect(Math.hypot(constrained.x - S, constrained.y - 0)).toBeLessThanOrEqual(Math.hypot(W - S, H) + 1e-3)

    const geom = computePageCurlFromTip(520, 460, W, H, S, 'br')
    expect(geom.isFlat).toBe(false)
    const m = parseMatrix(geom.backMatrix)
    // 行列式为 -1（正交镜像反射）
    expect(m[0] * m[3] - m[1] * m[2]).toBeCloseTo(-1, 4)
    // 右下角 (W, H) 精确映射到当前角点 (tipX, tipY)
    const mappedCorner = applyMatrix(m, W, H)
    expect(mappedCorner.x).toBeCloseTo(geom.tipX, 1)
    expect(mappedCorner.y).toBeCloseTo(geom.tipY, 1)
    // 折痕中点 M 是反射的不动点
    const mappedMid = applyMatrix(m, geom.foldMidX, geom.foldMidY)
    expect(mappedMid.x).toBeCloseTo(geom.foldMidX, 1)
    expect(mappedMid.y).toBeCloseTo(geom.foldMidY, 1)
  })

  it('decideDragOutcome：按拖拽进度与甩动速度判定提交或回弹', () => {
    expect(decideDragOutcome(0.3, 0, 'next')).toBe('commit')
    expect(decideDragOutcome(0.15, -0.45, 'next')).toBe('commit')
    expect(decideDragOutcome(0.15, -0.1, 'next')).toBe('revert')

    expect(decideDragOutcome(0.68, 0, 'prev')).toBe('commit')
    expect(decideDragOutcome(0.88, 0.45, 'prev')).toBe('commit')
    expect(decideDragOutcome(0.88, 0.1, 'prev')).toBe('revert')
  })

  it('computeStackThickness：左已翻/右未翻厚度动态守恒且封顶 14px', () => {
    expect(computeStackThickness(0, 1)).toEqual({ leftPx: 0, rightPx: 0 })

    const first = computeStackThickness(0, 14)
    const mid = computeStackThickness(7, 14)
    const last = computeStackThickness(13, 14)
    expect(first.leftPx).toBe(0)
    expect(first.rightPx).toBeGreaterThan(0)
    expect(mid.leftPx).toBeGreaterThan(0)
    expect(mid.rightPx).toBeGreaterThan(0)
    expect(last.rightPx).toBe(0)
    expect(last.leftPx).toBe(first.rightPx)

    const thick = computeStackThickness(0, 200)
    expect(thick.rightPx).toBe(14)
  })

  it('curlEase：缓入缓出三次曲线', () => {
    expect(curlEase(0)).toBe(0)
    expect(curlEase(1)).toBe(1)
    expect(curlEase(0.5)).toBe(0.5)
    expect(curlEase(0.25)).toBeLessThan(0.25)
    expect(curlEase(0.75)).toBeGreaterThan(0.75)
  })
})
