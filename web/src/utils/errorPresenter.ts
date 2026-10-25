/**
 * Copyright (c) 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 错误呈现层：决定「这个错怎么让用户看到」。
 *
 * 这里是全站唯一的错误出口，调用点把 `message.error('保存失败')` 换成
 * `reportErrorToast(message, err, '保存失败')` 即可。三种去向**互斥**，
 * 这是防「弹了救援框又弹一次 toast」的根本手段：
 *
 * 1. `PERIOD_CLOSED` 且允许补记 → 让位 `backfillConfirm`（它有可等待的重试语义）
 * 2. 命中救援规则且未被克制 → 入队弹 RescueDialog
 * 3. 其余 → 普通 toast，但用**后端的 message**，不再是「操作失败」这种空话
 *
 * 与 `utils/errors.ts` 的分工：那边只解析，这边只呈现。
 */
import type { MessageApi } from 'naive-ui'
import { readBackfillContext } from '@/composables/backfillConfirm'
import { offerRescue } from '@/composables/useRescue'
import { accountingErrorText, rescueRuleFor } from '@/utils/errors'

/**
 * 唯一的错误出口。返回 true 表示已被上层（救援弹窗 / 补记确认）接管，
 * 调用点此时**不要再弹任何东西**。
 */
export function presentAccountingError(
  message: MessageApi,
  err: unknown,
  fallback = '操作失败',
): boolean {
  // 1. 补记确认优先：它会自己弹窗并重试，救援层插一脚会打断 allowBackfill 流程
  if (readBackfillContext(err)) return false
  // 2. 救援弹窗
  if (offerRescue(err)) return true
  // 3. 普通兜底
  message.error(accountingErrorText(err, fallback))
  return false
}

/** 便捷封装：不需要知道返回值的绝大多数调用点用它就够了。 */
export function reportErrorToast(message: MessageApi, err: unknown, fallback = '操作失败'): void {
  presentAccountingError(message, err, fallback)
}

/** 只把错误变成一句话，不弹任何东西（用于需要自己拼文案的地方）。 */
export function errorSummary(err: unknown, fallback = '操作失败'): string {
  const match = rescueRuleFor(err)
  if (match) {
    return typeof match.rule.reason === 'function'
      ? match.rule.reason(match.ctx)
      : match.rule.reason
  }
  return accountingErrorText(err, fallback)
}
