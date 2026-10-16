# SoloBooks MCP 接入方案

> 2026-10-12 · 基于 main @ ad42153 代码现状制定
> **M0 已落地（2026-10-12）**：实际交付 14 个只读工具（较计划多 `get_filing_deadlines`）；挂载方式为根路径 mount + 内部路径 `/mcp`（避免前缀 307）；依赖锁定 `fastmcp==4.1.0`，web 栈随其硬性要求整体升级（fastapi 0.143 / starlette 1.7 / pydantic 2.14 / uvicorn 0.54，详见 §8.9——早期记录的 starlette 0.41.3 钉版方案实际不可安装，已废弃）。
> **M1 已落地（2026-10-12）**：写入工具 5 个放开（同日修正为 4 个，见下方「画像写入下架」；`preview_accruals` 实现确认为纯预览，改归 read——较计划的一处偏差）；`mcp_call_logs` 留痕 + 进程内滑动窗口限流（`MCP_RATE_LIMIT_PER_MINUTE`，默认 60）；token CRUD API `/api/mcp/tokens`（为其他公司签发需成员校验）；「我的」页新增「AI 连接（MCP）」管理界面。
> **M2 已落地（2026-10-12）**：4 个资源模板（`solobooks://entries/{entry_id}`、`reports/{month}`、`close-checks/{month}`、`company/profile`，复用工具层口径，留痕为 `resource:<uri>`）；2 个提示词（`closing_guide`、`reconciliation_review`）；nginx `/mcp` 位置块（SSE 关闭缓冲、读超时 600s）；对外接入文档 `docs/mcp-integration.md`。MCP 三期全部完成。
> **画像写入下架（2026-10-12 修正）**：`update_company_profile` 从 MCP 开放集合移除（写 5→4），MCP 侧公司画像只读。理由：画像是驱动后续 AI 建议的长期记忆（分类器提示 + agent system prompt），且 `notes` 会逐字进入 system prompt——外部令牌直写等于给建议回路开投毒通道；而外部 agent 对画像的真实需求是读（起草更准），写回已有产品内确认回路自增长。恢复开放的唯一路径：走 pending 草稿（agent_actions 加 profile_update 动作类型），不恢复直写。详见 §8.5。
> 目标：把「梭罗记账」的记账与查账能力以 MCP（Model Context Protocol）Server 形式开放给外部 AI 客户端（Claude Desktop、ZCode 等），让用户在自己的 AI 助手里直接查账、对账、起草凭证。

## 一、为什么值得做 & 核心原则

一人公司老板的主要工作环境正在向 AI 客户端迁移。MCP 接入后："帮我看看 9 月账上还有几笔没对平"、"把这笔回款记到飞书咨询项目里"这类需求可以在 Claude/ZCode 里直接完成，不必打开工作台。

**不可动摇的安全原则**：延续产品既有的「AI 起草、人工确认」叙事——**MCP 侧永远不直接入账**。写入类工具只产生待确认草稿（复用 `agent_actions` 审批流），最终确认必须在产品内完成。这既是合规底线，也是对外宣传的差异点：外部 Agent 可以提议，但账本的钥匙始终在老板手里。

## 二、可复用的现有资产（摸底结论）

| 资产 | 位置 | 对 MCP 的价值 |
|---|---|---|
| 工具执行层 | `backend/agent/tools.py` | `Tool(name/description/parameters/handler)` 四件套 + `TOOL_SCHEMAS` + `execute_tool(name, args, ctx)` 统一入口，**20 个工具一个调用点全覆盖**；输出已做 `_truncate` 裁剪，天然适合 MCP 往返体积 |
| 工具上下文 | `ToolContext(user_id, company_id)`（`tools.py:21`） | 与 HTTP 请求解耦，PAT 解析出这两个字段即可直接复用全部工具 |
| 鉴权模式 | `deps/auth.py` | `_parse_identity` + `_load_user_flags`（is_disabled 拦截、降级告警）可直接套用到 PAT 校验 |
| 审批流 | `agent/actions.py` + `agent_actions` 表 | 写入类工具的落点：propose_* 系列已实现「产生 pending 动作 → 产品内确认」 |
| 审计 | `services/accounting_core_service.record_audit` | MCP 调用留痕可复用或平行建表 |
| 部署 | `deploy/docker-compose.yml`（uvicorn + alembic） | MCP 挂在同一个 FastAPI 进程内，零新增运维对象 |

