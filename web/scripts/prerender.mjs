// 构建期后本地预渲染（SEO P1）：用本机 Chrome/Edge 把公开路由渲染成静态 HTML，
// 产物写入 web/prerender/，由 deploy/nginx/Dockerfile 在 stage-2 COPY 进镜像。
// 选「本地预渲染 + 产物随部署快照同步」而非服务器构建期预渲染的原因：
// 服务器只有 3.9G 内存且在国内，跑 Chromium 既挤又拉不动官方二进制；
// 新鲜度由 scripts/check-prerender.mjs 在镜像构建时强制把关（资产指纹对不上就拒绝出镜像）。
//
// 用法：先 npm run build，再 npm run prerender。页面清单见 ./public-pages.mjs。
// 浏览器查找顺序：环境变量 PUPPETEER_EXECUTABLE_PATH → puppeteer 缓存 headless-shell
// → Edge → Chrome。headless-shell 优先于系统浏览器：D-019（Edge 自动升级后新进程
// 委托给运行中的现有会话即退出，Puppeteer 拿不到调试端口），headless-shell 是
// 独立二进制，不受系统浏览器升级/会话状态影响。
import { mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { homedir } from 'node:os'
import { extname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { existsSync } from 'node:fs'
import puppeteer from 'puppeteer-core'
import { SPA_PAGES } from './public-pages.mjs'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const DIST = join(ROOT, 'dist')
const OUT = join(ROOT, 'prerender')
const PORT = 4173

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript',
  '.mjs': 'text/javascript',
  '.css': 'text/css',
  '.json': 'application/json',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
}

if (!existsSync(join(DIST, 'index.html'))) {
  console.error('[prerender] 未找到 web/dist，请先 npm run build')
  process.exit(1)
}

// puppeteer 缓存目录里的 chrome-headless-shell（npm i puppeteer 时自动下载），
// 布局：<cache>/chrome-headless-shell/<平台>-<版本>/chrome-headless-shell-<平台>/chrome-headless-shell[.exe]
function findCachedHeadlessShell() {
  const cacheRoot = process.env.PUPPETEER_CACHE_DIR || join(homedir(), '.cache', 'puppeteer')
  const shellRoot = join(cacheRoot, 'chrome-headless-shell')
  if (!existsSync(shellRoot)) return null
  const prefixes = process.platform === 'darwin'
    ? ['mac_arm', 'mac_x64']
    : [process.platform === 'win32' ? 'win64' : 'linux64']
  const binName = process.platform === 'win32' ? 'chrome-headless-shell.exe' : 'chrome-headless-shell'
  for (const prefix of prefixes) {
    const versions = readdirSync(shellRoot)
      .filter((d) => d.startsWith(`${prefix}-`))
      .map((d) => ({ d, parts: d.slice(prefix.length + 1).split('.').map(Number) }))
      .filter(({ parts }) => parts.length === 4 && parts.every((n) => Number.isFinite(n)))
      .sort((a, b) => a.parts[0] - b.parts[0] || a.parts[1] - b.parts[1]
        || a.parts[2] - b.parts[2] || a.parts[3] - b.parts[3])
      .reverse()
    for (const { d } of versions) {
      const candidate = join(shellRoot, d, `chrome-headless-shell-${prefix}`, binName)
      if (existsSync(candidate)) return candidate
    }
  }
  return null
}

function findBrowser() {
  const candidates = [
    process.env.PUPPETEER_EXECUTABLE_PATH,
    findCachedHeadlessShell(),
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
  ].filter(Boolean)
  const found = candidates.find((p) => existsSync(p))
  if (!found) {
    console.error('[prerender] 未找到 Chrome/Edge/headless-shell，可设 PUPPETEER_EXECUTABLE_PATH 指定浏览器路径')
    process.exit(1)
  }
  console.log(`[prerender] 浏览器: ${found}`)
  return found
}

// 静态服务 dist：ESM 脚本对 MIME 敏感，必须给对 text/javascript；
// 未命中的路径回退 index.html——与生产 nginx 对已知路由的行为一致
const server = createServer((req, res) => {
  const url = new URL(req.url ?? '/', 'http://localhost')
  let file = join(DIST, decodeURIComponent(url.pathname))
  if (file.endsWith('/') || !extname(file)) file = join(file, 'index.html')
  let body
  try {
    body = readFileSync(file)
  } catch {
    try {
      body = readFileSync(join(DIST, 'index.html'))
    } catch {
      res.writeHead(500)
      res.end('dist missing')
      return
    }
  }
  res.writeHead(200, { 'Content-Type': MIME[extname(file)] ?? 'text/html; charset=utf-8' })
  res.end(body)
})

await new Promise((resolve) => server.listen(PORT, '127.0.0.1', resolve))
const browser = await puppeteer.launch({ executablePath: findBrowser(), headless: true })

try {
  mkdirSync(OUT, { recursive: true })
  for (const page of SPA_PAGES) {
    const target = `http://127.0.0.1:${PORT}${page.path}`
    const p = await browser.newPage()
    await p.goto(target, { waitUntil: 'networkidle0', timeout: 60_000 })
    // 等 Vue 挂载出实质内容（约定：#app 可见文本 > 100 字符），最多再等 15s
    await p
      .waitForFunction(() => (document.querySelector('#app')?.textContent ?? '').trim().length > 100, {
        timeout: 15_000,
      })
      .catch(() => {
        console.warn(`[prerender] 警告: ${page.path} 挂载内容未达标，仍按当前 DOM 序列化`)
      })
    const html = `<!doctype html>\n${await p.evaluate(() => document.documentElement.outerHTML)}\n`
    const rel = page.path === '/' ? 'index.html' : `${page.path.replace(/^\//, '').replace(/\/$/, '')}/index.html`
    const out = join(OUT, rel)
    mkdirSync(join(out, '..'), { recursive: true })
    writeFileSync(out, html)
    console.log(`[prerender] ${page.path} → ${rel}（${(html.length / 1024).toFixed(0)} KB）`)
    await p.close()
  }
} finally {
  await browser.close()
  server.close()
}
