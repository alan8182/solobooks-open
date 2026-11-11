// /learn 内容板块构建器（SEO P2）：web/learn/posts/*.md → 独立静态 HTML。
// 文章不进 Vue 包、不依赖 JS——与预渲染产物同通道（web/prerender/learn/）进镜像，
// 由 nginx location / 直接命中。新增文章 = 放一个带 front-matter 的 .md + 重跑 npm run prerender。
// 用法：npm run build && npm run prerender（prerender 脚本已串联本文件）。
import { mkdirSync, readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { marked } from 'marked'
import { parseFrontMatter } from './frontmatter.mjs'
import { SITE_URL } from './public-pages.mjs'

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..')
const POSTS_DIR = join(ROOT, 'learn', 'posts')
const OUT_DIR = join(ROOT, 'prerender', 'learn')

const ICP_LICENSE = '浙ICP备2025195764号-7'

const esc = (s) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;')

const pageShell = ({ title, description, canonical, ogType, publishedTime, jsonLd, content }) => `<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>${esc(title)}</title>
<meta name="description" content="${esc(description)}" />
<link rel="canonical" href="${SITE_URL}${canonical}" />
<link rel="icon" type="image/png" sizes="48x48" href="/favicon-48.png" />
<meta property="og:type" content="${ogType}" />
<meta property="og:site_name" content="梭罗记账 SoloBooks" />
<meta property="og:title" content="${esc(title)}" />
<meta property="og:description" content="${esc(description)}" />
<meta property="og:url" content="${SITE_URL}${canonical}" />
<meta property="og:image" content="${SITE_URL}/solo-logo.png" />
<meta property="og:locale" content="zh_CN" />
${publishedTime ? `<meta property="article:published_time" content="${publishedTime}" />` : ''}
<script type="application/ld+json">${jsonLd}</script>
<style>
:root { --brand: #18a058; --ink: #333639; --muted: #7c8085; --line: #e8eaed; --bg: #f7f8fa; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink);
  font-family: system-ui, -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif; line-height: 1.8; }
header { background: #fff; border-bottom: 1px solid var(--line); }
.header-inner { max-width: 760px; margin: 0 auto; padding: 14px 20px; display: flex;
  justify-content: space-between; align-items: center; }
.brand { display: flex; align-items: center; gap: 10px; text-decoration: none; color: var(--ink); font-weight: 600; }
.brand img { width: 26px; height: 26px; }
.header-inner nav a { margin-left: 18px; color: var(--muted); text-decoration: none; font-size: 14px; }
.header-inner nav a:hover { color: var(--brand); }
main { max-width: 760px; margin: 0 auto; padding: 36px 20px 48px; background: #fff;
  border-left: 1px solid var(--line); border-right: 1px solid var(--line); min-height: 60vh; }
.page-date { color: var(--muted); font-size: 13px; margin: 0 0 20px; }
h1 { font-size: 26px; line-height: 1.5; margin: 0 0 8px; }
h2 { font-size: 20px; margin: 34px 0 12px; padding-left: 10px; border-left: 3px solid var(--brand); }
h3 { font-size: 17px; margin: 26px 0 10px; }
p, li { font-size: 15.5px; }
a { color: var(--brand); }
blockquote { margin: 16px 0; padding: 10px 16px; background: var(--bg); border-left: 3px solid var(--line);
  color: var(--muted); font-size: 14px; }
table { border-collapse: collapse; width: 100%; margin: 16px 0; font-size: 14.5px; }
th, td { border: 1px solid var(--line); padding: 8px 12px; text-align: left; }
th { background: var(--bg); }
code { background: var(--bg); padding: 2px 6px; border-radius: 4px; font-size: 14px; }
.cta { margin: 40px 0 8px; padding: 20px 22px; border: 1px solid var(--line); border-radius: 12px; background: var(--bg); }
.cta b { display: block; margin-bottom: 6px; }
.cta p { margin: 0 0 14px; font-size: 14.5px; color: var(--muted); }
.cta a { display: inline-block; padding: 9px 24px; border-radius: 8px; background: var(--brand);
  color: #fff; text-decoration: none; font-size: 14.5px; }
footer { max-width: 760px; margin: 0 auto; padding: 22px 20px 40px; color: var(--muted); font-size: 13px; }
footer a { color: var(--muted); margin-right: 14px; }
.post-list { list-style: none; padding: 0; margin: 24px 0 0; }
.post-list li { padding: 16px 0; border-bottom: 1px solid var(--line); }
.post-list a { font-size: 16.5px; font-weight: 600; text-decoration: none; color: var(--ink); }
.post-list a:hover { color: var(--brand); }
.post-list time { display: block; color: var(--muted); font-size: 13px; margin-top: 4px; }
.post-list p { margin: 6px 0 0; color: var(--muted); font-size: 14.5px; }
</style>
</head>
<body>
<header><div class="header-inner">
<a class="brand" href="/"><img src="/solo-logo.png" alt="梭罗记账" />梭罗记账 SoloBooks</a>
<nav><a href="/learn">学习中心</a><a href="/ai-connect">AI 连接</a><a href="/">官网首页</a></nav>
</div></header>
<main>
${content}
</main>
<footer>
<a href="/">官网首页</a><a href="/learn">学习中心</a><a href="/agreement">服务协议</a><a href="/privacy">隐私政策</a>
<div style="margin-top:10px">© ${new Date().getFullYear()} 梭罗记账 SoloBooks · <a href="https://beian.miit.gov.cn" rel="noreferrer">${ICP_LICENSE}</a></div>
</footer>
</body>
</html>
`

const publisher = {
  '@type': 'Organization',
  name: '芥子星辰（杭州）智能科技有限公司',
  url: `${SITE_URL}/`,
  logo: { '@type': 'ImageObject', url: `${SITE_URL}/solo-logo.png` },
}

mkdirSync(OUT_DIR, { recursive: true })

const files = readdirSync(POSTS_DIR).filter((f) => f.endsWith('.md'))
const articles = files.map((f) => {
  const slug = f.replace(/\.md$/, '')
  const { meta, body } = parseFrontMatter(readFileSync(join(POSTS_DIR, f), 'utf8'))
  if (!meta.title || !meta.description || !meta.date) {
    console.error(`[build-learn] ${f} 缺少 title / description / date front-matter`)
    process.exit(1)
  }
  return { slug, meta, body, html: marked.parse(body, { async: false }) }
})
articles.sort((a, b) => (a.meta.date < b.meta.date ? 1 : -1))

for (const a of articles) {
  const path = `/learn/${a.slug}`
  const jsonLd = JSON.stringify({
    '@context': 'https://schema.org',
    '@type': 'Article',
    headline: a.meta.title,
    description: a.meta.description,
    datePublished: a.meta.date,
    dateModified: a.meta.date,
    inLanguage: 'zh-CN',
    author: publisher,
    publisher,
    mainEntityOfPage: `${SITE_URL}${path}`,
  })
  const content = `
<h1>${esc(a.meta.title)}</h1>
<p class="page-date"><time datetime="${a.meta.date}">${a.meta.date}</time> · 梭罗记账学习中心</p>
${a.html}
<aside class="cta">
<b>把账记简单，把经营看清楚</b>
<p>梭罗记账 SoloBooks——面向新一代小微企业、创业团队与一人公司的驻场 AI 财务官。点一点把账做清爽，问一问把公司养值钱；规范记账、公私合规，关键动作由你确认。</p>
<a href="/">了解梭罗记账</a>
</aside>`
  const out = join(OUT_DIR, a.slug, 'index.html')
  mkdirSync(dirname(out), { recursive: true })
  writeFileSync(out, pageShell({
    title: `${a.meta.title} · 梭罗记账`,
    description: a.meta.description,
    canonical: path,
    ogType: 'article',
    publishedTime: a.meta.date,
    jsonLd,
    content,
  }))
  console.log(`[build-learn] ${path}（${(a.html.length / 1024).toFixed(0)} KB 内容）`)
}

// /learn 列表页：文章索引 + 导语，避免薄内容
const listItems = articles
  .map(
    (a) => `<li><a href="/learn/${a.slug}">${esc(a.meta.title)}</a><time datetime="${a.meta.date}">${a.meta.date}</time><p>${esc(a.meta.description)}</p></li>`,
  )
  .join('\n')
const listContent = `
<h1>梭罗记账学习中心</h1>
<p>写给一人公司、个体户与小微经营者的记账与报税实务：把经营动作翻译成会计语言，把合规拆成可以跟着做的步骤。</p>
<ul class="post-list">
${listItems}
</ul>
<aside class="cta">
<b>把账记简单，把经营看清楚</b>
<p>梭罗记账 SoloBooks——面向新一代小微企业、创业团队与一人公司的驻场 AI 财务官。点一点把账做清爽，问一问把公司养值钱；规范记账、公私合规。</p>
<a href="/">了解梭罗记账</a>
</aside>`
const listJsonLd = JSON.stringify({
  '@context': 'https://schema.org',
  '@type': 'CollectionPage',
  name: '梭罗记账学习中心',
  inLanguage: 'zh-CN',
  publisher,
})
writeFileSync(
  join(OUT_DIR, 'index.html'),
  pageShell({
    title: '学习中心 · 个体户与小微记账实务 —— 梭罗记账',
    description:
      '写给一人公司、个体户与小微经营者的记账报税实务：建账要求、免税额度、微信支付宝流水记账、月度结账与记账软件选购。',
    canonical: '/learn',
    ogType: 'website',
    publishedTime: '',
    jsonLd: listJsonLd,
    content: listContent,
  }),
)
console.log('[build-learn] /learn 列表页完成')
