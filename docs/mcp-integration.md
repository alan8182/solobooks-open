# 梭罗记账 MCP 接入指南

把「梭罗记账」接入你的 AI 助手（Claude Desktop、ZCode、Claude Code 等），让 AI 直接帮你查账、核对流水、起草凭证。

> **安全底线**：AI 只能起草，不能入账。所有 `propose_*` 类工具产生的凭证草稿、对账建议，都需要你在梭罗记账产品内的「待确认」列表中人工确认后才会生效。关账操作只能在产品内执行。

---

## 一、获取访问令牌

1. 登录梭罗记账网页版，进入 **我的 → AI 连接（MCP）**；
2. 点击 **新建令牌**，填写名称（如"我的 Claude Desktop"）、选择权限、有效期；
3. **立即复制弹窗中显示的令牌**（`sbk_` 开头）——明文只显示这一次，关掉就找不回了。

权限说明：

| 权限 | 能做什么 |
|---|---|
| 只读 | 查询账目、流水、对账差异、结账检查、征期提醒 |
| 读写草稿 | 在只读基础上，起草凭证草稿与对账建议（仍需产品内确认） |

令牌绑定单一公司。有多家公司时，为每家公司各签发一个令牌。令牌可随时在 same 页面吊销，吊销立即生效。

## 二、客户端配置

### Claude Desktop / 任意支持 Streamable HTTP 的客户端

```jsonc
// claude_desktop_config.json
{
  "mcpServers": {
    "solobooks": {
      "url": "https://<你的梭罗记账域名>/mcp",
      "headers": { "Authorization": "Bearer sbk_你的令牌" }
    }
  }
}
```

### Cursor（本地与远程）

在项目根目录 `.cursor/mcp.json` 中配置：

```jsonc
{
  "mcpServers": {
    "solobooks": {
      "url": "https://<你的梭罗记账域名>/mcp",
      "headers": { "Authorization": "Bearer sbk_你的令牌" }
    }
  }
}
```

### Claude Code / CLI（本地 stdio 模式）

适合本地部署或开发者工作流，通过一条命令即可快速注册：

```bash
# Windows（Git Bash）：backend/venv/Scripts/python.exe；macOS/Linux：backend/venv/bin/python
claude mcp add solobooks -s user -- \
  env MCP_STDIO_TOKEN="sbk_你的令牌" DATABASE_URL="sqlite+aiosqlite:///data/app.db" \
  <backend目录>/venv/Scripts/python.exe <backend目录>/scripts/mcp_stdio.py
```

或者手动编辑 `~/.claude/claude_desktop_config.json`：

```jsonc
{
  "mcpServers": {
    "solobooks": {
      "command": "<backend目录>/venv/Scripts/python.exe",
      "args": ["<backend目录>/scripts/mcp_stdio.py"],
      "env": {
        "MCP_STDIO_TOKEN": "sbk_你的令牌",
        "DATABASE_URL": "sqlite+aiosqlite:///data/app.db"
      }
    }
  }
}
```

### 直接通过 API 使用

MCP 端点遵循 Streamable HTTP 规范（JSON-RPC over POST）：