工具清单（`agent/tools.py`，全部按 company_id 隔离）：
- **只读 13 个**：`get_month_summary`、`list_entries`、`list_invoice_entries`、`list_bank_statements`、`get_aging`、`list_todos`、`search_books`、`get_company_profile`、`get_entry_detail`、`run_close_checks`、`list_unmatched`、`get_filing_deadlines`、`generate_close_report` / `generate_filing_package`
- **写入（落审批流）5 个**：`propose_match`、`propose_accrual`、`propose_voucher(s)`、`preview_accruals`、`update_company_profile`

## 三、总体设计

### 3.1 传输与进程模型

**Streamable HTTP，挂载在现有 FastAPI 应用内**（`main.py`）：

```
uvicorn (8001)
└── FastAPI app
    ├── /api/**            业务路由（现状不动）
    └── /mcp               MCP Streamable HTTP 端点（新增挂载）
        └── ASGI 中间件：PAT 校验 → AuthContext 注入
```

- 选型理由：复用现有 uvicorn、DB 连接池、日志与部署链路，运维零新增；MCP 规范原生支持 Streamable HTTP 上的 `Authorization` 头。
- SDK：`fastmcp`（锁版本，如 `fastmcp==2.*`），提供 `mcp.streamable_http_app()` ASGI 子应用，`app.mount("/mcp", ...)` 即完成接入。Python 3.10（Docker 基线）满足其要求。
- 本地开发另提供 stdio 入口脚本（`backend/scripts/mcp_stdio.py`），连同一个 SQLite 测试库，供 ZCode/Claude Code 单进程调试，M0 顺带交付。

### 3.2 认证：个人访问令牌（PAT）

不复用 JWT（7 天过期，MCP 客户端需要长期凭证）；新增 PAT：

- 新表 `mcp_tokens`（alembic 迁移）：

```sql
id UUID PK,
user_id VARCHAR NOT NULL,
company_id VARCHAR NOT NULL,     -- token 绑定单一公司作用域
name VARCHAR NOT NULL,           -- 用户起名，如 "Claude Desktop"
token_hash VARCHAR(64) NOT NULL, -- sha256(token)，明文只在创建时返回一次
scopes VARCHAR NOT NULL,         -- 逗号分隔: read / write:draft
last_used_at TEXT, expires_at TEXT, revoked_at TEXT,
created_at TEXT NOT NULL
```

- 令牌格式 `sbk_<token_urlsafe(32)>`，校验时 sha256 后查表（防库泄露反推），顺路校验 `is_disabled`（复用 `_load_user_flags` 模式）与 `revoked_at`。
- 请求头 `Authorization: Bearer sbk_...`；MCP 端点**不受** `AUTH_DEV_FALLBACK` 兜底（该开关只放行业务联调 token，PAT 校验独立实现）。
- token 管理 API：`GET/POST/DELETE /api/mcp/tokens`（挂 `require_auth`，删除=置 revoked_at）；M2 在「我的」页加管理界面，M0 先用脚本 + curl。

### 3.3 工具注册与鉴权边界

