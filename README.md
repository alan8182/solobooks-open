# 梭罗记账 SoloBooks · 面向一人公司与小微企业的 AI 记账工具

<p align="center">
  <img src="brand/solo-lockup.png" alt="梭罗记账 SoloBooks" width="420" /><br/>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-SoloBooks_v2.0-blue.svg" alt="SoloBooks License v2.0" /></a>
  <a href="#技术栈"><img src="https://img.shields.io/badge/Python-3.13-brightgreen.svg" alt="Python 3.13" /></a>
  <a href="#技术栈"><img src="https://img.shields.io/badge/Vue-3.x-42b883.svg" alt="Vue 3" /></a>
  <a href="MCP接入方案.md"><img src="https://img.shields.io/badge/MCP-Supported-purple.svg" alt="MCP Supported" /></a>
</p>

梭罗记账（SoloBooks）是一套面向**一人公司与小微企业**的智能记账与财税管理系统。以「记录 + 智能 → 记账 + 对账」为核心，帮助个体经营者、小微公司把日常收支、发票、银行流水管理成规范的会计账目，并自动生成凭证与经营报表。

> **为什么做梭罗记账？**
> 梭罗一个人在瓦尔登湖边生活、劳作、记录——一个人的公司，也值得一套真正清晰的账。传统代记账机构往往只帮你「把账记下来」，停留在流水登记，并不会真正去分析、管理你的财务。梭罗记账的初衷，是让 OPC（一人公司）和小微企业拥有一套**真正管好财务**的工具——不只是记录，而是对账、生成报表、做结账检查，让经营者自己也能看懂、掌控经营。

> 📌 **授权说明**：本项目采用**源码可用许可**（[SoloBooks License v2.0](LICENSE)）：个人、个体工商户、一人公司与小微企业可**永久免费自托管**用于本企业内部记账（含产生经营收入的日常记账）；未经书面授权，**不得**对外提供 SaaS 托管、付费云平台或第三方商业代账服务。商业授权（Partner License）与合作请联系：**taoguo66@yeah.net**。

---

## 功能特性

- **智能记账**：AI 识别文字/语音/单据 → 自动判断收支方向、金额与费用分类，生成记账凭证草稿
- **发票管理**：税务局全量发票 Excel 导入（标准列名自动映射）、AI 智能分类、发票明细查看、对账状态跟踪
- **银行/微信流水**：银行流水 CSV / 微信支付账单导入，自动识别收支方向，AI 辅助与账目对账匹配
- **结账向导**：月度结账流程——发票/流水对账、工资计提、AI 对账匹配、结账检查与账册锁定
- **财务报表**：利润表、资产负债表、现金流量表、往来账龄、增值税/所得税测算、税务申报导出
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
- **商业授权（Partner License）**：如需多租户云端连接器、代账批量管户平台或商业联合运营授权，请联系商务邮箱：**taoguo66@yeah.net**。

## 免责声明

本项目不构成任何税务、法律或财务建议。涉及税务申报等专业事项，请咨询持牌专业人士。
