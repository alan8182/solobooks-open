/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 *
 * 导入/对账类批量动作的跨期补记确认。
 *
 * 后端在「单据所属期间已关账」时默认抛 PERIOD_CLOSED（不静默换期），
 * context 里带 canBackfill 与 suggestedPostingPeriod。这里把它变成一个
 * 「确认补记到 X 月 + 填原因」的弹窗：用户点确认后用 allowBackfill 重试
 * 同一个动作，动作本身的成功处理逻辑不受影响（调用点仍是线性写法）。
 */

import { reactive } from 'vue'

export interface BackfillContext {
  canBackfill: boolean
  suggestedPostingPeriod: string | null
  crossQuarter: boolean
  crossYear: boolean
  period?: string
}

const CANCELLED = Symbol('backfill-cancelled')

export function isBackfillCancelled(err: unknown): boolean {
  return err === CANCELLED
}

/** 从 409 PERIOD_CLOSED 里取出补记上下文；不是这种情况返回 null。 */
export function readBackfillContext(err: unknown): BackfillContext | null {
  const detail = (err as { response?: { data?: { detail?: Record<string, unknown> } } })
    ?.response?.data?.detail
  if (!detail || detail.code !== 'PERIOD_CLOSED') return null
  const context = (detail.context || {}) as Record<string, unknown>
  if (!context.canBackfill) return null
  return {
    canBackfill: true,
    suggestedPostingPeriod: (context.suggestedPostingPeriod as string) || null,
    crossQuarter: Boolean(context.crossQuarter),
    crossYear: Boolean(context.crossYear),
    period: context.period as string | undefined,
  }
}

export function useBackfillConfirm() {
  const state = reactive({
    show: false,
    targetPeriod: '',
    businessPeriod: '',
    crossQuarter: false,
    reason: '',
  })

  let resolver: ((reason: string | null) => void) | null = null

  function askUser(target: string, businessPeriod: string, crossQuarter: boolean): Promise<string | null> {
    state.targetPeriod = target
    state.businessPeriod = businessPeriod
    state.crossQuarter = crossQuarter
    state.reason = ''
    state.show = true
    return new Promise<string | null>((resolve) => {
      resolver = resolve
    })
  }

  function settle(reason: string | null) {
    state.show = false
    const pending = resolver
    resolver = null
    pending?.(reason)
  }

  function submit() {
    const reason = state.reason.trim()
    if (!reason) return
    settle(reason)
  }

  function cancel() {
    settle(null)
  }

  /**
   * 执行动作；遇到「期间已关账」时先弹确认框，用户填原因后带 allowBackfill 重试。
   * 用户取消时抛出一个可识别的哨兵，调用点用 isBackfillCancelled 跳过报错。
   */
  async function withBackfill<T>(
    action: (allowBackfill: boolean, reason?: string) => Promise<T>,
  ): Promise<T> {
    try {
      return await action(false)
    } catch (err) {
      const ctx = readBackfillContext(err)
      if (!ctx) throw err
      const reason = await askUser(
        ctx.suggestedPostingPeriod || '',
        ctx.period || '',
        ctx.crossQuarter,
      )
      if (!reason) throw CANCELLED
      return await action(true, reason)
    }
  }

  return { state, withBackfill, submit, cancel }
}
