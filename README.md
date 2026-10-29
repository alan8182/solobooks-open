# 梭罗记账 SoloBooks · 面向一人公司与小微企业的 AI 智能记账报税系统

<p align="center">
  <img src="brand/solo-lockup.png" alt="梭罗记账 SoloBooks" width="420" /><br/>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-SoloBooks_v2.0-blue.svg" alt="SoloBooks License v2.0" /></a>
  <a href="#技术栈"><img src="https://img.shields.io/badge/Python-3.13-brightgreen.svg" alt="Python 3.13" /></a>
  <a href="#技术栈"><img src="https://img.shields.io/badge/Vue-3.x-42b883.svg" alt="Vue 3" /></a>
  <a href="MCP接入方案.md"><img src="https://img.shields.io/badge/MCP-Supported-purple.svg" alt="MCP Supported" /></a>
</p>

梭罗记账（SoloBooks）是一套面向**一人公司（OPC）与小微企业**的 AI 智能记账与自助报税系统。以「智能记账 + 规范对账 + 结账检查 + 自助报税」为完整闭环，帮助创业者把日常收支、全电发票、银行与微信流水沉淀为标准会计账簿，并通过 AI 报税副驾与电子税务局申报作业包，让经营者零财务门槛也能自己轻松完成记账与报税。

> **为什么做梭罗记账？**
> 梭罗一个人在瓦尔登湖畔独立生活、劳作与记录——一个人的公司，更值得一套清晰透明、自主可控的账税体系。
>
> 过去，小微企业找外部代账机构，往往只是因为“报税门槛高、怕填错表”；但传统代账一年收取数千元，绝大多数只做机械的流水记录，既不帮你看懂经营数据，遇到税收政策变化也难以深度跟进。
>
> **在 AI 时代，记账报税完全可以由创业者自己掌控。**
> 随着数电发票与新版电子税务局的全面推行，记账与报税的规则正在变得高度结构化。借助大模型与确定性复式记账引擎，AI 能够自动完成票据分类、凭证归集、对账平账，并一键生成电子税务局小抄与申报底稿。梭罗记账的初衷，正是把**“记账的主动权”**与**“报税的自由度”**真正交还给每一位超级个体与小微经营者——不用受制于外部代账中介，自己也能轻松管好账、独立报好税！

> 📌 **授权说明**：本项目采用**源码可用许可**（[SoloBooks License v2.0](LICENSE)）：个人、个体工商户、一人公司与小微企业可**永久免费自托管**用于本企业内部记账与报税（含产生经营收入的日常记账）；未经书面授权，**不得**对外提供 SaaS 托管、付费云平台或第三方商业代账服务。商业授权（Partner License）与合作请联系：**founder@jiezixingchen.com**。

---

## 功能特性

- **老板 / 会计双视图**：顶部一键切换经营视角与专业视角，同一套账、同一个 URL；视图只改变导航与信息密度，不改变权限
- **智能记账**：AI 识别文字/语音/单据 → 自动判断收支方向、金额与费用分类，生成记账凭证草稿；AI 只起草，入账必须人工确认
- **自助报税与申报小抄**：增值税、企业所得税预缴/汇算、经营所得个税（A 表预缴 / B 表汇算 / C 表提示，仅查账征收）测算；一键生成电子税务局申报数据包，自主申报不求人
- **发票全流程**：全电发票 Excel 导入（标准列名自动映射）、AI 智能分类、发票台账、明细查验与进销项核对
- **流水智能认领**：银行流水 / 微信账单导入后，以流水为客观事实认领到发票或凭证；月结第 4 步统一「认领 + 配平」，历史遗留流水单独列出
- **微信账单月度汇总**：微信支付账单支持逐笔导入或按月度汇总生成 1 张综合凭证
- **月度结账向导**：五步向导 + 五项结账检查 + 跨期补记/暂估闭环，未配平不允许关账
- **标准财税报表**：资产负债表、利润表、现金流量表、所有者权益变动表、三表季报、试算平衡、科目余额表、总账/明细账/序时账/多栏账、往来账龄与税务申报底稿导出
- **数量金额式核算**：科目级开关、凭证数量录入、余额表数量结存、旧账数量透传
- **固定资产 / 无形资产**：卡片、自动折旧/摊销、处置与总账核对
- **薪酬管理**：工资表导入、社保公积金计提、工资四凭证、研发工时分配
- **通用归集与分摊**：辅助核算维度、归集规则、待归集清单、归集报表与报表口径对比
- **老板私账**：个人收支记录（收入/支出/垫付）与公司账目联动
- **账套迁移与归档**：旧账导入、期初建账、电子账簿打印、数据备份归档
- **多账本 / 多主体**：支持多公司账本切换，owner / accountant / preparer / viewer 角色权限

