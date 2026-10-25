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
 * - 工具白名单：agent/mcp_server.py 的 MCP_READ_TOOLS / MCP_WRITE_TOOLS（19 + 5 = 24）
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
export const TOOL_TOTAL = 24

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

// ── 24 个原子工具：只按业务域列名字 ──
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
    tools: ['list_invoice_entries', 'list_bank_statements', 'list_unmatched', 'get_aging', 'get_advance_balances'],
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
  'propose_match',
  'propose_accrual',
  'propose_asset_closure',
]

export const READ_TOOL_COUNT = READ_TOOL_GROUPS.reduce((n, g) => n + g.tools.length, 0)

// ── 安全红线 ──

export const SAFETY_RULES: { icon: string; title: string; detail: string }[] = [
  {
    icon: 'ri-key-2-line',
    title: '令牌分级，最小权限',
    detail: '只读令牌只能查账；「读写草稿」才允许调用 propose_*。令牌绑定单一公司，可随时吊销，吊销立即生效。',
  },
  {
    icon: 'ri-draft-line',
    title: 'AI 只起草，不入账',
    detail: '所有写入都落成 pending 待确认草稿，必须你在产品内逐笔确认才生成凭证。关账动作根本不对外暴露。',
  },
  {
    icon: 'ri-shield-keyhole-line',
    title: '不碰税局账密，不代点提交',
    detail: '报税技能包全程零凭据接触；表单填完即停，最终「提交申报」必须老板本人点击。',
  },
  {
    icon: 'ri-file-search-line',
    title: '全程留痕，可审计',
    detail: '每次工具调用记录令牌指纹、参数摘要、耗时与成败；默认每分钟 60 次限流，异常调用有告警。',
  },
]

// ── 客户端实战矩阵 ──

