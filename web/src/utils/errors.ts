/**
 * Copyright (c) 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// 后端 AccountingError 统一从 main.py 异常处理器输出为
// { detail: { code, message, err_sig, context? } }，此处做类型安全的提取。
//
// 本文件是**纯解析层**：只回答「后端说了什么」，不弹任何东西、不碰组件。
// 弹什么（toast / 救援弹窗 / 交给补记确认）由 `utils/errorPresenter.ts` 决定。
import { RESCUE_RULES, type RescueRule } from '@/utils/rescueRules'

export interface AccountingErrorDetail {
  code: string
  message: string
  context?: Record<string, unknown>
}

export function accountingErrorDetail(err: unknown): AccountingErrorDetail | null {
  const detail = (err as { response?: { data?: { detail?: unknown } } } | null)?.response?.data?.detail
  if (!detail || typeof detail !== 'object' || !('code' in detail)) return null
  const d = detail as Record<string, unknown>
  return {
    code: String(d.code ?? ''),
    message: String(d.message ?? ''),
    context: (d.context && typeof d.context === 'object' ? d.context : undefined) as
      | Record<string, unknown>
      | undefined,
  }
}

/**
 * 面向用户的错误文案：后端 message 优先，其次兜底。
 *
 * 全站曾有 151 处 toast 直接丢弃后端 message，用户只看到「保存失败」，
 * 而后端其实说了「凭证借贷不平衡」——这层的第一职责就是把话还原出来。
 */
export function accountingErrorText(err: unknown, fallback = '操作失败'): string {
  const detail = accountingErrorDetail(err)
  const message = detail?.message?.trim()
  if (message) return message
  // 非结构化错误（网络中断、axios 包装层）也要给出可辨认的话，而不是空的
  const raw = err as { message?: unknown; response?: { status?: number } } | null
  if (raw?.response?.status) {
    const status = raw.response.status
    if (status === 401) return '登录已过期，请重新登录'
    if (status === 403) return '没有权限执行这个操作'
    if (status >= 500) return '服务暂时不可用，请稍后重试'
  }
  if (typeof raw?.message === 'string' && raw.message.trim()) return raw.message
  return fallback
}

export interface RescueMatch {
  rule: RescueRule
  ctx: Record<string, unknown>
}

/** 命中救援规则就返回规则与它的 context；没命中返回 null（调用方退回普通 toast）。 */
export function rescueRuleFor(err: unknown): RescueMatch | null {
  const detail = accountingErrorDetail(err)
  if (!detail) return null
  const rule = RESCUE_RULES[detail.code]
  if (!rule) return null
  return { rule, ctx: detail.context || {} }
}
