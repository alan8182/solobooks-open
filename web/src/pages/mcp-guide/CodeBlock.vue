<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
/**
 * 深色终端代码块：轻量高亮 + 一键复制。
 *
 * 不引 highlight.js / shiki：这里只有 json / bash / text 三种短片段，
 * 一个正则足够，省掉几百 KB 依赖。所有内容来自 data.ts 常量，v-html 安全。
 */
import { computed, ref } from 'vue'
import type { CodeSample, VisitorOS } from './data'

const props = defineProps<{
  sample: CodeSample
  os: VisitorOS
}>()

const copied = ref(false)
const failed = ref(false)
let timer: ReturnType<typeof setTimeout> | undefined

const LANG_LABEL: Record<CodeSample['lang'], string> = {
  json: 'JSON',
  bash: 'SHELL',
  text: 'PARAMS',
}

function escapeHtml(input: string): string {
  return input.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

const JSON_RE = /(\/\/[^\n]*)|("(?:[^"\\]|\\.)*")(\s*:)?|\b(true|false|null)\b|(-?\b\d+(?:\.\d+)?\b)/g
const BASH_RE = /(#[^\n]*)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*')|(^|\s)(--?[a-zA-Z][\w-]*)/g

function highlight(code: string, lang: CodeSample['lang']): string {
  const escaped = escapeHtml(code)
  if (lang === 'text') return escaped

  if (lang === 'bash') {
    return escaped.replace(BASH_RE, (match, comment, str, lead, flag) => {
      if (comment) return `<span class="tok-comment">${comment}</span>`
      if (str) return `<span class="tok-string">${str}</span>`
      if (flag) return `${lead}<span class="tok-flag">${flag}</span>`
      return match
    })
  }

  return escaped.replace(JSON_RE, (match, comment, str, colon, keyword, num) => {
    if (comment) return `<span class="tok-comment">${comment}</span>`
    if (str) {
      // 紧跟冒号的字符串是键名，其余是值
      return colon
        ? `<span class="tok-key">${str}</span><span class="tok-punct">${colon}</span>`
        : `<span class="tok-string">${str}</span>`
    }
    if (keyword) return `<span class="tok-keyword">${keyword}</span>`
    if (num) return `<span class="tok-number">${num}</span>`
    return match
  })
}

const html = computed(() => highlight(props.sample.code, props.sample.lang))

async function copy() {
  try {
    await navigator.clipboard.writeText(props.sample.code)
    copied.value = true
    failed.value = false
  } catch {
    failed.value = true
    copied.value = false
  }
  clearTimeout(timer)
  timer = setTimeout(() => {
    copied.value = false
    failed.value = false
  }, 2200)
}

const pathRows = computed(() => {
  const p = props.sample.paths
  if (!p) return []
  const rows = [
    { key: 'macos' as const, icon: 'ri-apple-line', text: p.macos },
    { key: 'windows' as const, icon: 'ri-windows-line', text: p.windows },
  ]
  // 认出系统就只留那一条：两条都铺开时长路径必然折行，反而看不清该复制哪个。
  // 认不出（Linux / 未知 UA）才把两条都摆出来让用户自己挑。
  if (props.os === 'other') return rows
  return rows.filter((row) => row.key === props.os)
})
</script>

<template>
  <div class="code-block">
    <div class="code-head">
      <span class="code-lang">{{ LANG_LABEL[sample.lang] }}</span>
      <span class="code-label">{{ sample.label }}</span>
      <button class="copy-btn" type="button" @click="copy">
        <i :class="copied ? 'ri-check-line' : 'ri-file-copy-line'" />
        {{ copied ? '已复制' : failed ? '手动复制' : '复制' }}
      </button>
    </div>

    <div v-if="pathRows.length" class="path-hint">
      <div
        v-for="row in pathRows"
        :key="row.key"
        class="path-row"
        :class="{ active: os === row.key }"
      >
        <i :class="row.icon" />
        <code>{{ row.text }}</code>
        <span v-if="os === row.key" class="path-badge">你的系统</span>
      </div>
    </div>

    <pre class="code-body"><code v-html="html" /></pre>
  </div>
</template>

<style scoped>
.code-block {
  border: 1px solid #1e293b;
  border-radius: 10px;
  overflow: hidden;
  background: #0f172a;
}

.code-head {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 9px 12px;
  background: #16233b;
  border-bottom: 1px solid #1e293b;
}

.code-lang {
  font-size: 10px;
  font-weight: 700;
  letter-spacing: 0.08em;
  color: #93c5fd;
  background: rgba(147, 197, 253, 0.12);
  border-radius: 4px;
  padding: 2px 6px;
  flex-shrink: 0;
}

.code-label {
  flex: 1;
  min-width: 0;
  font-size: 11.5px;
  color: #94a3b8;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.copy-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid #334155;
  background: #1e293b;
  color: #cbd5e1;
  font-size: 11.5px;
  padding: 3px 9px;
  border-radius: 6px;
  cursor: pointer;
  flex-shrink: 0;
  transition: background 0.16s, color 0.16s, border-color 0.16s;
}

.copy-btn:hover {
  background: #27364f;
  color: #ffffff;
  border-color: #475569;
}

.path-hint {
  padding: 8px 12px;
  background: #0b1220;
  border-bottom: 1px solid #1e293b;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.path-row {
  display: flex;
  align-items: center;
  gap: 7px;
  font-size: 11.5px;
  color: #64748b;
  line-height: 1.5;
}

.path-row i {
  font-size: 12px;
  flex-shrink: 0;
}

.path-row code {
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  word-break: break-all;
}

.path-row.active {
  color: #e2e8f0;
}

.path-badge {
  flex-shrink: 0;
  font-size: 9.5px;
  color: #93c5fd;
  border: 1px solid rgba(147, 197, 253, 0.4);
  border-radius: 4px;
  padding: 0 4px;
  line-height: 1.5;
}

.code-body {
  margin: 0;
  padding: 13px 14px;
  overflow-x: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  font-size: 12px;
  line-height: 1.72;
  color: #e2e8f0;
  tab-size: 2;
}

.code-body code {
  font-family: inherit;
  white-space: pre;
}

/* 语法着色：只保留四种语义色，避免调色板失控 */
:deep(.tok-key) {
  color: #93c5fd;
}
:deep(.tok-string) {
  color: #86efac;
}
:deep(.tok-number) {
  color: #fcd34d;
}
:deep(.tok-keyword) {
  color: #f0abfc;
}
:deep(.tok-comment) {
  color: #64748b;
  font-style: italic;
}
:deep(.tok-punct) {
  color: #94a3b8;
}
:deep(.tok-flag) {
  color: #fcd34d;
}
</style>