export const CLIENT_GUIDES: ClientGuide[] = [
  {
    id: 'workbuddy',
    name: 'WorkBuddy',
    vendor: '腾讯 · AI 办公工作台',
    icon: 'ri-robot-2-line',
    tagline: '连接器面板一键挂载，适合非技术老板',
    transport: 'Streamable HTTP（远程端点 + Bearer 令牌）',
    summary:
      'WorkBuddy 把 MCP 服务统一叫「连接器」。挂上梭罗记账后，在对话里勾选连接器就能直接查账、跑结账体检、起草凭证。',
    steps: [
      {
        title: '先在梭罗记账签发令牌',
        detail: '「我的 → AI 连接（MCP）」→ 新建令牌，权限按需选「只读」或「读写草稿」，复制 sbk_ 开头的明文（只显示一次）。',
      },
      {
        title: '打开连接器面板',
        detail: 'WorkBuddy 左侧导航进入「连接器」→ 切到「自定义连接器」→ 添加 MCP 服务；也可以直接编辑本地 mcp.json 配置文件。',
      },
      {
        title: '填入端点与鉴权头',
        detail: '服务地址填本页给出的 MCP 端点，请求头加一行 Authorization: Bearer <令牌>。保存后点「信任」激活。',
      },
      {
        title: '在对话里勾选并试用',
        detail: '新建对话，输入框左下角「+ → 连接器」勾选 solobooks，问一句「帮我查上个月收支汇总」即可验证。',
      },
    ],
    samples: (ctx) => [
      {
        lang: 'json',
        label: '自定义连接器配置（mcp.json）',
        paths: {
          macos: '~/.workbuddy-ai/mcp.json（新版）/ ~/.workbuddy/mcp.json（旧版）',
          windows: '%USERPROFILE%\\.workbuddy-ai\\mcp.json（新版）/ %USERPROFILE%\\.workbuddy\\mcp.json（旧版）',
        },
        code: remoteConfig(ctx.endpoint, ctx.token),
      },
    ],
    verify: '连接器列表里 solobooks 显示绿色「已连接」；对话中工具调用会显示 mcp__solobooks__* 前缀。',
    tips: [
      '改完配置文件必须重启客户端，或在连接器页点「重新连接」，否则新服务不会被加载。',
      '客户端只信任 HTTPS 端点；本地调试请用 http://127.0.0.1:8001/mcp，不要用局域网 IP。',
    ],
  },
  {
    id: 'claude',
    name: 'Claude',
    vendor: 'Anthropic · Claude Desktop',
    icon: 'ri-sparkling-2-line',
    tagline: '官方参考客户端，配置一次长期可用',
    transport: 'Streamable HTTP；旧版客户端用 mcp-remote 代理兜底',
    summary:
      'Claude Desktop 通过本地配置文件注册 MCP 服务。新版客户端原生支持远程 url + 请求头；旧版只认 command，用 mcp-remote 转一层即可。',
    steps: [
      {
        title: '找到配置文件',
        detail: '首次配置需要手动创建文件（目录已存在、文件不存在是正常的）。改完完全退出 Claude 再重开。',
      },
      {
        title: '写入服务配置',
        detail: '把下面的 JSON 合并进 mcpServers（不要覆盖已有的其他服务）。令牌填 sbk_ 开头的明文。',
      },
      {
        title: '重启并检查工具',
        detail: '重开后在对话框输入框右下角点工具图标，应看到 solobooks 下 24 个工具。',
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
    verify: '对话框工具图标点亮，展开能看到 24 个账务工具；问「这个月赚了多少」应触发 get_month_summary。',
    tips: [
      'Claude 需要 Node.js 才能跑 npx；没装 Node 请用远程端点配置。',
      'JSON 不允许注释和尾逗号，粘错一个逗号整份配置都会失效。',
    ],
  },
  {
    id: 'qwen',
    name: '通义千问 / 百炼',
    vendor: '阿里云 · 百炼智能体平台',
    icon: 'ri-cloud-line',
    tagline: '把账套能力挂进中文票据理解场景',
    transport: 'MCP 服务（Streamable HTTP / SSE）或 OpenAPI 工具',
    summary:
      '在阿里云百炼控制台把梭罗记账注册为自定义 MCP 服务，再挂到智能体应用上。适合「拍票识别 + 账套核对」这类中文场景。',
    steps: [
      {
        title: '进入百炼 MCP 服务管理',
        detail: '百炼控制台 → 工具 / MCP 服务 → 创建自定义 MCP 服务，传输方式选 Streamable HTTP（若控制台只提供 SSE，选 SSE）。',
      },
      {
        title: '填写端点与鉴权',
        detail: '服务地址填本页端点，鉴权方式选 Bearer / 自定义 Header，值为 Authorization: Bearer <令牌>。保存后平台会做一次连通性探测。',
      },
      {
        title: '挂到智能体应用',
        detail: '在智能体编排页添加该 MCP 服务作为工具集，写好系统提示词（例如「涉及账务先调 solobooks 工具取数，不要编造金额」）。',
      },
      {
        title: '用中文票据场景验证',
        detail: '上传一张增值税发票让模型识别，再让它调 list_invoice_entries 核对是否已入账。',
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
超时：60s（报表与结账检查类工具耗时较长）`,
      },
    ],
    verify: '平台连通性探测通过、工具列表出现 24 项；智能体调试台能看到工具调用日志。',
    tips: [
      '百炼运行在公网，端点必须公网可达；本地实例请用内网穿透或反向代理暴露。',
      '部分版本控制台只支持 SSE，而梭罗记账是 Streamable HTTP 端点，若探测失败优先确认平台是否支持该传输。',
    ],
  },
  {
    id: 'ide',
    name: 'Trae / Cursor / Windsurf',
    vendor: 'AI IDE 三件套',
    icon: 'ri-code-s-slash-line',
    tagline: '写代码间隙顺手查账，开发者最顺手的姿势',
    transport: 'Streamable HTTP（IDE 内置 MCP 面板）',
    summary:
      '三家 IDE 都内置 MCP 面板，配置结构基本一致：一个 JSON、一个 url、一个鉴权头。项目级配置跟着仓库走，全局配置所有项目可用。',
    steps: [
      {
        title: '打开 MCP 设置',
        detail: 'Cursor：设置 → MCP → Add new MCP server；Trae：设置 → MCP → 添加；Windsurf：Cascade → MCP。也可以直接编辑配置文件。',
      },
      {
        title: '粘贴配置',
        detail: '项目级配置放 .cursor/mcp.json 或 .trae/mcp.json（跟着仓库走）；全局配置放用户目录。结构见右侧。',
      },
      {
        title: '在对话里提问',
        detail: '回到 Chat 面板，工具列表出现 solobooks 后，直接问「上季度研发费用累计多少，需要补提折旧吗」。',
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
    verify: 'MCP 面板里 solobooks 显示绿灯、工具数 24；Chat 里 @ 工具能看到 propose_voucher 等写入工具。',
    tips: [
      '项目级配置文件会进 Git。令牌不要提交到仓库——用全局配置，或把文件加进 .gitignore。',
      'IDE 的 MCP 连接失败通常没有明显报错，先在终端用 curl 打一次端点确认令牌有效。',
    ],
  },
  {
    id: 'cli',
    name: 'CLI / 自托管',
    vendor: 'Claude Code · 本地 stdio',
    icon: 'ri-terminal-box-line',
    tagline: '本地部署与命令行调试的完整路径',
    transport: '远程 HTTP，或本地 stdio 单进程模式',
    summary:
      '已经本地部署了梭罗记账？stdio 模式让 MCP 直接跑在你的机器上，不经过网络、不需要公网域名，适合私有化与开发调试。',
    steps: [
      {
        title: '远程模式：一条命令注册',
        detail: 'Claude Code 用户可直接用 claude mcp add 注册远程 HTTP 端点，令牌写在请求头里。',
      },
      {
        title: '本地模式：stdio 直连',
        detail: '用仓库里的 scripts/mcp_stdio.py，把数据库地址与令牌通过环境变量传进去，客户端按 command 方式启动它。',
      },
      {
        title: '自托管起服务',
        detail: '仓库自带 Docker Compose，起完服务后 /mcp 端点即可用；反代记得关闭缓冲并放宽读超时。',
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
        label: '自托管起服务',
        code: `git clone ${ctx.repoUrl}.git && cd solobooks-open
docker compose -f deploy/docker-compose.yml up -d
# 端点就绪后自检：未带令牌应返回 401，带令牌应广播 24 个工具
python scripts/verify_workbuddy_mcp.py --url http://127.0.0.1:8001/mcp --token ${ctx.token}`,
      },
    ],
    verify: 'verify_workbuddy_mcp.py 三项全 PASS：401 安全边界、握手成功、关键工具齐备。',
    tips: [
      'stdio 模式的令牌同样绑定公司，别用生产令牌跑测试库。',
      '反代配置：proxy_buffering off、proxy_read_timeout 600s，否则长连接会被截断。',
    ],
  },
]

// ── Skills 技能包 ──

export const SKILL_INSTALL_STEPS: GuideStep[] = [
  {
    title: '拿到技能包',
    detail: '从开源仓库克隆，或直接下载 skills/ 目录。每个技能包是一个独立文件夹，核心是 SKILL.md。',
  },
  {
    title: '放进客户端的技能目录',
    detail: '支持 Agent Skills 规范的客户端（WorkBuddy / Claude Code 等）扫描 skills/ 目录；把整个 solobooks-tax-filing 文件夹放进去即可被识别。',
  },
  {
    title: '同时挂上 MCP 服务',
    detail: '技能包只描述「怎么做」，取数要靠 MCP。所以 solobooks MCP 必须已连接，报税技能还需要一个浏览器 MCP（Chrome DevTools 或 Playwright）。',
  },
  {
    title: '用一句话触发',
    detail: '「去电子税务局报这个季度的增值税」——技能描述里的触发条件命中后，Agent 会按 SOP 一步步执行。',
  },
]

export const TAX_SKILL_STEPS: { step: string; title: string; detail: string }[] = [
  {
    step: '01',
    title: '拉税局底账',
    detail: '在已登录的电子税务局页面读取应申报清册、金税归集的开票销售额、历史待弥补亏损与已预缴税额。全程只读快照，不碰账密。',
  },
  {
    step: '02',
    title: '账 ↔ 税逆向对账',
    detail: '调 generate_filing_package 取账套测算值，与税局底账逐项比对。税局额度更大 = 有票没记账，Agent 会定位到具体发票。',
  },
  {
    step: '03',
    title: '阻塞拦截 / 补记草稿',
    detail: '结账体检未过或存在重大差异时严禁填表，先列阻塞原因；确认漏记的，用 propose_voucher 起草补记凭证，等你确认。',
  },
  {
    step: '04',
    title: '预填 + 高亮，停在提交前',
    detail: '把免税额、实际利润额填进申报表并黄色高亮标记，输出核对清单。绝不点「提交申报」——那一下必须由你亲手点。',
  },
]

export const ROADMAP_SKILLS: { title: string; detail: string }[] = [
  { title: '月末结账五项体检', detail: '一键跑完账证 / 账账 / 账实 / 账表 / 税表检查，阻塞项直接给修复动作。' },
  { title: '流水批量对平', detail: '微信、网银流水与账目批量勾稽，重复入账与漏记一次清干净。' },
]

// ── 排错 ──

export const TROUBLESHOOTING: { q: string; a: string }[] = [
  {
    q: '返回 401 / 无效或已失效的访问令牌',
    a: '令牌只显示一次，丢了只能重新签发。另外确认没有多复制空格、没有把 sbk_ 前缀丢掉、令牌没被吊销或过期。',
  },
  {
    q: '连接成功但工具列表是空的',
    a: '多为令牌绑定的公司与当前账套不匹配，或令牌只有 read 权限却去找 propose_*。到「我的 → AI 连接」核对权限与绑定公司。',
  },
  {
    q: '调用报「调用过于频繁」',
    a: '默认每个令牌每分钟 60 次。批量场景请合并查询条件、减少轮次，而不是提高频率。',
  },
  {
    q: 'AI 说它不能记账 / 只能生成草稿',
    a: '这是设计如此。写入工具只产生待确认草稿，回到梭罗记账「待确认」列表逐笔确认后才会生成凭证。',
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
