/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 上手引导的**展示状态**（每个账套一份）。
 *
 * 这里只存「用户看过没 / 折叠没 / 跳过没」这类展示态；「这一项到底完成没」
 * 永远由后端 `/api/setup/readiness` 从业务数据判定。两者分开，才不会出现
 * 本地勾选假装完成的假进度。
 *
 * 键前缀与 `stores/workspaceView.ts` 一致（`solo:` + companyId），读写都做成
 * 可导出的纯函数，方便单测直接断言，不用 mount 组件。
 */

import { defineStore } from 'pinia'
import { ref, watch } from 'vue'
import { useAuthStore } from '@/stores/auth'
import type { SetupReadiness } from '@/api/types'

export const GUIDE_PREFIX = 'solo:guide:'
/** 就绪后再挂 7 天就归档，避免清单长期占着首页 */
export const ARCHIVE_AFTER_DAYS = 7
/** 建账超过 14 天仍未就绪 → 降级成一行，避免天天被同一块清单羞辱 */
export const EXPAND_WINDOW_DAYS = 14
/** 救援弹窗每日上限：宁可多提醒一次，也不要让用户以为是系统坏了 */
export const RESCUE_DAILY_LIMIT = 2
/** 同一个错误码的冷却时长（小时） */
export const RESCUE_COOLDOWN_HOURS = 24

export type ChecklistDisplay = 'expanded' | 'summary' | 'hidden'

export interface GuideState {
  version: 1
  collapsed: boolean
  /** 用户点「今天先不看」，当天不再展示 */
  dismissedAt: string | null
  /** 首次达到全部完成的时间，用来算归档 */
  readyAt: string | null
  archivedAt: string | null
  /** 新手引导（L0）弹过的时间：打开即写，Esc 之后不再反复弹 */
  l0SeenAt: string | null
  /** 用户主动跳过的可选项（如 accounting 的 legacy_import） */
  waived: string[]
  demoCompanyId: string | null
  /** 救援弹窗「不再提示」——按错误码粒度，静音一个码不该让别的码也不响 */
  rescueMutedCodes: string[]
  /** 近 24h 弹过的错误码与时间，用来给同码冷却 */
  rescueShownAt: Array<{ code: string; at: string }>
  /** 当日弹出计数，用来给每日上限封顶 */
  rescueDay: { date: string; count: number } | null
}

export function emptyGuideState(): GuideState {
  return {
    version: 1,
    collapsed: false,
    dismissedAt: null,
    readyAt: null,
    archivedAt: null,
    l0SeenAt: null,
    waived: [],
    demoCompanyId: null,
    rescueMutedCodes: [],
    rescueShownAt: [],
    rescueDay: null,
  }
}

export function guideStorageKey(companyId: string | null): string {
  return `${GUIDE_PREFIX}${companyId || 'default'}`
}

function parseGuideState(raw: string | null): GuideState {
  const base = emptyGuideState()
  if (!raw) return base
  try {
    const data = JSON.parse(raw) as Partial<GuideState> | null
    if (!data || typeof data !== 'object') return base
    const waived = Array.isArray(data.waived)
      ? [...new Set(data.waived.filter((item): item is string => typeof item === 'string'))]
      : []
    const rescueMutedCodes = Array.isArray(data.rescueMutedCodes)
      ? [
          ...new Set(
            data.rescueMutedCodes.filter((item): item is string => typeof item === 'string'),
          ),
        ]
      : []
    const rescueShownAt = Array.isArray(data.rescueShownAt)
      ? data.rescueShownAt.filter(
          (item): item is { code: string; at: string } =>
            Boolean(item) && typeof (item as { code?: unknown }).code === 'string'
            && typeof (item as { at?: unknown }).at === 'string',
        )
      : []
    return {
      ...base,
      ...data,
      version: 1,
      waived,
      rescueMutedCodes,
      rescueShownAt,
    }
  } catch {
    return base
  }
}

export function readGuideState(companyId: string | null): GuideState {
  try {
    return parseGuideState(localStorage.getItem(guideStorageKey(companyId)))
  } catch {
    return emptyGuideState()
  }
}

export function writeGuideState(
  companyId: string | null,
  patch: Partial<GuideState>,
): GuideState {
  const current = readGuideState(companyId)
  const next: GuideState = {
    ...current,
    ...patch,
    version: 1,
    waived: patch.waived
      ? [...new Set(patch.waived.filter((item) => typeof item === 'string'))]
      : current.waived,
  }
  try {
    localStorage.setItem(guideStorageKey(companyId), JSON.stringify(next))
  } catch {
    // 隐私模式 / 配额满：展示态丢失不影响主流程，下次按默认值展示即可
  }
  return next
}

export function resetGuideState(companyId: string | null): void {
  try {
    localStorage.removeItem(guideStorageKey(companyId))
  } catch {
    // ignore
  }
}

function startOfLocalDay(value: Date): Date {
  return new Date(value.getFullYear(), value.getMonth(), value.getDate())
}

