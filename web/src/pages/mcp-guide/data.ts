/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 *
 * AI 连接中心（MCP & Skills）内容数据层。
 *
 * 页面只负责渲染，所有客户端配置、工具清单、技能包说明都在这里结构化。
 * 新增一个客户端 = 往 CLIENT_GUIDES 里加一项，不改模板。
 *
 * 事实来源（改动前请核对后端）：
 * - 端点与传输：backend/agent/mcp_server.py（Streamable HTTP，stateless + JSON 响应）
 * - 工具白名单：agent/mcp_server.py 的 MCP_READ_TOOLS / MCP_WRITE_TOOLS（20 + 7 = 27）
 * - 令牌格式与权限：services/mcp_token_service.py（sbk_ 前缀，sha256 落库）
 * - 技能包：skills/solobooks-tax-filing/SKILL.md
 */

export type SampleLang = 'json' | 'bash' | 'text'

export interface CodeSample {
  /** 代码块左上角的语言标签 */
  lang: SampleLang
  /** 这块配置的用途，如「客户端配置」「连通性自检」 */
  label: string
  /** 配置文件位置；按访问者系统高亮对应一条 */
  paths?: { macos: string; windows: string }
  code: string
}

export interface GuideStep {
  title: string
  detail: string
}

export interface ConfigContext {
  /** 当前站点的 MCP 端点，形如 https://solobooks.cn/mcp */
  endpoint: string
  /** 用户临时粘贴的令牌；未粘贴时为占位符 */
  token: string
  /** 开源仓库地址 */
  repoUrl: string
}

export interface ClientGuide {
  id: string
  name: string
  vendor: string
  icon: string
  /** 一句话定位，用于 Tab 副标题 */
  tagline: string
  /** 传输方式说明 */
  transport: string
  summary: string
  steps: GuideStep[]
  samples: (ctx: ConfigContext) => CodeSample[]
  /** 接入成功后的验证动作 */
  verify: string
  tips: string[]
}

/** 令牌占位符：用户没粘贴令牌时配置里显示它 */
export const TOKEN_PLACEHOLDER = '<YOUR_TOKEN>'

/** 开源仓库（README、GitHub 徽标、克隆命令共用一处，换仓库只改这里） */
export const GITHUB_REPO = 'alan8182/solobooks-open'
export const GITHUB_URL = `https://github.com/${GITHUB_REPO}`

/** 站内令牌管理页 */
export const TOKEN_PAGE = '/mine/mcp'

/** 对外宣称的工具总数，与后端白名单联动（tests 里有一致性断言） */
export const TOOL_TOTAL = 27

export const ENDPOINT_PATH = '/mcp'

/** 由站点 origin 推导 MCP 端点。生产环境恒为 https://solobooks.cn/mcp */
export function resolveEndpoint(origin: string): string {
  const base = origin.replace(/\/+$/, '')
  return `${base}${ENDPOINT_PATH}`
}

/** 远程 MCP 的标准 JSON 配置（Claude / WorkBuddy / Cursor 同构） */
export function remoteConfig(endpoint: string, token: string): string {
  return `{
  "mcpServers": {
    "solobooks": {
      "url": "${endpoint}",
      "headers": {
        "Authorization": "Bearer ${token}"
      }
    }
  }
}`
}

/** 带鉴权头的 mcp-remote 代理命令：给还不支持远程 url 的旧版客户端兜底 */
export function mcpRemoteCommand(endpoint: string, token: string): string {
  return `npx -y mcp-remote ${endpoint} \\
  --header "Authorization: Bearer ${token}"`
}

// ── 27 个原子工具：只按业务域列名字 ──
// 逐条写中文说明会把接入页变成 API 手册，而工具描述本来就由 MCP 在握手时广播给客户端，
// 用户在自己的 AI 里能看到。这里只需要「有哪些、属于哪一类」。

export interface ToolGroup {
  title: string
  icon: string
  tools: string[]
}

export const READ_TOOL_GROUPS: ToolGroup[] = [
  {
    title: '账目与凭证',
    icon: 'ri-book-2-line',
    tools: ['get_month_summary', 'list_entries', 'get_entry_detail', 'search_books', 'get_company_profile'],
  },
  {
    title: '票据与流水',
    icon: 'ri-file-list-3-line',
    tools: ['list_invoice_entries', 'list_bank_statements', 'list_unmatched', 'list_private_expenses', 'get_aging', 'get_advance_balances'],
  },
  {
    title: '结账与资产',
    icon: 'ri-calendar-check-line',
    tools: [
      'run_close_checks',
      'generate_close_report',
      'preview_accruals',
      'preview_asset_actions',
      'list_fixed_assets',
      'list_intangible_assets',
    ],
  },
  {
    title: '税务与待办',
    icon: 'ri-government-line',
    tools: ['get_filing_deadlines', 'generate_filing_package', 'list_todos'],
  },
]

