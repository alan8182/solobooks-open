/**
 * Copyright (c) 2026 芷子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0.
 */

/**
 * 真实书本左装订约束下的 2D 卷页几何求解与书叠厚度计算。
 *
 * 物理模型：
 * - 页面矩形为 [0, W] × [0, H]，左侧装订轴钉死在 x = spineX（纸张不可拉伸）。
 * - 从右角 C（右下角或右上角）掀起到目标角点 P 时，约束 P 到同侧装订枢纽点距离
 *   ≤ (W - spineX)，且到对侧装订角距离 ≤ hypot(W - spineX, H)。
 * - 折痕线 L 是 C 与 P 的垂直平分线：L 将页面拆成仍在平铺的 flatClip（含装订侧）
 *   与掀起区域 peeledClip（角侧）；折起的纸背即为整页绕折痕线 L 做正交镜像
 *   (backMatrix, det = -1) 并按 peeledClip 在本地坐标系裁剪后的三角形/梯形区域。
 */

export type CurlCorner = 'br' | 'tr'

interface Pt {
  x: number
  y: number
}

export interface PageCurlGeometry {
  /** 是否处于完全平铺状态（折痕未进入页面） */
  isFlat: boolean
  /** 归一化翻页进度（0 = 平铺，1 = 完全翻至左侧） */
  progress: number
  /** 仍平铺于右侧的页面多边形（clip-path polygon，%） */
  flatClip: string
  /** 被掀起的多边形（用于本地坐标裁剪折起纸背 & 底层折痕投影，%） */
  peeledClip: string
  /** 折起纸背绕折痕线 L 镜像变换（CSS matrix，需配合 transform-origin: 0 0） */
  backMatrix: string
  /** 折痕线中点 M（像素坐标） */
  foldMidX: number
  foldMidY: number
  /** 折痕线法向角（度，从折痕指向掀起角一侧） */
  normalAngleDeg: number
  /** 折起页片末端收合淡出透明度 */
  flapOpacity: number
  /** 底层投影强度（0..1） */
  shadowOpacity: number
  /** 卷曲圆柱高光与内折缝阴影强度（0..1） */
  ridgeStrength: number
  /** 约束后的角点坐标（像素） */
  tipX: number
  tipY: number
}

function clamp01(value: number): number {
  if (!Number.isFinite(value)) return 0
  return Math.min(1, Math.max(0, value))
}

function clampToCircle(p: Pt, center: Pt, radius: number): Pt {
  const dx = p.x - center.x
  const dy = p.y - center.y
  const dist = Math.hypot(dx, dy)
  if (dist <= radius || dist < 1e-9) return p
  const scale = radius / dist
  return { x: center.x + dx * scale, y: center.y + dy * scale }
}

/** 物理不可拉伸约束：限制掀起的页角落在左装订枢纽半径交集内 */
export function constrainCurlTip(
  px: number,
  py: number,
  width: number,
  height: number,
  spineX: number,
  corner: CurlCorner,
): Pt {
  const w = Math.max(1, width)
  const h = Math.max(1, height)
  const sx = Math.min(Math.max(0, spineX), w - 1)
  const pivotSame: Pt = { x: sx, y: corner === 'br' ? h : 0 }
  const pivotOpp: Pt = { x: sx, y: corner === 'br' ? 0 : h }
  const rSame = Math.max(1, w - sx)
  const rOpp = Math.hypot(rSame, h)

  let out: Pt = {
    x: Number.isFinite(px) ? px : w,
    y: Number.isFinite(py) ? py : corner === 'br' ? h : 0,
  }
  // 交替向两个凸圆盘投影，保证同时满足两条装订半径约束
  for (let i = 0; i < 4; i += 1) {
    out = clampToCircle(out, pivotSame, rSame)
    out = clampToCircle(out, pivotOpp, rOpp)
  }
  return out
}

