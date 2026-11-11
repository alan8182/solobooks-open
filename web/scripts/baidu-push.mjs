// 百度普通收录 · API 推送（百度搜索资源平台 → 数据引入 → 普通收录）。
// 用法：
//   node scripts/baidu-push.mjs --all        推送全部公开页（public-pages.mjs 清单）
//   node scripts/baidu-push.mjs /learn/xx    推送指定路径，可传多个；传完整 URL 也可以
// token 读取顺序：环境变量 BAIDU_PUSH_TOKEN → web/.baidu-push-token（已 gitignore，勿提交）。
// 建议时机：每发布一篇 /learn 文章推一条。同一 URL 短时间重复推送不判重、照扣配额，别手滑。
// 传输层用系统 curl（官方示例客户端）：实测百度接口对 Node fetch 的请求指纹会间歇性
// 返回 success 0（配额不扣、URL 不入队），curl 稳定。
import { existsSync, readFileSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'
import { PAGES, SITE_URL } from './public-pages.mjs'

const args = process.argv.slice(2)
const paths = (args.includes('--all') ? PAGES.map((p) => p.path) : args.filter((a) => !a.startsWith('--'))).map(
  (p) => (p.startsWith('http') ? new URL(p).pathname : p),
)
if (paths.length === 0) {
  console.error('用法: node scripts/baidu-push.mjs --all | <路径...>')
  process.exit(1)
}

const tokenFile = fileURLToPath(new URL('../.baidu-push-token', import.meta.url))
const token = process.env.BAIDU_PUSH_TOKEN ?? (existsSync(tokenFile) ? readFileSync(tokenFile, 'utf8').trim() : '')
if (!token) {
  console.error('缺少 token：设置环境变量 BAIDU_PUSH_TOKEN，或在 web/.baidu-push-token 文件中保存（该文件已 gitignore）')
  process.exit(1)
}

const body = [...new Set(paths)].map((p) => `${SITE_URL}${p}`).join('\n')
const api = `http://data.zz.baidu.com/urls?site=${SITE_URL}&token=${token}`
const res = spawnSync(
  'curl',
  ['-s', '-m', '20', '-H', 'Content-Type:text/plain', '--data-binary', body, api],
  { encoding: 'utf8' },
)
if (res.error || !res.stdout) {
  console.error(`[baidu-push] curl 调用失败: ${res.error ?? '空响应'}`)
  process.exit(1)
}
const data = JSON.parse(res.stdout)

if (data.success !== undefined) {
  console.log(`[baidu-push] 推送成功 ${data.success} 条，今日剩余配额 ${data.remain} 条`)
} else {
  console.error(`[baidu-push] 推送失败: ${res.stdout.trim()}（error 401=token 不合法，402=配额用尽，400=超配额/格式错误）`)
  process.exit(1)
}
