/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import type { Router } from 'vue-router'

// ── 路由级 SEO（TDK / canonical / robots / OG）──
// JS 注入的 meta 只对执行 JS 的引擎（Google/Bing/微信内置浏览器）即时生效；
// 百度要等构建期预渲染（P1）才能读到完整 HTML。PAGE_SEO 的映射同时是
// 预渲染器产出每页 TDK 的数据源，改这里即可全站生效。
export const SITE_URL = 'https://solobooks.cn'
export const SITE_NAME = '梭罗记账 SoloBooks'
export const DEFAULT_TITLE = '梭罗记账 SoloBooks｜小微企业的驻场 AI 财务官'
export const DEFAULT_DESCRIPTION =
  '梭罗记账（SoloBooks），面向新一代小微企业、创业团队与一人公司的驻场 AI 财务官：点一点把账做清爽，问一问把公司养值钱。规范记账、公私合规、数据自主，让经营账本清晰可查。'

export interface SeoMeta {
  /** 搜索结果与浏览器标签标题，自动追加「 · 梭罗记账 SoloBooks」品牌后缀 */
  title?: string
  description?: string
  /** 规范地址（站点内相对路径）；设置后输出 <link rel="canonical"> */
  canonical?: string
  /** 如 noindex、noindex, nofollow */
  robots?: string
}

// 公开页面的 TDK，按路由 name 键控（单一数据源，刻意不往 RouteMeta 上塞 seo 字段，
// 避免与 guards.ts 的 GuardRouteLike 弱类型检查冲突）。未列出的路由（登录后的
// 工作台页面）不参与搜索收录，回落默认标题。
// 注意 `/` 匹配到的路由名是 Home（RootSwitch 匿名时直接渲染 Landing 组件），
// 所以 Home 的 canonical 指向根路径，与 Landing 保持一致。
const PAGE_SEO: Record<string, SeoMeta> = {
  // 未登录的 / 与 /landing 同屏渲染官网；规范地址统一指向根路径，避免双入口分权
  Home: { canonical: '/' },
  Landing: { canonical: '/' },
  // /landing-v2 是与原版并存的对比验收变体，内容与规范首页重复，禁止收录
  LandingV2: { robots: 'noindex' },
  Login: {
    title: '登录',
    description: '登录梭罗记账，让 AI 帮你把账记简单、把经营看清楚。',
    canonical: '/login',
  },
  Agreement: { title: '服务协议', canonical: '/agreement' },
  Privacy: { title: '隐私政策', canonical: '/privacy' },
  McpSkillsGuide: {
    title: 'AI 连接中心 · 用 MCP 把记账接进 AI 助手',
    description:
      '梭罗记账 MCP 与 Agent Skills 实战指南：把记账、查账问数接进 Claude、DeepSeek 等 AI 助手，让驻场 AI 财务官融入日常经营。',
    canonical: '/ai-connect',
  },
  // 协审链接是按 token 的私有财务视图，禁止收录（robots.txt 已同时 Disallow /share/）
  AuditShareView: { robots: 'noindex, nofollow' },
}

function upsertMeta(attr: 'name' | 'property', key: string, content: string) {
  let el = document.head.querySelector<HTMLMetaElement>(`meta[${attr}="${key}"]`)
  if (!el) {
    el = document.createElement('meta')
    el.setAttribute(attr, key)
    document.head.appendChild(el)
  }
  el.setAttribute('content', content)
}

function setCanonical(path: string | undefined) {
  const el = document.head.querySelector<HTMLLinkElement>('link[rel="canonical"]')
  if (!path) {
    // 离开有 canonical 的公开页时移除，避免把官网规范地址带进工作台路由
    el?.remove()
    return
  }
  const href = `${SITE_URL}${path}`
  if (el) {
    el.href = href
  } else {
    const link = document.createElement('link')
    link.rel = 'canonical'
    link.href = href
    document.head.appendChild(link)
  }
}

function setRobots(content: string | undefined) {
  const el = document.head.querySelector<HTMLMetaElement>('meta[name="robots"]')
  if (!content) {
    el?.remove()
    return
  }
  if (el) {
    el.setAttribute('content', content)
  } else {
    upsertMeta('name', 'robots', content)
  }
}

/** 每次导航后同步 TDK / canonical / robots / OG；公开页取 PAGE_SEO，其余回落默认 */
export function applySeo(to: { name?: unknown; path: string }) {
  const seo = typeof to.name === 'string' ? PAGE_SEO[to.name] : undefined
  const title = seo?.title ? `${seo.title} · ${SITE_NAME}` : DEFAULT_TITLE
  const description = seo?.description ?? DEFAULT_DESCRIPTION

  document.title = title
  upsertMeta('name', 'description', description)
  upsertMeta('property', 'og:title', title)
  upsertMeta('property', 'og:description', description)
  upsertMeta('property', 'og:url', `${SITE_URL}${seo?.canonical ?? to.path}`)
  setCanonical(seo?.canonical)
  setRobots(seo?.robots)
}

export function installSeo(router: Router) {
  router.afterEach((to) => applySeo(to))
}