/** 只接受带时间的 ISO 串（`company.created_at` 就是）；纯日期串会按 UTC 解析，东八区差一天。 */
export function daysSince(iso: string | null | undefined, now: Date = new Date()): number | null {
  if (!iso) return null
  const parsed = new Date(iso)
  if (Number.isNaN(parsed.getTime())) return null
  const diff = startOfLocalDay(now).getTime() - startOfLocalDay(parsed).getTime()
  return Math.floor(diff / 86_400_000)
}

export function isArchived(state: GuideState, now: Date = new Date()): boolean {
  if (state.archivedAt) return true
  if (!state.readyAt) return false
  const elapsed = daysSince(state.readyAt, now)
  return elapsed !== null && elapsed >= ARCHIVE_AFTER_DAYS
}

export function isDismissedToday(state: GuideState, now: Date = new Date()): boolean {
  if (!state.dismissedAt) return false
  const elapsed = daysSince(state.dismissedAt, now)
  return elapsed === 0
}

/**
 * 首页清单该长什么样：
 * - hidden：今天已经关掉 / 没有数据 / 接口失败 / 当前是演示账套
 * - expanded：完整 5 行（新手期）
 * - summary：压成一行（已就绪、已归档、或建账超过 14 天仍没做完）
 *
 * 演示账套的样本数据会让 readiness 全部判为完成，但那是样本不是用户进度，
 * 所以直接隐藏——由首页的演示提示条引导用户去建自己的账套。
 */
export function resolveChecklistDisplay(
  state: GuideState,
  readiness: SetupReadiness | null,
  createdAt: string | null | undefined,
  isDemo = false,
  now: Date = new Date(),
): ChecklistDisplay {
  if (!readiness) return 'hidden'
  if (isDemo) return 'hidden'
  if (isDismissedToday(state, now)) return 'hidden'
  if (readiness.ready) return 'summary'
  if (state.collapsed || isArchived(state, now)) return 'summary'
  const age = daysSince(createdAt, now)
  if (age !== null && age >= EXPAND_WINDOW_DAYS) return 'summary'
  return 'expanded'
}

export const useGuideStateStore = defineStore('guideState', () => {
  const authStore = useAuthStore()
  const state = ref<GuideState>(readGuideState(authStore.currentCompanyId))

  function load() {
    state.value = readGuideState(authStore.currentCompanyId)
  }

  function patch(next: Partial<GuideState>) {
    state.value = writeGuideState(authStore.currentCompanyId, next)
    return state.value
  }

  function reset() {
    resetGuideState(authStore.currentCompanyId)
    load()
  }

  /** 记录「刚刚全部完成」的时刻，用于 7 天后归档 */
  function markReady() {
    if (!state.value.readyAt) patch({ readyAt: new Date().toISOString() })
  }

  function markL0Seen() {
    patch({ l0SeenAt: new Date().toISOString() })
  }

  watch(
    () => authStore.currentCompanyId,
    () => load(),
  )

  return { state, load, patch, reset, markReady, markL0Seen }
})

/* ── 救援弹窗的克制（纯函数，便于 vitest 直接断言） ── */

export type RescueSuppression = 'muted' | 'cooldown' | 'daily'

/** 本地日期键 YYYY-MM-DD；不能用 toISOString（按 UTC 解析，东八区会差一天） */
export function localDayKey(now: Date = new Date()): string {
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

function recentShown(state: GuideState, now: Date) {
  return state.rescueShownAt.filter((item) => {
    const hours = daysSince(item.at, now)
    return hours !== null && hours * 24 < RESCUE_COOLDOWN_HOURS
  })
}

/**
 * 这个错误码现在该不该弹救援弹窗。返回 null = 该弹。
 *
 * 顺序刻意是「静音 → 冷却 → 每日上限」：用户明确说过不再提示的，优先级最高。
 */
export function rescueSuppression(
  state: GuideState,
  code: string,
  now: Date = new Date(),
): RescueSuppression | null {
  if (state.rescueMutedCodes.includes(code)) return 'muted'
  if (recentShown(state, now).some((item) => item.code === code)) return 'cooldown'
  const day = localDayKey(now)
  if (state.rescueDay && state.rescueDay.date === day && state.rescueDay.count >= RESCUE_DAILY_LIMIT) {
    return 'daily'
  }
  return null
}

/** 记一次弹出：刷新冷却窗口与当日计数。 */
export function noteRescueShown(
  state: GuideState,
  code: string,
  now: Date = new Date(),
): GuideState {
  const day = localDayKey(now)
  const count = state.rescueDay && state.rescueDay.date === day ? state.rescueDay.count + 1 : 1
  return {
    ...state,
    rescueShownAt: [...recentShown(state, now), { code, at: now.toISOString() }],
    rescueDay: { date: day, count },
  }
}

export function muteRescueCode(state: GuideState, code: string): GuideState {
  if (state.rescueMutedCodes.includes(code)) return state
  return { ...state, rescueMutedCodes: [...state.rescueMutedCodes, code] }
}
