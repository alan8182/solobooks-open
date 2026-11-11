// 公开页面清单 —— sitemap 生成、百度推送、预渲染、/learn 文章构建共用的单一数据源。
// 新增公开内容 = 在 learn/posts/ 放一个带 front-matter 的 .md（自动进清单），
// 或在 SPA_PAGES 追加路由；同步更新对应 lastmod。
import { existsSync, readdirSync, readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { parseFrontMatter } from './frontmatter.mjs'

export const SITE_URL = 'https://solobooks.cn'

const HERE = dirname(fileURLToPath(import.meta.url))
const POSTS_DIR = join(HERE, '..', 'learn', 'posts')

// 需要浏览器预渲染的 SPA 路由。
// /learn 系列不在此列：它们由 build-learn.mjs 直接产出静态 HTML，不走浏览器渲染。
export const SPA_PAGES = [
  { path: '/', changefreq: 'weekly', priority: '1.0', lastmod: '2026-10-30' },
  { path: '/landing', changefreq: 'monthly', priority: '0.6', lastmod: '2026-10-30' },
  { path: '/ai-connect', changefreq: 'monthly', priority: '0.8', lastmod: '2026-10-30' },
  { path: '/agreement', changefreq: 'yearly', priority: '0.3', lastmod: '2026-10-30' },
  { path: '/privacy', changefreq: 'yearly', priority: '0.3', lastmod: '2026-10-30' },
]

// /learn 文章清单：slug 即文件名，lastmod 取 front-matter 的 date
export function learnPosts() {
  if (!existsSync(POSTS_DIR)) return []
  return readdirSync(POSTS_DIR)
    .filter((f) => f.endsWith('.md'))
    .map((f) => {
      const { meta } = parseFrontMatter(readFileSync(join(POSTS_DIR, f), 'utf8'))
      return { path: `/learn/${f.replace(/\.md$/, '')}`, title: meta.title ?? '', date: meta.date ?? '' }
    })
}

// 全部公开页（sitemap / 百度推送口径）
export const PAGES = [
  ...SPA_PAGES,
  { path: '/learn', changefreq: 'weekly', priority: '0.6', lastmod: '2026-10-30' },
  ...learnPosts().map((p) => ({ path: p.path, changefreq: 'monthly', priority: '0.6', lastmod: p.date })),
]
