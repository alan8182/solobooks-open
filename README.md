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

- **智能记账**：AI 识别文字/语音/单据 → 自动判断收支方向、金额与费用分类，生成记账凭证草稿
- **自助报税与申报小抄**：小微企业增值税、企业所得税季度/月度智能预缴测算；一键生成与新版电子税务局一比一对应的填报数据包（报税小抄），AI 辅助核对，自主申报不求人
- **发票全流程**：税务局全电发票 Excel 导入（标准列名自动映射）、AI 智能分类、明细查验与进销项核对
- **流水智能对账**：银行流水 CSV / 微信支付账单导入，自动识别收支方向，AI 辅助流水与凭证对账平账
- **月度结账向导**：五项结账检查、发票流水对账、工资计提、折旧摊销与账册锁定，保证账账相符
- **标准财税报表**：资产负债表、利润表、现金流量表、科目余额表、明细账、往来账龄与税务申报底稿导出
- **固定资产**：资产录入、自动折旧、凭证生成
- **薪酬管理**：工资表导入、社保公积金计提、工资发放凭证
- **老板私账**：个人收支记录（收入/支出/垫付）与公司账目联动
- **多账本 / 多主体**：支持多公司账本切换

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
│   ├── routers/        # API 路由
│   ├── services/       # 业务服务（记账/分类/对账/报表…）
│   ├── models/         # Pydantic 模型
│   ├── db/             # SQLAlchemy 引擎/会话/模型
│   ├── core/           # 金额、错误等基础设施
│   ├── alembic/        # 数据库迁移
│   └── tests/          # 后端测试
├── web/                # Vue 前端
│   ├── src/
│   │   ├── pages/      # 页面
│   │   ├── components/ # 组件
│   │   ├── api/        # API 客户端
│   │   ├── stores/     # Pinia 状态
│   │   └── utils/      # 工具函数
│   ├── docs/           # 前端设计规范与页面交互文档
│   └── vite.config.ts  # 含 dev 代理
├── deploy/             # Docker/生产部署配置
└── docs/               # 运维文档（默认不公开）
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

浏览器打开 `http://localhost:5173`，使用**测试账号一键登录**（手机号 `13800000000` / 验证码 `123456`）。

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
