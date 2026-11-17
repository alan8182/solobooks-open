/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

export interface GuardRouteLike {
  path: string
  fullPath?: string
  query?: Record<string, unknown>
  meta: { requiresAuth?: boolean }
}

/**
 * 校验登录回跳目标，仅允许站内相对路径（拦截 //evil.com、外链与 /login 自循环）。
 */
export function safeRedirectTarget(raw: unknown): string | null {
  if (typeof raw !== 'string') return null
  const target = raw.trim()
  if (!target.startsWith('/') || target.startsWith('//') || target.startsWith('/\\')) {
    return null
  }
  if (/[\r\n]/.test(target)) return null
  if (target === '/login' || target.startsWith('/login?')) return null
  return target
}

/**
 * 认证重定向规则（纯函数，便于单测）：
 * - 未登录访问根路径 → 不重定向，由 RootSwitch 在 / 原地呈现官网落地页
 * - 未登录访问受保护路由 → 登录页（携带站内 fullPath 便于登录后回跳）
 * - 已登录访问登录页 → 优先回跳安全的 redirect 参数，否则回工作台
 * 返回 null 表示不需要重定向。
 */
export function resolveAuthRedirect(to: GuardRouteLike, token: string | null): string | null {
  // 必须先于 requiresAuth 规则：/ 的匹配链上 AppLayout 带 requiresAuth，
  // 但匿名根路径要交给 RootSwitch 渲染官网而不是跳登录
  if (!token && to.path === '/') return null
  if (to.meta.requiresAuth && !token) {
    const target = safeRedirectTarget(to.fullPath)
    return target && target !== '/' ? `/login?redirect=${encodeURIComponent(target)}` : '/login'
  }
  if (to.path === '/login' && token) {
    return safeRedirectTarget(to.query?.redirect) || '/'
  }
  return null
}