/** 按钮/键盘自动翻页的自然弧线轨迹点 */
export function arcPointForProgress(
  progress: number,
  width: number,
  height: number,
  spineX: number,
  corner: CurlCorner,
): Pt {
  const p = clamp01(progress)
  const w = Math.max(1, width)
  const h = Math.max(1, height)
  const sx = Math.min(Math.max(0, spineX), w - 1)
  const theta = p * Math.PI
  const rawX = sx + (w - sx) * Math.cos(theta)
  const liftY = h * 0.24 * Math.sin(theta)
  const rawY = corner === 'br' ? h - liftY : liftY
  return constrainCurlTip(rawX, rawY, w, h, sx, corner)
}

function clipHalfPlane(poly: Pt[], nx: number, ny: number, dLine: number, keepSide: 'flat' | 'peeled'): Pt[] {
  const out: Pt[] = []
  for (let i = 0; i < poly.length; i += 1) {
    const a = poly[i]
    const b = poly[(i + 1) % poly.length]
    const da = a.x * nx + a.y * ny - dLine
    const db = b.x * nx + b.y * ny - dLine
    const inA = keepSide === 'flat' ? da <= 1e-6 : da >= -1e-6
    const inB = keepSide === 'flat' ? db <= 1e-6 : db >= -1e-6
    if (inA) out.push(a)
    if (inA !== inB) {
      const denom = da - db
      if (Math.abs(denom) > 1e-9) {
        const t = da / denom
        out.push({ x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t })
      }
    }
  }
  return out
}

function polygonArea(poly: Pt[]): number {
  let sum = 0
  for (let i = 0; i < poly.length; i += 1) {
    const a = poly[i]
    const b = poly[(i + 1) % poly.length]
    sum += a.x * b.y - b.x * a.y
  }
  return Math.abs(sum) / 2
}

function toClipPolygon(poly: Pt[], width: number, height: number): string {
  const unique: Pt[] = []
  for (const pt of poly) {
    const prev = unique[unique.length - 1]
    if (!prev || Math.abs(prev.x - pt.x) > 1e-4 || Math.abs(prev.y - pt.y) > 1e-4) {
      unique.push(pt)
    }
  }
  if (unique.length < 3 || polygonArea(unique) < 0.5) {
    return 'polygon(0% 0%, 0% 0%, 0% 0%)'
  }
  return `polygon(${unique
    .map((pt) => `${((pt.x / width) * 100).toFixed(2)}% ${((pt.y / height) * 100).toFixed(2)}%`)
    .join(', ')})`
}

function flatGeometry(width: number, height: number, corner: CurlCorner): PageCurlGeometry {
  const tipY = corner === 'br' ? height : 0
  return {
    isFlat: true,
    progress: 0,
    flatClip: 'polygon(0% 0%, 100% 0%, 100% 100%, 0% 100%)',
    peeledClip: 'polygon(0% 0%, 0% 0%, 0% 0%)',
    backMatrix: 'matrix(1, 0, 0, 1, 0, 0)',
    foldMidX: width,
    foldMidY: tipY,
    normalAngleDeg: 0,
    flapOpacity: 1,
    shadowOpacity: 0,
    ridgeStrength: 0,
    tipX: width,
    tipY,
  }
}

