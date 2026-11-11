// D-020 防再犯：新增一级前端路由必须同步追加 deploy/nginx/default.conf 的 SPA 白名单
// 正则，否则该路由直连访问返回真 404（分发外链/二维码场景全断，对爬虫是死链）。
// 本脚本静态解析 web/src/router/index.ts 的一级路径段，与 nginx 白名单比对；
// 由 deploy/preflight.sh 作为固定门禁步运行（不可跳过）。可传参指定 conf 路径做测试：
//   node scripts/check-nginx-routes.mjs [conf路径]
//
// 解析规则（对当前 router 结构够用，不追求通用 TS 解析）：
//   - `path: '/x'` 绝对路由 → 一级段 = x（redirect 回调里的 path 同样命中，无害：
//     redirect 目标必然是真实路由）；`path: '/'` 根路由不产生段
//   - `path: 'x'` 相对路由 → 找「最近的、花括号深度更浅的上一条 path」当父级：
//     父级是 '/' 或 ''（空路径布局父级）才贡献一级段；挂在 /console 这类绝对父级下
//     的一级段已在父级覆盖（/console/usage 的 first segment 仍是 console），跳过
//   - 以 ':' 开头的是动态段/通配（如 :pathMatch(.*)*），不产生一级段
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { join } from 'node:path'

const WEB_ROOT = fileURLToPath(new URL('..', import.meta.url))
const REPO_ROOT = fileURLToPath(new URL('../..', import.meta.url))
const routerSrc = readFileSync(join(WEB_ROOT, 'src/router/index.ts'), 'utf8')
const nginxPath = process.argv[2] || join(REPO_ROOT, 'deploy/nginx/default.conf')
const nginxSrc = readFileSync(nginxPath, 'utf8')

// 供运维显式豁免的一级段（当前为空；注意 /mcp 是后端代理不走 SPA，不在 router 里）
const EXCEPTIONS = new Set([])

// ---- 逐字符扫描，记录每条 path: '...' 的花括号深度 ----
const entries = [] // { value, depth }
let depth = 0
const src = routerSrc
for (let i = 0; i < src.length; i++) {
  const ch = src[i]
  if (ch === '{') depth++
  else if (ch === '}') depth--
  else if (ch === "'" && i >= 6 && src.startsWith("path: '", i - 6)) {
    const start = i + 1
    const end = src.indexOf("'", start)
    if (end > start) {
      entries.push({ value: src.slice(start, end), depth })
      i = end
    }
  }
}

// ---- 归并出一级段 ----
const firstSegments = new Set()
for (let i = 0; i < entries.length; i++) {
  const { value, depth } = entries[i]
  if (value.startsWith(':')) continue
  if (value.startsWith('/')) {
    const seg = value.split('/')[1]
    if (seg) firstSegments.add(seg)
    continue
  }
  // 相对路由：找最近的深度更浅的父级 path
  let parent = null
  for (let j = i - 1; j >= 0; j--) {
    if (entries[j].depth < depth) { parent = entries[j]; break }
  }
  const parentPath = parent ? parent.value : ''
  if (parentPath === '/' || parentPath === '') {
    const seg = value.split('/')[0]
    if (seg) firstSegments.add(seg)
  }
}

// ---- 提取 nginx SPA 白名单 ----
const m = nginxSrc.match(/location ~ \^\/\(([^)]*)\)\(\/\|\$\)/)
if (!m) {
  console.error('[check-nginx-routes] 未在 nginx conf 找到 SPA 白名单正则（location ~ ^/(...)(/|$)）')
  process.exit(1)
}
const whitelist = new Set(m[1].split('|'))

// ---- 比对 ----
const missing = [...firstSegments].filter((s) => !whitelist.has(s) && !EXCEPTIONS.has(s)).sort()
const extra = [...whitelist].filter((s) => !firstSegments.has(s)).sort()

if (missing.length > 0) {
  console.error('[check-nginx-routes] 以下一级路由不在 nginx 白名单里，直连访问会返回 404：')
  console.error('  ' + missing.join(', '))
  console.error('  → 同步追加到 deploy/nginx/default.conf 的 SPA location 正则（D-020）')
  process.exit(1)
}
console.log(`[check-nginx-routes] router 一级段 ${firstSegments.size} 个全部在 nginx 白名单内`)
if (extra.length > 0) {
  console.log(`[check-nginx-routes] 白名单中 router 未使用（历史/外部路由，仅提示）：${extra.join(', ')}`)
}