export const WRITE_TOOLS: string[] = [
  'propose_voucher',
  'propose_vouchers',
  'propose_statement_vouchers',
  'propose_match',
  'propose_accrual',
  'propose_asset_closure',
  'record_private_expense',
]

export const READ_TOOL_COUNT = READ_TOOL_GROUPS.reduce((n, g) => n + g.tools.length, 0)

// ── 安全红线 ──

export const SAFETY_RULES: { icon: string; title: string; detail: string }[] = [
  {
    icon: 'ri-key-2-line',
    title: '令牌分级，最小权限',
    detail: '只读与「读写草稿」两级权限，绑定单一账套，支持随时吊销并立即生效。',
  },
  {
    icon: 'ri-draft-line',
    title: 'AI 只起草，不入账',
    detail: '所有写操作仅生成待确认草稿；最终入账与锁账必须由用户在产品内确认。',
  },
  {
    icon: 'ri-shield-keyhole-line',
    title: '不碰税局账密，不代提交',
    detail: '报税技能包不读取税局账号密码，预填完成后停在提交前由本人确认。',
  },
  {
    icon: 'ri-file-search-line',
    title: '全程审计与限流',
    detail: '每次调用记录令牌指纹与参数摘要；默认 60 次/分钟限流防异常刷写。',
  },
]

// ── 客户端实战矩阵 ──