- 写一个 **OpenAI schema → MCP tool schema 转换器**（`agent/mcp_server.py`）：遍历 `TOOLS`，字段几乎同构（`parameters` 直接就是 JSON Schema），约 30 行代码完成 20 个工具注册，handler 统一转调 `execute_tool(name, args, ctx)`。
- **scope 门禁**：`read` 仅放行只读工具；`write:draft` 额外放行 propose_* 系列（`update_company_profile` 已于同日下架，见 §8.5）。默认创建的 token 只给 `read`。
- **不暴露**：用户/公司管理、管理后台、密码与 token 管理本身、关账执行（`close` 动作需 step-up 级别确认，MCP 无此通道）、删除类操作。

### 3.4 审计与限流

- 新表 `mcp_call_logs`（M1）：token_id、tool 名、参数摘要、耗时、成功/失败、created_at；管理后台 M3 可加查看页。M0 先用 uvicorn 日志过渡。
- 限流（M1）：进程内令牌桶按 token 限流（如 60 次/分钟），超限返回 MCP error。

## 四、分期计划

### M0 — 只读工具上线（约 1.5~2 天）
1. 依赖：`requirements.txt` 加 `fastmcp`（锁版本）。
2. 迁移：`mcp_tokens` 表；`services/mcp_token_service.py`（签发/校验/吊销）。
3. `agent/mcp_server.py`：FastMCP 实例 + schema 转换器 + 13 个只读工具 + PAT 校验中间件；`main.py` 挂载 `/mcp`。
4. `scripts/mcp_stdio.py` 本地 stdio 入口（读 `MCP_STDIO_TOKEN` 环境变量）。
5. `scripts/make_mcp_token.py`：为指定 user/company 签发首个 token。
6. 测试：PAT 过期/吊销/禁用用户 401；工具结果与内部 agent 调用一致（pytest 直连 execute_tool 对照）。

### M1 — 写入工具 + 运营配套（约 1~2 天）
1. 放开 5 个写入工具（scope `write:draft`），确认落点全部是 pending `agent_actions`，产品内审批后执行。
2. `mcp_call_logs` + 限流；token CRUD API 联调。
3. 「我的」页新增「AI 连接（MCP）」入口：token 列表/新建（明文只显示一次）/吊销/最近使用时间。

### M2 — 体验增强（可选）
1. MCP Resources：`solobooks://entries/{id}`、`solobooks://reports/{month}` 等资源 URI，让客户端可订阅式读取。
2. MCP Prompts：预置「月度结账引导」「对账核对」提示词模板。
3. `nginx` 配置确认（`text/event-stream` 长连接、读超时放宽）；对外发布接入文档（官网 landing 加「用你的 AI 助手记账」板块的素材）。

## 五、客户端接入示例（写进 README/官网文档）

```jsonc
// Claude Desktop / ZCode mcp 配置（远程，M0 后可用）
{
  "mcpServers": {
    "solobooks": {
      "url": "https://<your-domain>/mcp",
      "headers": { "Authorization": "Bearer sbk_xxxx" }
    }
  }
}
// 本地 stdio（开发调试）
{ "mcpServers": { "solobooks": { "command": "backend/.venv313/bin/python", "args": ["scripts/mcp_stdio.py"] } } }
```

接入后即可："查一下 9 月没对平的流水"、"生成 9 月结账检查报告"、"起草一笔：9 月 30 日支付办公室房租 6,000 元"（后者产生待确认草稿，回产品内点确认）。

## 六、风险与注意事项

1. **fastmcp 迭代快**：锁死小版本；升级走独立 PR，`streamable_http_app()` 挂载方式写集成测试兜住。
2. **token 泄露 = 账本只读权**：默认 scope 收窄、last_used_at 可见、一键吊销；文档明示不要把 sbk_ 提交给 LLM 写入对话。
3. **AUTH_DEV_FALLBACK 联动**：MCP 中间件不走兜底逻辑；生产部署检查清单（管理后台方案中已列）同样覆盖 `/mcp`。
4. **工具输出体积**：`_truncate` 已兜底，但 `list_entries` 类工具在 MCP 场景建议默认 limit ≤ 50，防止撑爆客户端上下文。
5. **并发与事务**：工具复用现有 service 层（ORM session_scope / get_conn 兼容层），与 HTTP 路径同源，无新事务语义；唯一注意 streamable HTTP 是长连接，工具内不要持有请求级单例。
6. **多公司用户**：一个 token 绑一个公司；多公司需生成多个 token（M1 界面上说明），避免做公司切换的复杂协议。

