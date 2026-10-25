#!/usr/bin/env node
/**
 * 色板守卫：防止 design token 再次被内联 hex 侵蚀。
 *
 * 三件事：
 *  1) 语义色（收/支/警/信息）在 token 之外出现近似色 → 直接报错。
 *     这类重复会导致「同一含义两个颜色」，且历史上出过兜底写反的事故。
 *  2) 统计非 token 的 hex 出现次数，超过冻结基线 → 报错（棘轮，只许降不许升）。
 *  3) 打印当前色板普查，让漂移可见而不是靠自觉。
 *
 * 用法：node scripts/check-palette.mjs [--report]
 *   --report 只打印不退出（本地排查用）
 */

import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, relative, extname } from 'node:path'

const ROOT = new URL('../src/', import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, '$1')
const REPORT_ONLY = process.argv.includes('--report')

/**
 * 主题桥（App.vue 的 themeOverrides）是唯一允许出现 token 镜像 hex 的地方：
 * naive-ui 的 JS 主题读取不到 CSS 变量，只能写死字面量，且 hover/pressed 是派生值。
 */
const THEME_BRIDGE = 'App.vue'

/**
 * 落地页有自己的 slate 灰阶（0f172a/334155/475569/64748b/94a3b8/cbd5e1/e2e8f0…），
 * 那是一套自洽的营销页层级，硬塞进产品的 3 档灰会压平层次。它不计入棘轮，
 * 只做语义色检查（红/绿/琥珀必须和产品同一套）。
 */
const OWN_SCALE_SURFACES = [
  'pages/landing/index.vue',
  // AI 连接中心与落地页同源：深色代码终端 + 浅色卡片，共用一套 slate 灰阶。
  // 它是对外营销/接入文档页，硬套产品内 3 档灰会把代码块压平；语义色仍纳入检查。
  'pages/mcp-guide/index.vue',
  'pages/mcp-guide/CodeBlock.vue',
]

/**
 * 应用内非 token hex 出现总数的上限 —— 棘轮，只许下调不许上升。
 * 2026-10-20 首次收敛后冻结在 53（此前 144 个不同色值 / 全站内联 hex 上千处）。
 * 剩下的多是浅色底纹、图表色、微信绿这类一次性取值；再往下压时把基线一起改小。
 */
// 棘轮基线：只降不升。
// 2026-10-20：53 → 49。守卫改为跳过块注释后，注释里解释历史色值不再计入，
// 实际用量降到 49，基线跟着收紧，不给以后的改动留空位。
const BASELINE_NON_TOKEN = 49

/** 语义色：这些含义只允许来自 token，出现近似色即失败 */
const SEMANTIC = {
  income: '#0f7354',
  expense: '#bd3d3d',
  warning: '#a05c10',
  info: '#1747b0',
}

/** 两种颜色的 RGB 欧氏距离 */
function distance(a, b) {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16))
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16))
  return Math.sqrt(pa.reduce((s, v, i) => s + (v - pb[i]) ** 2, 0))
}

function collectFiles(dir, out = []) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) collectFiles(p, out)
    else if (['.vue', '.css'].includes(extname(p))) out.push(p)
  }
  return out
}

// 1) 读 token
const variables = readFileSync(join(ROOT, 'assets/styles/variables.css'), 'utf8')
const tokenSet = new Set(
  [...variables.matchAll(/--solo-[a-z0-9-]+:\s*(#[0-9a-fA-F]{6})/g)].map((m) => m[1].toLowerCase()),
)

// 2) 扫源码
const HEX = /#[0-9a-fA-F]{6}\b/g
const counts = new Map() // 全部（含落地页），用于语义色检查
const appCounts = new Map() // 只算棘轮范围，用于报告
const filesByColor = new Map()
let nonToken = 0

/**
 * 把 CSS 块注释抹成等长空白，保留字符索引。
 * 注释里常要写「原来是 #ff7d00，对比度只有 2.41:1」这种说明——那是给人看的，
 * 不是生效的样式，不该计入棘轮；否则每次解释历史值都会让守卫变红，
 * 久而久之大家只会去改基线。
 */
function stripBlockComments(s) {
  return s.replace(/\/\*[\s\S]*?\*\//g, (m) => ' '.repeat(m.length))
}

for (const file of collectFiles(ROOT)) {
  const rel = relative(ROOT, file).replace(/\\/g, '/')
  if (rel === THEME_BRIDGE) continue
  const ownScale = OWN_SCALE_SURFACES.includes(rel)
  const src = stripBlockComments(readFileSync(file, 'utf8'))
  for (const m of src.matchAll(HEX)) {
    const hex = m[0].toLowerCase()
    counts.set(hex, (counts.get(hex) ?? 0) + 1)
    if (!filesByColor.has(hex)) filesByColor.set(hex, new Set())
    filesByColor.get(hex).add(rel)
    if (tokenSet.has(hex)) continue
    if (ownScale) continue
    nonToken += 1
    appCounts.set(hex, (appCounts.get(hex) ?? 0) + 1)
  }
}

// 3) 语义色近似检查
const semanticViolations = []
for (const [hex, n] of counts) {
  if (tokenSet.has(hex)) continue
  for (const [name, token] of Object.entries(SEMANTIC)) {
    if (distance(hex, token) <= 30) {
      semanticViolations.push({ hex, n, name, token, files: [...filesByColor.get(hex)].slice(0, 3) })
      break
    }
  }
}

// 4) 输出
const top = [...appCounts.entries()].sort((a, b) => b[1] - a[1]).slice(0, 12)

console.log(
  `token 数 ${tokenSet.size} ｜ 不同色值 ${counts.size} ｜ ` +
    `应用内非 token 出现 ${nonToken} 次（基线 ${BASELINE_NON_TOKEN}）` +
    ` ｜ 落地页自有灰阶已豁免`,
)
if (top.length) {
  console.log('应用内非 token 用量 Top:')
  for (const [hex, n] of top) {
    const where = [...filesByColor.get(hex)].slice(0, 2).join(', ')
    console.log(`  ${hex}  ×${n}  ${where}`)
  }
}

let failed = false

if (semanticViolations.length) {
  failed = true
  console.error(`\n✗ 语义色出现近似重复（应直接使用 token）：`)
  for (const v of semanticViolations) {
    console.error(`  ${v.hex} ×${v.n} ≈ --solo-${v.name} ${v.token}  ← ${v.files.join(', ')}`)
  }
}

if (nonToken > BASELINE_NON_TOKEN) {
  failed = true
  console.error(
    `\n✗ 非 token 色值用量上升到 ${nonToken}（基线 ${BASELINE_NON_TOKEN}）。` +
      `\n  新增颜色请先加进 variables.css 成为 token，或复用既有 token。`,
  )
}

if (failed && !REPORT_ONLY) process.exit(1)
console.log(failed ? '\n(报告模式，未失败退出)' : '\n✓ 色板检查通过')
