/**
 * Copyright (c) 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 救援弹窗的队列。
 *
 * 用队列而不是单例 ref：批量过账、批量导入这类场景会一次命中多条错误，
 * 单 ref 会被后一次覆盖，用户只看到最后一条——那等于没救。
 *
 * 与 `composables/backfillConfirm.ts` 的区别（两者并存，互不替代）：
 * - backfillConfirm 是「每次调用一份 state + Promise 哨兵」，出路是**带参重试同一动作**，
 *   调用方需要 `await`；
 * - 这里是全局单例队列，出路是**导航到某个页面**，调用方不需要等待。
 */
import { computed, ref } from 'vue'
import { rescueRuleFor } from '@/utils/errors'
import type { RescueRule } from '@/utils/rescueRules'
import { useAuthStore } from '@/stores/auth'
import {
  muteRescueCode,
  noteRescueShown,
  readGuideState,
  rescueSuppression,
  writeGuideState,
} from '@/stores/guideState'

export interface RescueRequest {
  key: number
  rule: RescueRule
  ctx: Record<string, unknown>
  reason: string
}

export const rescueQueue = ref<RescueRequest[]>([])
export const rescueVisible = computed(() => rescueQueue.value.length > 0)
export const rescuePendingCount = computed(() => rescueQueue.value.length)

let sequence = 0

function resolveReason(rule: RescueRule, ctx: Record<string, unknown>): string {
  return typeof rule.reason === 'function' ? rule.reason(ctx) : rule.reason
}

/** 当前账套 id。pinia 未激活时（单测）退回 null，展示态落到 'default' 键。 */
function currentCompanyId(): string | null {
  try {
    return useAuthStore().currentCompanyId
  } catch {
    return null
  }
}

export function rescueCurrent(): RescueRequest | null {
  return rescueQueue.value[0] ?? null
}

/**
 * 把一个错误变成救援弹窗。返回 true = 已接管，调用点不要再 toast。
 *
 * 三种「不该弹」的情况会返回 false（**不是静默丢弃，而是让调用点退回普通 toast**）：
 * 用户说过不再提示、同码 24h 内弹过、今日已达上限——错误本身必须还看得见。
 */
export function offerRescue(err: unknown, options: { companyId?: string | null } = {}, now: Date = new Date()): boolean {
  const match = rescueRuleFor(err)
  if (!match) return false

  const companyId = options.companyId ?? currentCompanyId()
  const state = readGuideState(companyId)
  if (rescueSuppression(state, match.rule.code, now)) return false
  writeGuideState(companyId, noteRescueShown(state, match.rule.code, now))

  sequence += 1
  rescueQueue.value = [
    ...rescueQueue.value,
    {
      key: sequence,
      rule: match.rule,
      ctx: match.ctx,
      reason: resolveReason(match.rule, match.ctx),
    },
  ]
  return true
}

/** 关掉当前这条；队列里还有就自动接着弹。 */
export function rescueNext() {
  rescueQueue.value = rescueQueue.value.slice(1)
}

/** 「不再提示这个错误」：只静音这一个码，不是所有错误。 */
export function muteCurrentRescue(companyId?: string | null) {
  const current = rescueCurrent()
  if (!current) return
  const id = companyId ?? currentCompanyId()
  const state = readGuideState(id)
  writeGuideState(id, muteRescueCode(state, current.rule.code))
  rescueNext()
}

export function clearRescueQueue() {
  rescueQueue.value = []
}
