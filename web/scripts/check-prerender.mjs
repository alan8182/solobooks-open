// 预渲染产物新鲜度门禁：在 deploy/nginx/Dockerfile 的构建阶段运行。
// 产物缺失 → 仅警告（站点仍可部署，只是退回无预渲染状态）；
// 产物引用的 /assets/ 资源在当前 dist 里不存在 → 说明预渲染过期（构建后忘记重跑），
// 引擎执行旧 JS 会 404 白屏，直接让镜像构建失败拦住。
import { existsSync, readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const ROOT = fileURLToPath(new URL('..', import.meta.url))
const PRERENDER = join(ROOT, 'prerender')
const DIST_ASSETS = join(ROOT, 'dist', 'assets')

function collectHtml(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((e) =>
    e.isDirectory() ? collectHtml(join(dir, e.name)) : e.name.endsWith('.html') ? [join(dir, e.name)] : [],
  )
}

if (!existsSync(join(PRERENDER, 'index.html'))) {
  console.warn('[check-prerender] 预渲染产物缺失，本次镜像将不含预渲染 HTML（建议本地跑 npm run prerender 后再部署）')
  process.exit(0)
}

const distFiles = new Set(existsSync(DIST_ASSETS) ? readdirSync(DIST_ASSETS) : [])
const missing = []
for (const file of collectHtml(PRERENDER)) {
  const html = readFileSync(file, 'utf8')
  // 只校验标签属性里的真实资源引用（src/href），HTML 内嵌 JS 字符串里的
  // "/assets/xxx" 不是浏览器会去请求的文件，不能参与比对
  for (const ref of html.matchAll(/(?:src|href)="\s*\/assets\/([^"]+)"/g)) {
    if (!distFiles.has(ref[1])) missing.push(`${file} → /assets/${ref[1]}`)
  }
}

if (missing.length > 0) {
  console.error('[check-prerender] 预渲染产物已过期（引用的构建资产在当前 dist 不存在）：')
  for (const m of missing.slice(0, 10)) console.error('  ' + m)
  console.error('请先在本地执行 npm run build && npm run prerender，再重新部署。')
  process.exit(1)
}
console.log('[check-prerender] 预渲染产物与当前构建一致')
