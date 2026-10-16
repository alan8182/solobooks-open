/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

export interface GuardRouteLike {
  path: string
  meta: { requiresAuth?: boolean }
}

/**
 * 认证重定向规则（纯函数，便于单测）：
 * - 未登录访问根路径 → 官网首页
 * - 未登录访问受保护路由 → 登录页
 * - 已登录访问登录页 → 回工作台
 * 返回 null 表示不需要重定向。
 */
export function resolveAuthRedirect(to: GuardRouteLike, token: string | null): string | null {
  if (!token && to.path === '/') return '/landing'
  if (to.meta.requiresAuth && !token) return '/login'
  if (to.path === '/login' && token) return '/'
  return null
}