function buildCurlGeometry(
  tip: Pt,
  width: number,
  height: number,
  spineX: number,
  corner: CurlCorner,
  progressOverride?: number,
): PageCurlGeometry {
  const w = Math.max(1, width)
  const h = Math.max(1, height)
  const sx = Math.min(Math.max(0, spineX), w - 1)
  const anchor: Pt = { x: w, y: corner === 'br' ? h : 0 }
  const dx = anchor.x - tip.x
  const dy = anchor.y - tip.y
  const len = Math.hypot(dx, dy)

  if (len < 1) {
    return flatGeometry(w, h, corner)
  }

  const nx = dx / len
  const ny = dy / len
  const midX = (anchor.x + tip.x) / 2
  const midY = (anchor.y + tip.y) / 2
  const dLine = midX * nx + midY * ny

  const rect: Pt[] = [
    { x: 0, y: 0 },
    { x: w, y: 0 },
    { x: w, y: h },
    { x: 0, y: h },
  ]
  const flatPoly = clipHalfPlane(rect, nx, ny, dLine, 'flat')
  const peeledPoly = clipHalfPlane(rect, nx, ny, dLine, 'peeled')

  const a = 1 - 2 * nx * nx
  const b = -2 * nx * ny
  const c = b
  const d = 1 - 2 * ny * ny
  const tx = 2 * dLine * nx
  const ty = 2 * dLine * ny
  const backMatrix = `matrix(${a.toFixed(6)}, ${b.toFixed(6)}, ${c.toFixed(6)}, ${d.toFixed(6)}, ${tx.toFixed(2)}, ${ty.toFixed(2)})`

  const progress =
    progressOverride !== undefined
      ? clamp01(progressOverride)
      : clamp01((w - tip.x) / (2 * Math.max(1, w - sx)))

  const rawFlap = progress <= 0.78 ? 1 : clamp01(1 - (progress - 0.78) / 0.22)
  const flapOpacity = rawFlap < 1e-6 ? 0 : rawFlap
  const rawShadow = Math.sin(progress * Math.PI)
  const shadowOpacity = rawShadow < 1e-6 ? 0 : rawShadow
  const rawRidge = Math.min(1, len / 48) * flapOpacity
  const ridgeStrength = rawRidge < 1e-6 ? 0 : rawRidge

  return {
    isFlat: false,
    progress,
    flatClip: toClipPolygon(flatPoly, w, h),
    peeledClip: toClipPolygon(peeledPoly, w, h),
    backMatrix,
    foldMidX: midX,
    foldMidY: midY,
    normalAngleDeg: (Math.atan2(ny, nx) * 180) / Math.PI,
    flapOpacity,
    shadowOpacity,
    ridgeStrength,
    tipX: tip.x,
    tipY: tip.y,
  }
}

/** 由手势/指针的实时角点坐标计算装订约束下的真实卷页几何 */
export function computePageCurlFromTip(
  px: number,
  py: number,
  width: number,
  height: number,
  spineX = 28,
  corner: CurlCorner = 'br',
): PageCurlGeometry {
  const tip = constrainCurlTip(px, py, width, height, spineX, corner)
  return buildCurlGeometry(tip, width, height, spineX, corner)
}

/** 由翻页进度（0=平铺 1=翻至左侧）计算沿着自然弧线展开的真实卷页几何 */
export function computePageCurl(
  progress: number,
  width: number,
  height: number,
  spineX = 28,
  corner: CurlCorner = 'br',
): PageCurlGeometry {
  const p = clamp01(progress)
  if (p <= 1e-4) {
    return flatGeometry(Math.max(1, width), Math.max(1, height), corner)
  }
  const tip = arcPointForProgress(p, width, height, spineX, corner)
  return buildCurlGeometry(tip, width, height, spineX, corner, p)
}

/** 松手后判定提交翻页还是回弹原页（支持拖拽比例与甩动水平速度 px/ms） */
export function decideDragOutcome(
  progress: number,
  velocityX: number,
  dir: 'next' | 'prev',
): 'commit' | 'revert' {
  const p = clamp01(progress)
  if (dir === 'next') {
    return p >= 0.28 || velocityX <= -0.35 ? 'commit' : 'revert'
  }
  return p <= 0.72 || velocityX >= 0.35 ? 'commit' : 'revert'
}

/** 根据当前页码与总页数计算左（已翻）/右（未翻）纸叠视觉厚度（px） */
export function computeStackThickness(
  pageIndex: number,
  totalPages: number,
): { leftPx: number; rightPx: number } {
  if (totalPages <= 1) return { leftPx: 0, rightPx: 0 }
  const totalPx = Math.min(14, Math.max(4, Math.round(totalPages * 0.6)))
  const clampedIndex = Math.min(Math.max(0, pageIndex), totalPages - 1)
  const ratio = clampedIndex / Math.max(1, totalPages - 1)
  const leftPx = Math.round(totalPx * ratio)
  return { leftPx, rightPx: Math.max(0, totalPx - leftPx) }
}

/** 翻页缓动（缓入缓出三次） */
export function curlEase(t: number): number {
  const clamped = clamp01(t)
  return clamped < 0.5 ? 4 * clamped * clamped * clamped : 1 - Math.pow(-2 * clamped + 2, 3) / 2
}