## 技术栈

| 层 | 技术 |
|---|---|
| 后端 | Python 3.13 · FastAPI · SQLAlchemy(async) · Alembic |
| 数据库 | PostgreSQL 16 |
| 前端 Web | Vue 3 · TypeScript · Vite · Naive UI · Pinia · Tailwind |
| 小程序 | uni-app（**默认不公开，另行处理**） |
| 部署 | Docker Compose · Nginx |

## 目录结构

```
.
├── backend/            # FastAPI 后端
│   ├── routers/        # API 路由（记账/账簿/报表/发票/银行/资产/税务/MCP…）
│   ├── services/       # 业务服务（记账/分类/对账/折旧/税表/归集…）
│   ├── models/         # Pydantic 模型
│   ├── db/             # SQLAlchemy 引擎/会话/模型
│   ├── core/           # 金额、错误等基础设施
│   ├── alembic/        # 数据库迁移
│   └── tests/          # 后端测试
├── web/                # Vue 前端
│   ├── src/
│   │   ├── pages/      # 页面（工作台/AI 会计/账本/报表/结账/归集…）
│   │   ├── components/ # 组件
│   │   ├── api/        # API 客户端
│   │   ├── stores/     # Pinia 状态
│   │   └── utils/      # 工具函数
│   ├── docs/           # 前端设计规范与页面交互文档
│   └── vite.config.ts  # 含 dev 代理
├── deploy/             # Docker/生产部署配置
├── brand/              # 品牌素材（logo、favicon）
└── docs/               # 开发与运维文档
```

## 文档索引

| 文档 | 说明 |
|---|---|
| [web/docs/pages-and-interactions.md](web/docs/pages-and-interactions.md) | 前端页面、路由与交互逻辑总览 |
| [docs/development-workflow.md](docs/development-workflow.md) | 本地热更新、Docker 构建与数据库变更流程 |
| [docs/finance-core-runbook.md](docs/finance-core-runbook.md) | 财务核心运维与排障手册 |
| [MCP接入方案.md](MCP接入方案.md) / [docs/mcp-integration.md](docs/mcp-integration.md) | 外部 AI 接入设计与使用说明；在线版图文指南见 [AI 连接中心](https://solobooks.cn/ai-connect)（源码 `web/src/pages/mcp-guide/`） |

> 产品需求文档（`梭罗记账_PRD_v4.md`）与视觉设计规范（`DESIGN.md`、`DESIGN_SYSTEM.md`）为内部文档，仅在私有开发仓库中维护，不随本开源库发布。

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

浏览器打开 `http://localhost:5173`，注册后会自动生成一本演示账套。本地调试仍可用测试账号（手机号 `13800000000` / 验证码 `123456`），官网与登录页不再单独展示该入口。

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
cp .env.staging.example .env.staging.local   # 填入真实密钥后重命名为 .env.staging.local
docker compose up -d
```

后端启动时会自动执行 `alembic upgrade head`。

## 贡献

欢迎通过 Issue 报告问题、提出建议或提交 PR。请阅读 [CONTRIBUTING.md](CONTRIBUTING.md) 了解贡献规范。

## 许可证与商业授权

本项目核心开源底座采用 [SoloBooks 源码可用许可 v2.0](LICENSE)：
- **个人与企业自用**：个人、个体工商户、一人公司与小微企业可**永久免费自托管**用于本企业内部记账（包括产生经营收入的真实记账场景），享有阅读源码、自用修改与部署运行的完整自由。
- **商业使用限制**：未经芥子星辰（杭州）智能科技有限公司书面授权，**禁止**将本软件用于对外 SaaS 托管经营、按用户/多租户收费、或持牌代账机构向客户提供商业记账/报税服务。
- **商业授权（Partner License）**：如需多租户云端连接器、代账批量管户平台或商业联合运营授权，请联系商务邮箱：**founder@jiezixingchen.com**。

## 免责声明

本项目不构成任何税务、法律或财务建议。涉及税务申报等专业事项，请咨询持牌专业人士。
