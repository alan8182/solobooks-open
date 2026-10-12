# 梭罗记账 SoloBooks · 小微企业智能记账

SoloBooks（梭罗记账）是一套面向**小微企业**的智能记账与财税管理系统。以「记录 + 智能 → 记账 + 对账」为核心，帮助个体经营者、小微公司把日常收支、发票、银行流水管理成规范的会计账目，并自动生成凭证与经营报表。

> **为什么做 SoloBooks？**
> 传统代记账机构往往只帮你「把账记下来」，停留在流水登记，并不会真正去分析、管理你的财务。SoloBooks 的初衷，是让 OPC（一人公司）和小微企业拥有一套**真正管好财务**的工具——不只是记录，而是对账、生成报表、做结账检查，让经营者自己也能看懂、掌控经营。

> ⚠️ **授权说明**：本项目采用自定义**非商业开源许可**（`LICENSE`），允许个人学习与研究用途，**禁止商业使用**。商业授权请联系作者。

---

## 功能特性

- **智能记账**：AI 识别文字/语音/单据 → 自动判断收支方向、金额与费用分类，生成记账凭证草稿
- **AI 智能副驾（Copilot）**：对话式记账与结账助手——批量起草凭证、多模态输入、经营画像记忆、结账预检与票流账差异分析
- **MCP 接入**：内置 MCP Server，Claude Desktop / ZCode 等 AI 客户端可经工具调用直接查账、记账、对账（见下文）
- **发票管理**：税务局全量发票 Excel 导入（标准列名自动映射）、AI 智能分类、发票明细查看、对账状态跟踪
- **银行/微信流水**：银行流水 CSV / 微信支付账单导入，自动识别收支方向，AI 辅助与账目对账匹配
- **结账向导**：月度结账流程——待办任务队列、发票/流水对账、工资计提、批量过账、结账检查与账册锁定
- **待办闭环**：发票催办、流水待处理、股东垫付确认等事项分组呈现，就地处理
- **申报作业包**（小规模纳税人）：征期日历、增值税/企业所得税申报表草稿、账↔票↔流水三方核对、官方模板 XLS 导出
- **财务报表**：利润表、资产负债表、现金流量表、往来账龄、增值税/所得税测算、税务申报导出
- **固定资产**：资产录入、自动折旧、凭证生成
- **薪酬管理**：工资表导入、社保公积金计提（费率/基数可配）、工资发放凭证
- **老板私账**：个人收支记录（收入/支出/垫付）与公司账目联动
- **多账本 / 多主体**：支持多公司账本切换

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.13 · FastAPI · SQLAlchemy(async) · Alembic |
| LLM | OpenAI 兼容协议（供应商无关）：通义千问 / DeepSeek / 智谱 GLM 仅换环境变量 |
| 数据库 | PostgreSQL 16（本地开发可用 SQLite） |
| 前端 Web | Vue 3 · TypeScript · Vite · Naive UI · Pinia · Tailwind |
| AI 连接 | MCP（fastmcp）：Streamable HTTP `/mcp` + stdio |
| 部署 | Docker Compose · Nginx |

## 目录结构

```
.
├── backend/            # FastAPI 后端
│   ├── routers/        # API 路由
│   ├── services/       # 业务服务（记账/分类/对账/报表/申报…）
│   ├── agent/          # AI 副驾工具循环 + MCP Server
│   ├── models/         # Pydantic 模型
│   ├── db/             # SQLAlchemy 引擎/会话/模型
│   ├── core/           # 金额、错误等基础设施
│   ├── assets/         # 税务报表 XLS 官方模板
│   ├── alembic/        # 数据库迁移
│   ├── scripts/        # MCP 令牌签发 / stdio 入口等工具
│   └── tests/          # 后端测试
├── web/                # Vue 前端
│   ├── src/
│   │   ├── pages/      # 页面（账本/结账向导/申报/智能副驾/落地页…）
│   │   ├── components/ # 组件
│   │   ├── api/        # API 客户端
│   │   ├── stores/     # Pinia 状态
│   │   └── utils/      # 工具函数
│   └── vite.config.ts  # 含 dev 代理
└── deploy/             # Docker/生产部署配置
```