```bash
curl -X POST https://<域名>/mcp \
  -H "Authorization: Bearer sbk_你的令牌" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

## 三、能力清单

**查询（19 个）**：`get_month_summary`（月度汇总）、`list_entries`（账目列表）、`list_invoice_entries`（发票）、`list_bank_statements`（流水）、`list_fixed_assets`（固定资产台账：原值/折旧/净值）、`list_intangible_assets`（无形资产台账：原值/摊销/净值/权利到期）、`get_advance_balances`（股东/员工垫付与报销台账）、`get_aging`（应收/应付账龄）、`list_todos`（待办）、`search_books`（全文搜索）、`get_entry_detail`、`get_company_profile`（公司画像/AI 记忆，只读）、`run_close_checks`（结账五项检查）、`list_unmatched`（未对账流水 + 建议）、`preview_accruals`、`preview_asset_actions`（计提预览）、`get_filing_deadlines`（征期）、`generate_close_report` / `generate_filing_package`（报告/申报作业包）。

**起草（5 个，需读写草稿权限，产出均为待确认草稿，在梭罗记账产品内确认后才入账）**：`propose_voucher` / `propose_vouchers`（记账草稿）、`propose_match`（对账建议）、`propose_accrual、propose_asset_closure`（折旧/工资计提草稿）。公司画像（AI 长期记忆）对 MCP 只读：分类习惯等记忆的写入只能在产品内完成。

**为 agent 效率与系统安全做的核心设计（2026-10-12）**：

- **单一载荷**：工具返回的 dict 结果只经 content 一路下发紧凑 JSON（`agent/mcp_server.py` 收敛 FastMCP 默认的 content+structuredContent 双发，省一半上下文 token）；列表类工具带 `total`（过滤后总命中）+ `count`（本次返回数）便于分页判断。
- **参数 Schema 原生正则纠偏**：所有 `month`（`^\d{4}-(0[1-9]|1[0-2])$`）、`occur_date`、`period` 参数均内置正则表达式约束，在客户端发出前即拦截格式幻觉；`propose_voucher` 的 `category` 提示核心会计分类并引导读取 `company_profile`。
- **stdio 运行时吊销阻断**：stdio 模式长驻进程在工具调用时具备 30s 运行时状态复核，后台吊销令牌或账号被停用后能够及时阻断，并实时刷新 `last_used_at`。
- **重型分析防雪崩短缓存**：`run_close_checks`、`generate_filing_package` 等重度计算内置 15s 内存短缓存，防止外部 Agent 连续多步循环调用打垮数据库。
- **列表过滤与健壮错误反馈**：`list_todos` 支持 `type`/`level`/`limit`，`list_bank_statements` 支持 `matched`；`list_unmatched` 的 AI 建议生成失败时带 `aiSuggestionsError` 显式说明，与「无可匹配」区分。

**资源模板（4 个）**：`solobooks://entries/{entry_id}`、`solobooks://reports/{month}`、`solobooks://close-checks/{month}`、`solobooks://company/profile`。

**提示词模板（2 个）**：`closing_guide`（月度结账引导）、`reconciliation_review`（对账核对）——在客户端里调用即可获得结构化的操作引导。

## 四、使用示例

配置好之后，你可以直接对 AI 说：

- 「9 月账记得怎么样了？能结账吗？」→ AI 跑结账检查并逐项解读
- 「查一下 9 月有哪些流水还没对上」→ 列出未匹配流水并给出建议
- 「记一笔：10 月 12 日收到网页设计项目款 8,000 元」→ AI 起草凭证草稿，你回产品内确认
- 「我们公司 UU 跑腿的订单平时记什么科目？」→ 读公司画像里的分类习惯回答（修改记忆请在产品内进行）
- 「我已在浏览器打开电子税务局申报表，帮我核对并预填本季度的增值税和企税」→ 配合浏览器自动化 MCP 与 [`skills/solobooks-tax-filing/SKILL.md`](../skills/solobooks-tax-filing/SKILL.md)，AI 先做账套阻塞体检、比对税局预填销售额、填入可编辑栏次并高亮标记，停在提交按钮前由你人工确认申报

## 五、安全与限制

- **令牌即账本只读权（+可选起草权）**：不要把令牌粘贴进任何对话内容；泄露立即在「AI 连接」页吊销。
- **限流**：每个令牌每分钟 60 次工具调用。
- **审计**：每次调用留痕（工具、时间、耗时、结果），可在后台追溯。
- **公司隔离**：AI 只能看到令牌绑定公司的数据。
- 被禁用的账号，其所有令牌立即失效。

## 六、自部署运维说明

- MCP 与业务 API 同进程（`/mcp` 路径），无需额外部署对象；
- nginx 已配置 `/mcp` 位置块（SSE 关闭缓冲、读超时 600s），见 `deploy/nginx/default.conf`；
- 生产部署务必设置 `AUTH_DEV_FALLBACK=0`——开发兜底 token 对 `/mcp` 本来就无效，但关闭兜底是整体安全基线。
