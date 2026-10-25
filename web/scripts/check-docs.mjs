#!/usr/bin/env node
/**
 * 文档与代码的 token 一致性守卫。
 *
 * 为什么需要它：2026-10-20 的排查发现三份设计文档里有两份在说谎——
 * DESIGN.md 和 DESIGN_SYSTEM.md 还写着已经废弃的 #8A95A3 / #12805C / #C74444，
 * 而 check:palette 只管代码里的散装 hex，管不到文档。照旧文档写新页面，
 * 会直接把已废弃的色值又引回来。
 *
 * 校验范围（只查「声明式」的地方，正文里提到历史值不算）：
 *   1. DESIGN.md frontmatter 的 colors 段
 *   2. DESIGN_SYSTEM.md 的 token 表
 * 真值来源只有一个：src/assets/styles/variables.css
 *
 * 用法：cd web && node scripts/check-docs.mjs
 */
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const root = resolve(here, '../..')
const webRoot = resolve(here, '..')

const read = (p) => readFileSync(p, 'utf8')
const norm = (hex) => hex.trim().toUpperCase()

/* ---------- 真值：variables.css ---------- */
const variablesCss = read(resolve(webRoot, 'src/assets/styles/variables.css'))
const tokenValue = new Map()
for (const m of variablesCss.matchAll(/(--solo-[a-z0-9-]+)\s*:\s*(#[0-9a-fA-F]{6})\b/g)) {
  tokenValue.set(m[1], norm(m[2]))
}
// 兼容别名不在「声明式」文档里出现，但要参与取值校验
for (const m of variablesCss.matchAll(/(--solo-[a-z0-9-]+)\s*:\s*var\((--solo-[a-z0-9-]+)\)/g)) {
  const target = tokenValue.get(m[2])
  if (target) tokenValue.set(m[1], target)
}

/* ---------- DESIGN.md frontmatter colors ---------- */
// 文档用的是语义名（primary / text-muted），不是 token 名，映射显式写出来
const SEMANTIC_TO_TOKEN = {
  primary: '--solo-primary',
  'primary-hover': '--solo-primary-light',
  'primary-pressed': '--solo-primary-dark',
  page: '--solo-bg-page',
  surface: '--solo-bg-card',
  'surface-soft': '--solo-bg-soft',
  'primary-soft': '--solo-bg-blue-soft',
  'text-primary': '--solo-text-primary',
  'text-secondary': '--solo-text-secondary',
  'text-muted': '--solo-text-muted',
  border: '--solo-border',
  'border-light': '--solo-border-light',
  income: '--solo-income',
  'income-soft': '--solo-income-bg',
  warning: '--solo-warning',
  'warning-soft': '--solo-warning-bg',
  expense: '--solo-expense',
  'expense-soft': '--solo-expense-bg',
  capital: '--solo-capital',
  'capital-soft': '--solo-capital-bg',
}

const problems = []
const designMd = read(resolve(root, 'DESIGN.md'))
const fm = /^---\n([\s\S]*?)\n---/.exec(designMd)
let designChecked = 0
if (!fm) {
  problems.push('DESIGN.md 找不到 frontmatter')
} else {
  // 按行扫描，遇到顶格的下一段就停。不要用 lookahead 定义块边界——
  // `(?=\n[a-zA-Z]|\n*$)` 在 multiline 下会在第一个换行就成立，捕获到空块，
  // 守卫会变成"什么都校验、永远通过"的空壳（第一版就这么写的）。
  const lines = fm[1].split('\n')
  const start = lines.findIndex((l) => l.trim() === 'colors:')
  if (start === -1) {
    problems.push('DESIGN.md frontmatter 缺 colors 段')
  } else {
    for (let i = start + 1; i < lines.length; i++) {
      const line = lines[i]
      if (line && !line.startsWith(' ')) break // 顶格 → 下一段
      const m = /^\s{2}([a-z-]+):\s*"(#[0-9a-fA-F]{6})"/.exec(line)
      if (!m) continue
      const [, name, hex] = m
      designChecked += 1
      const token = SEMANTIC_TO_TOKEN[name]
      if (!token) {
        problems.push(`DESIGN.md: colors.${name} 没有对应的 token 映射（新增色时请同步 SEMANTIC_TO_TOKEN）`)
        continue
      }
      const actual = tokenValue.get(token)
      if (!actual) {
        problems.push(`DESIGN.md: colors.${name} 指向 ${token}，但 variables.css 里没有这个 token`)
      } else if (actual !== norm(hex)) {
        problems.push(`DESIGN.md: colors.${name} = ${hex}，但 ${token} 实际是 ${actual}`)
      }
    }
    if (designChecked === 0) problems.push('DESIGN.md colors 段解析到 0 行，守卫失效')
  }
}

/* ---------- DESIGN_SYSTEM.md token 表 ---------- */
const systemMd = read(resolve(root, 'DESIGN_SYSTEM.md'))
let systemRows = 0
for (const line of systemMd.split('\n')) {
  const m = /^\|\s*[^|]+\|\s*`(--solo-[a-z0-9-]+)`\s*\|\s*`(#[0-9a-fA-F]{6})`/.exec(line)
  if (!m) continue
  systemRows += 1
  const [, token, hex] = m
  const actual = tokenValue.get(token)
  if (!actual) {
    problems.push(`DESIGN_SYSTEM.md: 表里写了 ${token}，但 variables.css 没有这个 token`)
  } else if (actual !== norm(hex)) {
    problems.push(`DESIGN_SYSTEM.md: ${token} 写的是 ${hex}，实际是 ${actual}`)
  }
}
if (systemRows === 0) problems.push('DESIGN_SYSTEM.md 没解析到任何 token 表行')

/* ---------- 输出 ---------- */
if (problems.length) {
  console.error('✗ 文档与 token 不一致：')
  for (const p of problems) console.error('  -', p)
  console.error('\n真值来源：web/src/assets/styles/variables.css')
  process.exit(1)
}

console.log(
  `✓ 文档 token 一致（DESIGN.md colors ${designChecked} 项 / DESIGN_SYSTEM.md ${systemRows} 行 / variables.css ${tokenValue.size} 个 token）`,
)