## 七、验收标准（M0 完成时）

- Claude Desktop / ZCode 按 §5 配置后，能列出 13 个 solobooks 工具并成功调用 `get_month_summary`、`run_close_checks`。
- 无 token / 过期 / 吊销 / 禁用用户四种情况调用 `/mcp` 均被 401 拒绝；`read` scope token 调写入工具被拒绝并提示所需 scope。
- 同一工具经 MCP 与经内部 agent 调用返回一致结果（对照测试通过）。
- 生产配置下 `/mcp` 不受开发兜底 token 影响。

## 八、落地实现详情（M0+M1+M2 已于 2026-10-12 全量交付）

> 本章记录**实际实现**——与§四计划的偏差已在各节标注。代码基准：main @ MCP 三期提交。

### 8.1 文件清单

| 文件 | 职责 |
|---|---|
| `backend/agent/mcp_server.py` | MCP Server 核心：FastMCP 实例、工具注册、PAT 中间件、资源模板、提示词、限流、留痕 |
| `backend/services/mcp_token_service.py` | PAT 签发/校验/列表/吊销；scope 常量单一出处（`SCOPE_READ`/`SCOPE_WRITE`） |
| `backend/routers/mcp_tokens.py` | 令牌 CRUD API（`/api/mcp/tokens`） |
| `backend/db/models.py` | `MCPAccessToken`（mcp_tokens 表）、`MCPCallLog`（mcp_call_logs 表） |
| `backend/alembic/versions/d5e7f9a1c3b5_mcp_tokens.py` | M0 迁移 |
| `backend/alembic/versions/f7a9c1e3d5b7_mcp_call_logs.py` | M1 迁移 |
| `backend/scripts/make_mcp_token.py` | 运维脚本：签发（--list 列表 / --revoke 吊销） |
| `backend/scripts/mcp_stdio.py` | stdio 本地调试入口（读 `MCP_STDIO_TOKEN`） |
| `backend/tests/test_mcp_server.py` | M0 测试（PAT 服务/注册/HTTP 鉴权/执行一致性）+ 共享辅助函数 |
| `backend/tests/test_mcp_m1.py` | M1 测试（scope 门禁/审批流落点/留痕/限流/CRUD API/HTTP 写入） |
| `backend/tests/test_mcp_m2.py` | M2 测试（资源模板/提示词） |
| `web/src/api/mcpTokens.ts` | 令牌 API 封装 |
| `web/src/pages/mine/mcp-tokens.vue` | 「AI 连接（MCP）」管理页 |
| `web/src/router/index.ts` | `/mine/mcp` 路由 |
| `web/src/pages/mine/index.vue` | 我的页菜单入口 |
| `deploy/nginx/default.conf` | `/mcp` 位置块（SSE 支持） |
| `docs/mcp-integration.md` | 对外接入指南 |

### 8.2 MCP 端点行为

- **路径**：`/mcp`（FastAPI 根路径挂载 `mount("/", mcp_asgi_app)`，业务路由先匹配，未命中 `/api/*` 的请求才落到 MCP 应用）。**挂载语句必须位于 `main.py` 全部业务路由（含 `/api/health`）之后**，否则后注册的路由被 MCP 应用截胡——M0 时实测踩坑。
- **传输**：Streamable HTTP，`stateless_http=True` + `json_response=True`（无 SSE 会话保持，适配无状态部署与 curl 调试）。
- **鉴权错误**：统一 JSON-RPC 错误体——401 `{"code": -32001}`（缺头/令牌无效）、429 `{"code": -32002}`（限流命中）。两类失败均记 warning 日志：来源 IP（优先 X-Forwarded-For 首段）+ token 指纹（sha256 前 12 位，即 `token_hash` 前缀，可对库排查），绝不落明文。
- **lifespan 接管**：FastAPI 不会运行挂载子应用的 lifespan，`main.py` 中 `async with mcp_app.lifespan(_app): yield` 显式接管（注意传 `_app` 参数，`lifespan` 是方法引用不是上下文管理器）。