## 快速开始（本地开发）

> 依赖：Python 3.13+、Node.js、Docker（用于本地 PostgreSQL）。

### 1. 启动 PostgreSQL

本地无 PostgreSQL 时，用 Docker 起一个：

```bash
docker run -d --name solobooks-pg \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -e POSTGRES_DB=solobooks \
  -p 5432:5432 \
  postgres:16-alpine
```

### 2. 配置后端环境变量

```bash
cp backend/.env.example backend/.env
```

编辑 `backend/.env`，至少配置：

```env
DASHSCOPE_API_KEY=sk-xxx                # 阿里云百炼（通义千问），必填
DATABASE_URL=postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/solobooks
```

所有文本/多模态 LLM 调用统一走 **OpenAI 兼容端点**，切换供应商只需改 `LLM_BASE_URL` / `LLM_API_KEY` / 模型名三项（详见 `.env.example` 注释），业务代码零改动。`DASHSCOPE_API_KEY` 未配置 `LLM_API_KEY` 时自动复用。

### 3. 初始化数据库表

```bash
cd backend
DATABASE_URL="postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/solobooks" \
  python -m alembic upgrade head
```

### 4. 启动后端

```bash
cd backend
python -m uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

验证：`curl http://localhost:8001/api/health` → `{"status":"ok","database":"postgresql"}`

### 5. 启动前端

```bash
cd web
npm install
npm run dev
```

> 前端 dev 代理指向后端 `http://localhost:8001`（见 `web/vite.config.ts`）。
> 如后端端口不同，可设环境变量 `VITE_DEV_API_TARGET=http://localhost:PORT`。

### 6. 登录

浏览器打开 `http://localhost:5173`，使用**测试账号一键登录**（手机号 `13800000000` / 验证码 `123456`）；也可在「我的」页设置密码后改用密码登录。

## MCP 接入（AI 客户端直连记账）

SoloBooks 内置 MCP Server，Claude Desktop、ZCode 等 AI 客户端可以工具形式直接查账、记账草稿、对账与生成申报作业包。

1. **签发个人访问令牌（PAT）**：

   ```bash
   cd backend
   python scripts/make_mcp_token.py --user <user_id> --company <company_id> \
     --name "Claude Desktop" --scopes read
   ```

   明文令牌（`sbk_…`）仅在签发时打印一次。`--list` / `--revoke` 可列出与吊销。

2. **接入 AI 客户端**（Streamable HTTP，PAT 鉴权）：

   ```json
   {
     "mcpServers": {
       "solobooks": {
         "url": "http://localhost:8001/mcp",
         "headers": { "Authorization": "Bearer sbk_..." }
       }
     }
   }
   ```

3. **本地 stdio 调试**：`MCP_STDIO_TOKEN=<sbk_…> python scripts/mcp_stdio.py`（单进程，启动时校验一次令牌）。

---

## 运行测试

```bash
# 后端
cd backend && pytest

# 前端
cd web && npm run test
```

## 部署（Docker Compose）

生产部署使用 `deploy/docker-compose.yml`，一键拉起 PostgreSQL + 后端 + 前端（Nginx）：

```bash
cd deploy
cp .env.staging.example .env.staging.local   # 填入真实密钥
docker compose --env-file .env.staging.local up -d
```

后端启动时会自动执行 `alembic upgrade head`。

## 贡献

欢迎通过 Issue 报告问题、提出建议或提交 PR。请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 了解贡献规范。

## 许可证

本项目采用 [SoloBooks 非商业开源许可](LICENSE)：**允许个人学习/非商业用途，禁止商用**。详见 `LICENSE`。

## 免责声明

本项目不构成任何税务、法律或财务建议。涉及税务申报等专业事项，请咨询持牌专业人士。