export const CLIENT_GUIDES: ClientGuide[] = [
  {
    id: 'workbuddy',
    name: 'WorkBuddy',
    vendor: '腾讯 · AI 办公工作台',
    icon: 'ri-robot-2-line',
    tagline: '官方插件商城搜「梭罗记账」，填令牌即用',
    transport: '官方插件商城 / Streamable HTTP',
    summary: 'WorkBuddy 官方插件商城已上架「梭罗记账」，搜索安装并配置访问令牌即可直接使用。',
    steps: [
      {
        title: '签发访问令牌',
        detail: '在「我的 → AI 连接」新建令牌并复制 sbk_ 明文。',
      },
      {
        title: '插件商城安装「梭罗记账」',
        detail: '打开 WorkBuddy 官方插件商城，搜索「梭罗记账」并安装，填入访问令牌即可。',
      },
      {
        title: '对话中启用',
        detail: '启用后在对话输入框勾选梭罗记账连接器，即可直接查账与起草凭证。',
      },
    ],
    samples: (ctx) => [
      {
        lang: 'json',
        label: '手动配置兜底（自托管 / mcp.json）',
        paths: {
          macos: '~/.workbuddy-ai/mcp.json（新版）/ ~/.workbuddy/mcp.json（旧版）',
          windows: '%USERPROFILE%\\.workbuddy-ai\\mcp.json（新版）/ %USERPROFILE%\\.workbuddy\\mcp.json（旧版）',
        },
        code: remoteConfig(ctx.endpoint, ctx.token),
      },
    ],
    verify: '插件启用后连接状态显示「已连接」，对话中出现 mcp__solobooks__* 工具调用。',
    tips: [
      '官方商城版免写配置即可接入；自托管或本地调试可使用右侧 mcp.json 手动配置（本地请用 http://127.0.0.1:8001/mcp）。',
      '更换或吊销令牌后，在插件配置中更新令牌并重新连接即可生效。',
    ],
  },
  {
    id: 'claude',
    name: 'Claude',
    vendor: 'Anthropic · Claude Desktop',
    icon: 'ri-chat-3-line',
    tagline: '原生支持远程端点，稳定通用',
    transport: 'Streamable HTTP / mcp-remote',
    summary: '在 claude_desktop_config.json 中写入远程配置后重启。',
    steps: [
      {
        title: '打开配置文件',
        detail: '打开对应系统路径下的 claude_desktop_config.json（首次配置需新建）。',
      },
      {
        title: '写入服务配置',
        detail: '将右侧 JSON 合并至 mcpServers，并替换为你的 sbk_ 令牌。',
      },
      {
        title: '重启生效',
        detail: '完全退出 Claude Desktop 并重开，加载全部 27 个工具。',
      },
    ],
    samples: (ctx) => [
      {
        lang: 'json',
        label: '远程端点配置（推荐）',
        paths: {
          macos: '~/Library/Application Support/Claude/claude_desktop_config.json',
          windows: '%APPDATA%\\Claude\\claude_desktop_config.json',
        },
        code: remoteConfig(ctx.endpoint, ctx.token),
      },
      {
        lang: 'json',
        label: '旧版客户端兜底（command + mcp-remote）',
        code: `{
  "mcpServers": {
    "solobooks": {
      "command": "npx",
      "args": [
        "-y",
        "mcp-remote",
        "${ctx.endpoint}",
        "--header",
        "Authorization: Bearer ${ctx.token}"
      ]
    }
  }
}`,
      },
    ],
    verify: '输入框工具图标点亮并列出 27 个工具，提问「这个月赚了多少」会调用 get_month_summary。',
    tips: [
      '仅旧版 command + mcp-remote 方式依赖 Node.js，推荐优先使用远程配置。',
      'JSON 严禁出现注释或末尾多余逗号。',
    ],
  },
  {
    id: 'qwen',
    name: '通义千问 / 百炼',
    vendor: '阿里云 · 百炼智能体平台',
    icon: 'ri-cloud-line',
    tagline: '适合票据识别与中文智能体编排',
    transport: 'Streamable HTTP / Bearer',
    summary: '在百炼控制台创建自定义 MCP 服务并绑定到智能体。',
    steps: [
      {
        title: '创建自定义 MCP 服务',
        detail: '百炼控制台进入「工具 / MCP 服务」，传输方式选 Streamable HTTP。',
      },
      {
        title: '配置端点与鉴权',
        detail: '填入服务地址与 Authorization: Bearer <令牌> 请求头，保存完成连通性探测。',
      },
      {
        title: '绑定智能体应用',
        detail: '在智能体工具集添加 solobooks，提示词要求优先调用工具取数。',
      },
    ],
    samples: (ctx) => [
      {
        lang: 'text',
        label: '服务注册参数',
        code: `服务名称：solobooks
服务描述：梭罗记账账套查询与凭证草稿（AI 只起草，人工确认后入账）
传输方式：Streamable HTTP
服务地址：${ctx.endpoint}
鉴权类型：Bearer Token / 自定义 Header
请求头：Authorization: Bearer ${ctx.token}
超时：60s`,
      },
    ],
    verify: '平台连通性探测通过并识别 27 项工具，调试台可见调用日志。',
    tips: [
      '百炼云端调用要求端点公网可达，本地调试需配合反向代理或穿透。',
      '请确认所选控制台版本已支持 Streamable HTTP 传输协议。',
    ],
  },
  {
    id: 'ide',
    name: 'Trae / Cursor / Windsurf',
    vendor: 'AI IDE 三件套',
    icon: 'ri-code-s-slash-line',
    tagline: '编码时直接查账与核对财务数据',
    transport: 'Streamable HTTP（IDE 内置）',
    summary: '在 IDE MCP 设置中添加或直接编辑 mcp.json。',
    steps: [
      {
        title: '打开 MCP 配置',
        detail: '在 Cursor / Trae 设置的 MCP 面板新增服务，或直接新建 mcp.json。',
      },
      {
        title: '填入配置 JSON',
        detail: '写入右侧配置，推荐放全局目录以避免令牌误提交进仓库。',
      },
      {
        title: '在 Chat 中调用',
        detail: '待状态变为绿灯后，直接在对话中查询账目或生成草稿。',
      },
    ],
    samples: (ctx) => [
      {
        lang: 'json',
        label: '项目级 MCP 配置',
        paths: {
          macos: '<项目根>/.cursor/mcp.json（Cursor）/ <项目根>/.trae/mcp.json（Trae）',
          windows: '<项目根>\\.cursor\\mcp.json（Cursor）/ <项目根>\\.trae\\mcp.json（Trae）',
        },
        code: remoteConfig(ctx.endpoint, ctx.token),
      },
      {
        lang: 'json',
        label: '全局配置（所有项目可用）',
        paths: {
          macos: '~/.cursor/mcp.json',
          windows: '%USERPROFILE%\\.cursor\\mcp.json',
        },
        code: remoteConfig(ctx.endpoint, ctx.token),
      },
    ],
    verify: 'MCP 面板显示绿灯且列出 27 个工具，Chat 中可正常调用。',
    tips: [
      '项目级 mcp.json 容易误入 Git，切勿将令牌提交到代码仓库。',
      '若面板无报错但连不上，可先用 curl 验证端点与令牌是否返回 200。',
    ],
  },
  {
    id: 'cli',
    name: 'CLI / 自托管',
    vendor: 'Claude Code · 本地 stdio',
    icon: 'ri-terminal-box-line',
    tagline: '命令行接入与私有化部署',
    transport: 'HTTP / 本地 stdio',
    summary: '支持 claude mcp add 一键注册、stdio 本地直连与 Docker 部署。',
    steps: [
      {
        title: '命令行注册远程端点',
        detail: 'Claude Code 执行右侧 claude mcp add 命令即可完成接入。',
      },
      {
        title: '本地 stdio 直连（可选）',
        detail: '私有化环境可直接通过 scripts/mcp_stdio.py 以进程模式连接数据库。',
      },
      {
        title: 'Docker 自托管启动',
        detail: '使用 Docker Compose 启动服务后，用验证脚本确认 27 个工具握手正常。',
      },
    ],
    samples: (ctx) => [
      {
        lang: 'bash',
        label: 'Claude Code 注册远程端点',
        code: `claude mcp add --transport http solobooks ${ctx.endpoint} \\
  --header "Authorization: Bearer ${ctx.token}"`,
      },
      {
        lang: 'json',
        label: '本地 stdio 配置（无需公网）',
        code: `{
  "mcpServers": {
    "solobooks": {
      "command": "<backend目录>/venv/bin/python",
      "args": ["<backend目录>/scripts/mcp_stdio.py"],
      "env": {
        "MCP_STDIO_TOKEN": "${ctx.token}",
        "DATABASE_URL": "sqlite+aiosqlite:///data/app.db"
      }
    }
  }
}`,
      },
      {
        lang: 'bash',
        label: '自托管启动与自检',
        code: `git clone ${ctx.repoUrl}.git && cd solobooks-open
docker compose -f deploy/docker-compose.yml up -d
python scripts/verify_workbuddy_mcp.py --url http://127.0.0.1:8001/mcp --token ${ctx.token}`,
      },
    ],
    verify: 'verify_workbuddy_mcp.py 输出三项全 PASS（鉴权边界 / 握手 / 工具完整）。',
    tips: [
      'stdio 模式同样严格校验公司绑定关系，切勿混用测试与生产令牌。',
      'Nginx 反代需配置 proxy_buffering off 与 proxy_read_timeout 600s。',
    ],
  },
]