### 8.3 fastmcp 4.1 的实际用法（与常见教程不同）

```python
from fastmcp import FastMCP
from fastmcp.tools import FunctionTool          # 不是 @mcp.tool 装饰器（动态注册场景）
from fastmcp.resources.template import FunctionResourceTemplate  # 注意在 .template 子模块
from fastmcp.prompts.function_prompt import FunctionPrompt

# 工具：parameters 直接喂 OpenAI function schema 的 JSON Schema
FunctionTool(name=..., description=..., parameters=<json schema>, fn=<async handler>)

# 资源模板：基础类 ResourceTemplate 不收 fn，必须用 FunctionResourceTemplate
FunctionResourceTemplate(uri_template="solobooks://reports/{month}", parameters={...}, mime_type="application/json", fn=<async reader>)

# 提示词：同理用 FunctionPrompt
FunctionPrompt(name=..., arguments=[PromptArgument(name, description, required)], fn=<str 返回函数>)
```

读取 API：`mcp.list_tools()` / `mcp._list_resource_templates()`（返回 list）/ `mcp.list_prompts()` / `mcp.read_resource(uri)`（返回 `ResourceResult`，文本在 `.content[0].text`）。

### 8.4 鉴权实现：PAT + contextvar

- **contextvar 承载身份**（`_mcp_identity: ContextVar[PatIdentity | None]`）：HTTP 路径由 `PatAuthMiddleware`（纯 ASGI，包在 mcp_app 外层）每请求校验一次并 `set`，工具执行在中间件同任务链内自然继承；stdio 由 `scripts/mcp_stdio.py` 启动时注入——**必须在 `asyncio.run` 之外、线程当前上下文里 set**，否则 server 新事件循环看不到（M0 实测踩坑）。
- **校验流程**（`mcp_token_service.validate_token`）：Bearer 前缀归一化 → sha256 查 `mcp_tokens.token_hash` → 检查 `revoked_at` / `expires_at`（SQLite 读回 naive datetime 按 UTC 解释）→ 查 `auth_users.is_disabled`（用户行不存在直接拒绝，**不享受** `require_auth` 的兜底放行）→ 命中则更新 `last_used_at`。
- **与业务鉴权完全隔离**：`AUTH_DEV_FALLBACK` 开发兜底 token（`test-token` 前缀、非 JWT 垃圾）对 `/mcp` 一律 401。
- **PatIdentity**：`context: AuthContext(user_id, company_id)` + `token_id` + `scopes: frozenset`；`has_scope()` 供工具门禁。

### 8.5 scope 门禁矩阵

每个工具经 `_required_scope(name)` 归类，handler 入口校验，缺权限抛 `PermissionError("令牌缺少 write:draft 权限…")`。门禁在执行前：不写 `mcp_call_logs`、无任何副作用，但记 warning 日志（token_id + 工具名）——越权尝试是安全信号，须可追溯（2026-10-12 补）：

| scope | 工具 |
|---|---|
| `read`（15 个） | get_month_summary、list_entries、list_invoice_entries、list_bank_statements、get_aging、list_todos、search_books、get_company_profile、get_entry_detail、run_close_checks、list_unmatched、get_filing_deadlines、generate_close_report、generate_filing_package、preview_accruals* |
| `write:draft`（4 个） | propose_match、propose_accrual、propose_voucher、propose_vouchers |

