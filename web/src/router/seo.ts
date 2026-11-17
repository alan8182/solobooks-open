/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import type { Router } from 'vue-router'
import { FREE_FAQ, HOME_FAQ, faqPageJsonLd } from '@/content/faq'

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
  /** 路由级 JSON-LD（FAQPage / SoftwareApplication 等），预渲染时随 DOM 一起输出 */
  jsonLd?: Record<string, unknown>
}

const FREE_SOFTWARE_JSON_LD: Record<string, unknown> = {
  '@context': 'https://schema.org',
  '@graph': [
    faqPageJsonLd(FREE_FAQ),
    {
      '@type': 'SoftwareApplication',
      name: '梭罗记账 SoloBooks',
      url: `${SITE_URL}/free`,
      applicationCategory: 'BusinessApplication',
      operatingSystem: 'Web',
      inLanguage: 'zh-CN',
      description:
        '面向小微企业、一人公司与个体户的复式记账与经营分析软件，提供 0 元云端免费试用版与可自行部署的免费自托管版。',
      offers: [
        {
          '@type': 'Offer',
          name: '云端免费试用版',
          price: '0',
          priceCurrency: 'CNY',
          description: '每月 10 万 AI Token，最多 3 个企业账套',
        },
        {
          '@type': 'Offer',
          name: '自托管版',
          price: '0',
          priceCurrency: 'CNY',
          description: '源码可用，本主体内部记账永久免费，需自行部署',
        },
      ],
    },
  ],
}

// 公开页面的 TDK，按路由 name 键控（单一数据源，刻意不往 RouteMeta 上塞 seo 字段，
// 避免与 guards.ts 的 GuardRouteLike 弱类型检查冲突）。未列出的路由（登录后的
// 工作台页面）不参与搜索收录，回落默认标题。
// 注意 `/` 匹配到的路由名是 Home（RootSwitch 匿名时直接渲染 Landing 组件），
// 所以 Home 的 canonical 指向根路径，与 Landing 保持一致。
const PAGE_SEO: Record<string, SeoMeta> = {
  // 未登录的 / 与 /landing 同屏渲染官网；规范地址统一指向根路径，避免双入口分权
  Home: { canonical: '/', jsonLd: faqPageJsonLd(HOME_FAQ) },
  Landing: { canonical: '/', jsonLd: faqPageJsonLd(HOME_FAQ) },
  // /landing-old 是保留可访问的旧版首页，内容与规范首页重复，禁止收录
  LandingOld: { robots: 'noindex' },
  FreeSoftware: {
    title: '免费财务软件怎么选？云端免费试用与自托管永久免费',
    description:
      '小微企业、一人公司找免费财务软件，先分清两种免费：0 元云端免费试用版（每月 10 万 Token、3 个账套），以及源码可用、可自行部署的自托管版（永久免费、数据留在本地）。含免费边界、成本对比与常见问题。',
    canonical: '/free',
    jsonLd: FREE_SOFTWARE_JSON_LD,
  },
  AuditScan: {
    title: '免费财务健康体检 · 3 分钟自查公私混同与账本风险',
    description:
      '免登录、免存即焚的财务健康体检：6 道经营常识题或一张科目余额表，3 分钟揪出公私混同、股东借款个税、找票顶账等隐形风险，并给出可执行的整改建议。',
    canonical: '/audit-scan',
  },
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

const JSON_LD_ID = 'seo-jsonld'

function setJsonLd(data: Record<string, unknown> | undefined) {
  let el = document.getElementById(JSON_LD_ID) as HTMLScriptElement | null
  if (!data) {
    el?.remove()
    return
  }
  if (!el) {
    el = document.createElement('script')
    el.type = 'application/ld+json'
    el.id = JSON_LD_ID
    document.head.appendChild(el)
  }
  el.textContent = JSON.stringify(data)
}

/** 每次导航后同步 TDK / canonical / robots / OG / JSON-LD；公开页取 PAGE_SEO，其余回落默认 */
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
  setJsonLd(seo?.jsonLd)
}

export function installSeo(router: Router) {
  router.afterEach((to) => applySeo(to))
}
