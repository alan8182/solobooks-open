/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { readBackfillContext, isBackfillCancelled, useBackfillConfirm } from '@/composables/backfillConfirm'

function periodClosed(detail: Record<string, unknown>) {
  return { response: { data: { detail: { code: 'PERIOD_CLOSED', message: '已锁账', context: detail } } } }
}

describe('backfill confirm', () => {
  it('只在可补记时接管错误', () => {
    expect(readBackfillContext(periodClosed({ period: '2026-07', canBackfill: true, suggestedPostingPeriod: '2026-08' })))
      .toEqual({
        canBackfill: true,
        suggestedPostingPeriod: '2026-08',
        crossQuarter: false,
        crossYear: false,
        period: '2026-07',
      })
    expect(readBackfillContext(periodClosed({ period: '2025-12', canBackfill: false }))).toBeNull()
    expect(readBackfillContext(new Error('other'))).toBeNull()
    expect(readBackfillContext({ response: { data: { detail: { code: 'AMOUNT_MISMATCH' } } } })).toBeNull()
  })

  it('确认后带 allowBackfill 重试，取消则抛出可识别哨兵', async () => {
    const { state, withBackfill, submit, cancel } = useBackfillConfirm()
    const calls: Array<[boolean, string | undefined]> = []

    // 确认路径
    const action = async (allow: boolean, reason?: string) => {
      calls.push([allow, reason])
      if (!allow) throw periodClosed({ period: '2026-07', canBackfill: true, suggestedPostingPeriod: '2026-08' })
      return 'ok'
    }
    const promise = withBackfill(action)
    await Promise.resolve()
    state.reason = '8 月单据 9 月才拿到'
    submit()
    await expect(promise).resolves.toBe('ok')
    expect(calls).toEqual([[false, undefined], [true, '8 月单据 9 月才拿到']])

    // 取消路径
    const cancelled = withBackfill(action)
    await Promise.resolve()
    cancel()
    await expect(cancelled).rejects.toSatisfy(isBackfillCancelled)
  })
})