\* `preview_accruals` 计划时归写入，实现确认为纯预览无副作用，改归 read（已核代码：只读 `fixed_assets` 与 `salary_sheets`）。

**\*\* `update_company_profile` 已于 2026-10-12 从 MCP 下架**（原在 write:draft 名单）。原因：画像（industry/taxpayerType/notes/categoryPreferences）是产品内 AI 建议回路的长期记忆——消费方为分类器提示（`/api/record/text` top-8 习惯注入）与 agent system prompt（`agent/loop.py` 全字段注入），其中 `notes` 为逐字注入的自由文本。经核查，投毒不会直接造成错误入账（分类结果仍需前端确认）也不影响申报税额（filing 服务不读画像），但存在橡皮图章式确认下的慢性偏移与持久提示注入两个真实风险；且画像对用户完全不可见（无查看 UI）。结论：画像写入是产品内特权（登录态 + 未来可见性建设），MCP 只保留 `get_company_profile` 读取。工具仍在内部 `TOOLS` 中，产品内 agent 行为不变。

写入类落点（M1 已验证）：`propose_*` 全部走 `agent/actions.create_action` → `agent_actions` 表 pending 状态 → 产品「待确认」池（`GET /api/agent/actions?status=pending` 可见）→ 人工确认后走既有确定性入账管线。

### 8.6 限流与留痕

- **限流**：进程内滑动窗口（`collections.deque` per token_id），默认 60 次/分钟。环境变量：`MCP_RATE_LIMIT`（=0 关闭）、`MCP_RATE_LIMIT_PER_MINUTE`。多实例部署需换集中存储（Redis）。
- **留痕**：`mcp_call_logs` 表——token_id、user_id、company_id、tool、arguments_digest（JSON 序列化截断 2000 字符）、duration_ms、success、error（截断 500 字符）。**留痕失败只记日志不影响调用**（工具执行先、留痕 try/except 包裹）。资源读取也留痕，tool 字段记为 `resource:<uri_template>`。`success` 口径：异常与业务错误都算失败——工具层以返回值 `{"error": msg}` 表达的业务错误（不抛异常）在 MCP 侧统一转 `ToolError`，客户端收到 isError 结果、留痕记 `success=false`（资源模板同口径）。

### 8.7 令牌管理与前端界面

**数据模型**（`mcp_tokens`）：id(UUID)、user_id、company_id（绑定单一公司）、name、token_hash（sha256，unique）、scopes（逗号分隔）、last_used_at、expires_at、revoked_at、created_at。

**API**（均需 `require_auth` JWT）：

| 方法 | 路径 | 行为 |
|---|---|---|
| GET | `/api/mcp/tokens` | 本人令牌列表（不含 hash） |
| POST | `/api/mcp/tokens` | 签发；body `{name, scopes, company_id?, expires_days?}`；scope 白名单校验（未知 scope 400、write:draft 无 read 400）、跨公司需 `company_members` **有效**成员关系（`status=active`，与 `ensure_tenant` 访问门禁同口径，2026-10-12 修正；403）；响应含明文 token（仅此一次） |
| DELETE | `/api/mcp/tokens/{id}` | 吊销（仅本人；重复/非本人 404） |

**前端页面** `/mine/mcp`：令牌表格（权限标签「只读/读写草稿」、状态「有效/已吊销/已过期」、最近使用时间）、新建弹窗（名称 + 权限复选 + 公司选择 + 有效期天数）、明文令牌一次性弹窗（警告条 + 复制按钮）、吊销二次确认、客户端配置示例（自动带当前站点 origin）。

### 8.8 运维操作手册