// ── Skills 技能包 ──

export const SKILL_INSTALL_STEPS: GuideStep[] = [
  {
    title: '获取技能目录',
    detail: '克隆开源仓库或下载 skills/ 目录，每个技能包核心为 SKILL.md。',
  },
  {
    title: '放入客户端 skills 目录',
    detail: '将 solobooks-tax-filing 文件夹放入客户端对应的技能扫描路径。',
  },
  {
    title: '配合 MCP 使用',
    detail: '确保已连接 solobooks MCP（报税技能还需搭配浏览器 MCP）。',
  },
  {
    title: '自然语言触发',
    detail: '输入「去电子税务局报这季度增值税」即可按标准 SOP 执行。',
  },
]

export const TAX_SKILL_STEPS: { step: string; title: string; detail: string }[] = [
  {
    step: '01',
    title: '读取税局底账',
    detail: '只读获取已登录电子税务局的清册、开票销售额及历史预缴/亏损数据。',
  },
  {
    step: '02',
    title: '账税逆向对账',
    detail: '调用 generate_filing_package 逐项比对，自动定位漏记发票。',
  },
  {
    step: '03',
    title: '阻塞校验 / 补记草稿',
    detail: '结账未过即拦截并列出原因；确认漏记则调用 propose_voucher 生成草稿。',
  },
  {
    step: '04',
    title: '预填并停在提交前',
    detail: '填表并高亮关键金额、输出核对清单，最终「提交申报」由老板本人点击。',
  },
]

export const ROADMAP_SKILLS: { title: string; detail: string }[] = [
  { title: '月末结账五项体检', detail: '一键跑完账证/账账/账实/账表/税表核查并给出修复建议。' },
  { title: '流水批量对平', detail: '微信、网银流水与账目批量勾稽，自动拦截重复与漏记。' },
]

// ── 排错 ──

export const TROUBLESHOOTING: { q: string; a: string }[] = [
  {
    q: '401 / 无效或已失效令牌',
    a: '检查是否遗漏 sbk_ 前缀、多了空格，或令牌已吊销/过期（明文丢失需重新签发）。',
  },
  {
    q: '连接成功但工具为空',
    a: '检查令牌绑定的公司是否正确，以及是否具备「读写草稿」权限。',
  },
  {
    q: '提示「调用过于频繁」',
    a: '默认每令牌限流 60 次/分钟，建议合并批量查询条件减少请求次数。',
  },
  {
    q: 'AI 提示只能生成草稿',
    a: '安全机制设计如此，所有写操作需在「待确认」列表人工确认后才正式入账。',
  },
]

// ── 系统识别（配置路径高亮用）──

export type VisitorOS = 'macos' | 'windows' | 'other'

export function detectOS(ua: string): VisitorOS {
  const s = ua.toLowerCase()
  if (s.includes('windows')) return 'windows'
  if (s.includes('mac os') || s.includes('macintosh')) return 'macos'
  return 'other'
}
