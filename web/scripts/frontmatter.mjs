// 极简 front-matter 解析（--- key: value --- 头部），供 /learn 文章链路共用：
// public-pages.mjs（sitemap/推送清单）与 build-learn.mjs（文章 HTML 生成）。
export function parseFrontMatter(md) {
  const match = /^---\r?\n([\s\S]*?)\r?\n---\r?\n?/.exec(md)
  if (!match) return { meta: {}, body: md }
  const meta = {}
  for (const line of match[1].split(/\r?\n/)) {
    const kv = /^([A-Za-z_][\w-]*)\s*:\s*(.*)$/.exec(line.trim())
    if (kv) meta[kv[1]] = kv[2].trim()
  }
  return { meta, body: md.slice(match[0].length) }
}