```bash
cd backend
# 本地 SQLite 测试模式签发（生产改 DATABASE_URL 指向 PG）
DATABASE_URL="sqlite+aiosqlite:///data/app.db" \
  ./.venv313/bin/python scripts/make_mcp_token.py \
  --user <user_id> --company <company_id> --name "Claude Desktop" \
  --scopes read,write:draft --expires-days 365

./scripts/make_mcp_token.py --list                 # 列表
./scripts/make_mcp_token.py --revoke <token_id>    # 吊销
```

注意：本地库若 `auth_users` 无该用户行需先补（PAT 校验拒绝无库记录用户）；缺表跑 `scripts/sync_sqlite_schema.py`。

### 8.9 依赖与部署清单

- **依赖锁定**（2026-10-12 修正）：fastmcp-slim 4.1 的 client/server extras **硬性要求** starlette>=1.0.1、uvicorn>=0.35、pydantic>=2.12、python-multipart>=0.0.26、python-dotenv>=1.1.0——与旧栈（fastapi 0.115.6 / starlette 0.41.3 / pydantic 2.10.3 / uvicorn 0.34.0）互斥，全新 `pip install -r` 必然 ResolutionImpossible（本文档曾记录的"钉住 starlette 0.41.3"方案实际不可安装，勿回滚）。现行钉版：`fastapi==0.143.0` / `starlette==1.7.0` / `pydantic==2.14.1` / `uvicorn[standard]==0.54.0` / `fastmcp==4.1.0`，四者须联动升级；全量测试在新栈通过，依赖链 Requires-Python 均 >=3.10（Docker python:3.10 兼容）。
- **nginx**：`/mcp` 位置块——`proxy_buffering off` + `proxy_cache off`（SSE 即时到达）、`proxy_read_timeout 600s`（长会话）、HTTP/1.1 + 清空 `Connection` 头。
- **生产检查**：`AUTH_DEV_FALLBACK=0`；迁移自动执行（compose 启动 `alembic upgrade head`）。

### 8.10 测试与验证记录

**41 个自动化测试**（全量 269 个中）：
- M0 `test_mcp_server.py`（19）：PAT 往返/未知/吊销/过期/禁用/无用户行/Bearer 归一化；工具注册全集与 schema 一致 + 画像写入不开放（19 = 20 - update_company_profile）；execute_tool 对照一致性；**mount 顺序回归**（MCP mount 必须是最后一个路由，防后注册路由被截胡）；HTTP 401 三分支（含开发兜底 token 被拒）+ 认证失败留痕（IP+指纹、不落明文）；initialize 200 全链路。
- M1 `test_mcp_m1.py`（14）：read 令牌调写入工具被拒（断言越权 warning 留痕、无副作用、无 mcp_call_logs 行）；write 令牌起草产生 pending action（company/user/payload 断言）；失败调用留痕（monkeypatch 注入异常）；业务错误 `{"error"}` 转 ToolError 且留痕记失败；限流滑动窗口与关闭开关；CRUD 生命周期/非法 scope/write 缺 read/跨公司 403/**被停用成员签发 403（`status=active` 口径）**/未登录 401；HTTP 全链路 `tools/call` 写入。
- M2 `test_mcp_m2.py`（8）：资源模板注册全集；真实读取（reports/entries）；无身份抛错；资源留痕；提示词注册与月份渲染断言。

**端到端验证**（真实运行服务，fastmcp Client）：19 工具/2 提示词/4 资源模板全部可见；`get_month_summary`、`run_close_checks`、`get_filing_deadlines`、`propose_voucher`（→ pending → 产品待确认池可见）真实调用通过；吊销后同令牌立即 401；stdio 入口经客户端验证可用。

### 8.11 已知边界与后续方向

- 多公司用户需每公司一个令牌（UI 已说明）；未做公司切换协议。
- 限流为进程内实现，多实例部署需换 Redis。
- `mcp_call_logs` 无清理机制，量大后可在管理后台 M3 加按时间清理。
- 未做（原计划外，暂无需求）：OAuth 授权流、每令牌细粒度工具白名单、订阅式资源变更推送。
