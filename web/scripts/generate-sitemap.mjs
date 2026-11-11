// 构建期生成 public/sitemap.xml（package.json 的 prebuild 自动执行）。
// 页面清单维护在 ./public-pages.mjs（与百度推送、预渲染共用）；新增页面后同步更新 lastmod。
// 仅在内容变化时写盘，避免每次构建都弄脏工作区。
import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { PAGES, SITE_URL } from './public-pages.mjs'

const xml =
  '<?xml version="1.0" encoding="UTF-8"?>\n' +
  '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' +
  PAGES
    .map(
      (p) =>
        `  <url>\n    <loc>${SITE_URL}${p.path}</loc>\n    <lastmod>${p.lastmod}</lastmod>\n` +
        `    <changefreq>${p.changefreq}</changefreq>\n    <priority>${p.priority}</priority>\n  </url>`,
    )
    .join('\n') +
  '\n</urlset>\n'

const target = fileURLToPath(new URL('../public/sitemap.xml', import.meta.url))
if (existsSync(target) && readFileSync(target, 'utf8') === xml) {
  console.log('[generate-sitemap] sitemap.xml 无变化')
} else {
  writeFileSync(target, xml)
  console.log('[generate-sitemap] sitemap.xml 已生成:', target)
}
