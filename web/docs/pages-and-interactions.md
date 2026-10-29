# SoloBooks 前端页面与交互逻辑总览

> 基于代码实际实现整理（2026-10-29 最新架构，对应资产合流、发票与私账抽屉化、账簿五合一工作台、控制台 `/console` 安全迁移、周期账与冗余税表清理，以及报表双视图）。
> 技术栈：Vue 3.5 + TypeScript + Vite 8 + Naive UI + Tailwind CSS 4 + Pinia + ECharts。
> 品牌色定稿 `#1747B0`（`variables.css` 与 `App.vue` themeOverrides 已统一）。

---

## 1. 全局架构

### 1.1 路由与守卫（`src/router/index.ts` + `src/router/guards.ts`）

- 共 80+ 条活动 path 与规范重定向，四大块：公开页（`/landing`、`/login`、`/agreement`、`/privacy`、`/ai-connect`、`/share/:token`）+ `/console` 管理后台（嵌套 7 个子页）+ `/` 下 AppLayout 嵌套的业务子路由（含账表、税务、资产、归集、系统设置等页面）。
- **根域名分流与登录守卫**：全局守卫逻辑由 `guards.ts::resolveAuthRedirect(to, token)` 统一调度（单测见 `__tests__/navigation.test.ts`）：
  - 访问根路径 `/` 时：未登录直达官网落地页 `/landing`，已登录进入工作台 `/`；
  - 访问内部受保护页面：未登录跳 `/login`；已登录访问 `/login` 跳 `/`；
  - 独立协审页 `/share/:token` 与 AI 连接中心 `/ai-connect` 保持公开免登访问。
- **废弃路由与平滑兼容重定向**：
  - `/record/invoice-import` 自动重定向至 `/invoices?import=true`（唤起台账内置导入抽屉）；
  - `/intangibles` 自动重定向至 `/assets?tab=intangible`（进入统一资产台账无形资产标签页）；
  - `/reports/vat` 与 `/reports/cit` 自动重定向至 `/tax-filing?card=vat|cit`（沉淀入税务申报中心）；
  - 五大总明细与辅助账簿（`general-ledger`、`sub-ledger`、`chronological`、`multi-column`、`partner-balances`）自动重定向至 `/ledger/books?book=...` 统一电子账簿工作台；
  - 旧版手工记账 `/record/text|batch` 重定向到 `/copilot`，`/record/receipt` 重定向到 `/copilot?mode=receipt`；
  - 周期记账 `/recurring` 已全面下线移除，不再提供入口。
- `scrollBehavior`：带 hash 的导航平滑滚动并预留吸顶导航高度（`top: 72`），用于官网锚点导航。

### 1.2 布局壳（`src/pages/layout/`）

- **AppLayout.vue**：挂载时校验登录态 → `fetchProfile()` → `entryStore.fetchEntries()`，失败跳登录。固定侧边栏 + 顶栏 + `<router-view>`；支持侧栏折叠（切换 `--sidebar-width`）、`Ctrl/Cmd+K` 唤起全局搜索；≤1024px 隐藏侧栏。容器宽度采用分层响应式标准（见 `web/docs/design_guidelines.md`）：外层上限 1440px，多列财务报表、银行对账、统一账册工作台及台账全面支持 1200px~1240px 宽屏舒展展示，表单详情页保持 640px~720px 居中聚焦。
- **Sidebar.vue**：菜单定义在 `utils/constants.ts` 的 `NAV_ITEMS`，按工作阶段分四组：
  - **日常**：工作台 `/`、AI 会计 `/copilot`、待办 `/todos`
  - **账务**：经营账本 `/ledger`、发票台账 `/invoices`（含抽屉导入）、银行流水 `/bank`、记账凭证 `/vouchers`、电子账簿 `/ledger/books`（总账/明细账/序时账/多栏账/项目余额五合一）
  - **周期**：月度结账 `/closing-wizard`、税务申报 `/tax-filing`、经营报表 `/reports`
  - **工具**：专项核算（资产台账 `/assets`、研发费用 `/rd-expenses`、薪酬 `/mine/salary`、老板私账 `/private-expenses`、归集与分摊 `/aggregation`）与设置（公司信息、会计科目、辅助核算、期初、旧账导入、备份归档、审计日志、AI 连接、个人设置）
  - 二级菜单为手风琴，同一时间只展开一个父级；`≤1024px` 时固定侧栏隐藏，顶部汉堡按钮打开左侧抽屉导航。
  - 侧栏按老板视图 / 会计视图过滤；底部保留“切换到会计视图 / 查看老板摘要”的快捷入口。
- **Navbar.vue**：公司切换（NDropdown → `POST /api/auth/switch-company`，换发新 token 并重拉 profile）、视图分段切换器、用户菜单（公司管理 / 退出）。

### 1.3 全局状态（Pinia，`src/stores/`）

| Store | 持久化 | 职责 |
|---|---|---|
| `auth.ts` | localStorage（仅 token/userId，另写 `auth_token`） | 会话、profile、公司切换、step-up |
| `company.ts` | 否 | 公司列表及增改 |
| `entry.ts` | 否 | 流水缓存、按当前公司过滤的 `activeEntries`、本月收支/利润计算属性、流水增删改 |
| `aggregation.ts` | localStorage（仅当前维度 id） | 通用归集维度、规则与报表口径的前端共享状态 |
| `workspaceView.ts` | localStorage（按公司） | 老板视图 / 会计视图切换与持久化 |
| `admin.ts` | 否 | 平台控制台概览统计、用户/公司/用量数据缓存与全局运维状态 |

多页面默认优先读 entryStore 缓存，为空才 `fetchEntries()`。

### 1.4 API 层（`src/api/`，26 个业务模块）

`client.ts` 为 axios 实例并统一注入 token。模块清单：

| 模块 | 资源 |
|---|---|
| `auth.ts` | 手机验证码/密码登录、密码设置/重置、验证码绑定、profile、公司增改/切换、step-up 验证 |
| `admin.ts` | 平台控制台：概览、用户/公司管理、token 用量统计、审计与数据运维 |
| `record.ts` | 文字/票据/语音/批量记账、确认入账、流水增删改查、提交复核、过账选项 |
| `accounting.ts` | 过账/审批/提交复核/过账选项/冲销、期间结账检查/关账/重开 |
| `agent.ts` | AI Agent 会话（SSE 流式）+ 动作草稿列表/批准/拒绝 |
| `ledger.ts` | 生成凭证、总分类账/明细分类账/序时账/多栏账/辅助核算余额表、电子账簿导出 |
| `invoice.ts` / `invoices.ts` / `wechat-bill.ts` | 发票 Excel 解析导入 / 发票台账 / 微信账单解析与月度汇总 |
| `report.ts` / `tax-report.ts` / `tax-filing.ts` | 月报、健康分、三大报表及扩展报表、老板白话摘要、税务申报包与征期管理 |
| `rd.ts` | 研发项目/费用类型/台账/未分摊更正/佐证登记 |
| `aggregation.ts` / `auxiliary.ts` | 通用归集（维度/规则/清单/报表/口径对比）/ 辅助核算档案 |
| `asset.ts` / `intangible.ts` | 固定资产与无形资产卡片、折旧/摊销补提测算与处置 |
| `salary.ts` | 员工、工资表、计提/发放、社保个税、研发工时分配 |
| `legacy.ts` / `backup.ts` / `mcpTokens.ts` | 旧账导入 / 备份归档 / MCP 令牌 |
| `todo.ts` / `private-expense.ts` / `category.ts` / `audit.ts` / `copilot.ts` | 待办 / 私账 / 分类 / 审计日志 / AI 会计 |

### 1.5 官网首页（`src/pages/landing/index.vue`，公开）

- 面向访客的营销页：吸顶白色通栏导航（≤640px 收起链接）、Hero + 可切换三场景的「梭罗 AI 会计工作台」mockup、方案对比、Agent 能力、适用场景、合规资质（4 层架构）、页脚（ICP 预留位 `ICP_LICENSE`）。
- 主 CTA 统一为「免费注册 / 登录」：注册后自动获得演示账套，不再展示免注册试用入口。
- 锚点板块带 `scroll-margin-top` 防吸顶导航遮挡。注意：`.landing-page` 不可加 `overflow-x: hidden`，否则 sticky 导航失效（已踩坑）。

### 1.6 登录（`src/pages/auth/login.vue`）

双页签登录 + 全部辅助流：

- **验证码登录**：`POST /api/auth/phone/send-code`（60s 倒计时）→ `POST /api/auth/phone/login`。
- **密码登录**：`POST /api/auth/password/login`；「忘记密码」弹窗走验证码重置 `POST /api/auth/password/reset`。
- 登录成功统一 `finishLogin`：`setSession` + `fetchProfile` → `window.location.replace('/')`；未设密码（`has_password === false`）或昵称为空时写 `sessionStorage['solo:setup-password-hint']`，由登录后首页弹「完善账号信息」引导。
- **演示账套**：新手机号首次登录即注册，后端同步生成一本带样本数据的演示账套并设为当前账套；测试账号 `13800000000 / 123456` 保留供调试，前端不单独展示入口。
- 视觉遵循安静经营台（纯白卡片、无光晕/毛玻璃）；≤960px 收起左侧「梭罗实景协作卡」（三场景切换的纯 CSS 展示卡），登录卡内展示品牌区（定位语 + 口号 + 三个特色胶囊）；左上角「← 返回官网首页」。

### 1.7 平台管理控制台（路径安全隔离 `/console`，需平台管理员）

- 独立于业务布局的 `AdminLayout.vue`：后台入口收敛到安全路径 `/console`（旧 `/admin` 自动重定向），挂载时调 `GET /api/admin/overview` 探测权限（403 即拒绝），管理员身份不落 localStorage。
- 子页矩阵：概览 `AdminOverview`、token 用量 `AdminUsage`、用户 `AdminUsers`、公司 `AdminCompanies`、审计日志 `AdminAuditLogs`、Agent 动作审计 `AdminAgentAudit`、数据运维 `AdminDataOps`；API 封装在 `src/api/admin.ts`。
- **控制台通用架构**：全面引入 `useAdminTable` composable 与 `stores/admin.ts`，统一全后台页面的异步检索、多条件过滤、分页器及批量操作规范，保障管理端视觉与交互的高一致性。
- 后端权限模型：`auth_users.is_platform_admin` 每次查库校验（不进 JWT claim），`is_disabled` 用户全接口拦截。

### 1.8 双视图（老板 / 会计）

- 顶部栏公司切换器右侧常驻 `老板 | 会计` 分段开关；移动端放在抽屉顶部。
- 首次进入默认老板视图；切换后按公司写入 `solo:workspace-view:{companyId}`，不是按角色强制。
- 视图只改变导航、默认页面、信息密度和文案，不改变服务端权限；老板切到会计视图也不能越权过账或关账。
- 老板视图：保留六个高频入口，按使用节奏分为三组——`每天`：`老板工作台`（`/`，含上传发票、上传流水、记一笔）、`AI 会计`（`/copilot`）、`待办`（`/todos`）；`每月`：`月度结账`（`/closing-wizard`）、`报税`（`/tax-filing`）；`复盘`：`经营`（`/reports`）。经营账本、发票导入、银行流水、项目利润等通过工作台卡片、任务或报表卡片进入；公司信息和个人设置收进用户菜单。
- 会计视图：完整侧栏，包含凭证与账簿（`/ledger/books`）、会计科目、辅助核算、期初、旧账导入、备份、审计、MCP、研发费用和完整归集工作台。
- 会计专属路由通过 `meta.workspace: ['accounting']` 标记；老板深链访问时不拦截，页面顶部显示视图提示，可一键切换或返回工作台。

---

## 2. 页面清单

| 路由 | 页面 | 一句话用途 |
|---|---|---|
| `/` | home/index.vue | Agent 驾驶舱：唯一主动作 + 统一任务列表 + 经营概览 + 最近账目 |
| `/copilot` | copilot/index.vue | AI 会计对话工作台（查账/记账/票据/语音，流式） |
| `/ledger`、`/ledger/:id` | ledger/ | 经营账本流水列表与交易明细（编辑/冲销） |
| `/ledger/books` | ledger/books.vue | 统一电子账簿工作台（总分类账/明细分类账/序时账/多栏账/项目余额 5 合 1） |
| `/vouchers`、`/vouchers/:id`、`/vouchers/print` | voucher/ | 凭证生成管理、详情与打印 |
| `/invoices` | invoice/ledger.vue | 发票台账（进销项主数据，右上角常驻抽屉式导入向导） |
| `/reports` | report/index.vue | 报表中心门户（支持老板视图白话解读与会计视图法定四表） |
| `/reports/health` 等子页 | report/*.vue | 体检、权益变动、资产负债表、利润表、现金流量表、账龄、垫付台账、口径对比等 |
| `/tax-filing` | tax-filing/index.vue | 税务申报作业台（深度整合增值税与所得税辅助测算、税种分流与征期清单） |
| `/tax-registration` | tax-registration/index.vue | 税源登记与征期日程看板 |
| `/rd-expenses` | rd/index.vue | 研发项目台账、费用归集与勾稽（Tab微组件架构） |
| `/aggregation` 及 5 个子页 | aggregation/*.vue | 通用归集：维度、规则、待归集、报表、报表行口径 |
| `/bank` | bank/index.vue | 银行/微信流水台账、认领配对与差异修复 |
| `/closing-wizard` | closing-wizard/index.vue | 月度结账向导（5 步，支持往来小账本抽屉穿透） |
| `/assets` | asset/index.vue | 统一资产台账（固定资产与无形资产 Tab 并行展示） |
| `/assets/create` | asset/create.vue | 固定资产录入（双栏工作台 + 实时折旧测算） |
| `/intangibles/create` | intangible/create.vue | 无形资产录入（双栏工作台 + 实时摊销测算） |
| `/todos` | todo/index.vue | 智能待办（补票/税务/风险/待配，支持即时打勾办结） |
| `/private-expenses` | private-expense/index.vue | 老板私账收支台账（内置 PrivateExpenseDrawer 抽屉新建/编辑） |
| `/mine` 系列 11 页 | mine/*.vue | 个人中心、公司、分类、科目、辅助核算、期初、旧账导入、备份、薪酬（组件化）、MCP、审计 |
| `/landing` | landing/index.vue | 官网首页（公开，未登录访问 `/` 的落点） |
| `/ai-connect`（别名 `/docs/mcp-skills`） | mcp-guide/index.vue | AI 连接中心：MCP/Skills 概念、5 类客户端配置矩阵、开源共建（公开） |
| `/console` 系列 7 页 | admin/*.vue | 平台管理控制台（仅平台管理员，统一使用 useAdminTable） |
| `/login` | auth/login.vue | 验证码 / 密码登录 |

---

## 3. AI 记账与账本凭证

### 3.1 AI 会计工作台 `/copilot`

- **API**：`chatAgentStream`（POST `/api/agent/chat`，fetch + SSE 流式）、`listAgentActions`（GET `/api/agent/actions?status=pending`）、`recordReceipt`（POST `/api/record/receipt`，multipart 图片）、`recordSpeech`（POST `/api/record/speech`，录音）。
- **组件**：`AgentActionCard.vue`（待确认动作审批卡）；助手回复经 `utils/markdown.ts`（marked + DOMPurify 消毒）渲染。
- **资产处理确认**：AI 先调 `preview_asset_actions` 找缺卡交易和待补提折旧/摊销，再调 `propose_asset_closure` 生成一张 `propose_accrual`（`kind=asset_closure`）确认卡；老板确认后在同一事务内建固定资产/无形资产卡并按期间补提，任一步失败整批回滚。
- **交互逻辑**：
  - 发送后 assistant 消息进入 `streaming` 状态，按 SSE 事件处理：`token` 增量追加正文并自动滚动；`tool_call`/`tool_result` 增删工具步骤（running/ok/fail，带中文标签映射）；`action` 插入审批卡；`error` 标记失败。AbortController 支持「停止」。
  - 图片识别成功后将结果拼成文本自动发送；语音经 MediaRecorder 录音 → ASR → 自动发送（<2KB 视为误触丢弃）。
  - Enter 发送 / Shift+Enter 换行；支持清空对话与示例 prompt。
  - 接收外部跳入参数：`?mode=receipt` 自动打开选图，`?q=...&autoSend=1` 自动提问（来自首页输入栏与全局搜索）；**消费后立即调用 `router.replace` 清理 URL 参数**，避免用户刷新页面时重复触发提问或反复弹出选图框。
  - 顶部「待确认条」聚合未在当前对话流中出现的外部待办草稿（`stripActions.length > 0` 才渲染，支持一键展开/收起，杜绝「幽灵占位条」）；任一草稿审批（resolved）后，联动刷新 pendingActions 与 `entryStore.fetchEntries()`，确保全站账目与脉搏统计即时同步。
  - **视图感知**：请求携带 `workspaceView`（boss/accounting）；老板视图优先经营语言和下一步，会计视图可展开科目编码、借贷方向与勾稽细节。示例 prompt 也按视图切换。全局搜索在老板视图只返回账目，会计视图额外返回科目。

**SSE 流式协议**（`src/api/agent.ts`）：`data: {json}` 行以空行分隔。事件类型：`meta`（工具列表/maxSteps）、`token`（增量正文）、`tool_call`、`tool_result`（ok/summary）、`action`（完整 AgentActionItem，pending/approved/rejected/expired）、`final`、`done`、`error`。配套审批接口支持批量剔除明细（`excludeIndexes`）与逐行修正（`edits`）。

### 3.2 发票台账与导入抽屉 `/invoices`

- **架构演进**：彻底淘汰孤立的全局导入大页面 `/record/invoice-import`，升级为发票台账内置的侧滑抽屉（`InvoiceImportDrawer.vue`）。保持「查台账 → 导发票 → 列表就地刷新」的紧凑闭环。外部路由访问 `/record/invoice-import` 自动 301 重定向到 `/invoices?import=true`。
- **API**：`POST /api/invoice/parse`（解析校验）、`POST /api/invoice/import`（导入）、`POST /api/invoice/classify-entry/{id}`（AI 分类建议）、`PATCH /api/record/entries/{id}`（仅归档切换）、研发选项 `GET /api/rd/projects|expense-types`。
- **交互**：抽屉内三步向导 `upload → mapping → preview`。前端 XLSX 解析，自动映射税务局「数电发票」标准列（精确 + 正则模糊匹配），红冲/负数发票取负。preview 可逐行改方向/分类，研发行必选项目与费用类别；导入时剔除 `is_duplicate` 行，全重复则拦截。已有名下发票支持关键词/日期/收支/对账状态过滤分页；删除走统一确认弹窗。

### 3.3 经营账本 `/ledger`

- **列表页**：数据全部来自 entryStore（GET `/api/record/entries`），筛选为纯前端 computed——关键词、时间片（本周/本月/本季/今年）、性质（收入/支出/往来垫付/股东投入，`utils/entryNature.ts`）、**过账状态**（`utils/postingStatus.ts`：草稿/待复核/已过账/已冲销标签与筛选）、分类、单据状态；筛选变化重置页码。顶部汇总收入/支出/净利润与待补单据数；导出 Excel 为纯前端（`utils/export.ts`）。行点击 → `/ledger/:id`。老板视图隐藏「过账状态」和「凭证类型」两个专业筛选项。
- **明细页 `/ledger/:id`**：两段式加载（缓存未命中再 fetch，仍无则报错回列表）；`watch` 路由参数变化重新加载（同页跳转不残留旧数据）。按凭证状态分流：未过账 → 可编辑/删除/提交复核；已过账 → 可冲销（弹窗必填原因 + 可选冲销日）；已冲销 → 只读。删除/冲销均为二次确认弹窗。过账按钮按 `GET /api/record/entries/{id}/posting-options` 的 `canPost/canApprove/canSubmit` 与公司 `postingPolicy`（maker-checker 制衡：制单人不能自己复核）渲染，提交复核走 `POST /api/record/entries/{id}/submit-review`。复式凭证分录区默认折叠（无存储分录时由 `utils/accounting.ts` 前端生成借贷表）。

### 3.4 凭证 `/vouchers`

- **列表页**：按 `voucherStatus/voucherRef` 把账目分为「待生成凭证」（预览前 10 条）与「已生成凭证」两组；「批量生成 N 份凭证」→ POST `/api/ledger/voucher`（body `{entryIds}`），成功后刷新缓存。
- **详情页 `/vouchers/:id`**：打印版记账凭证（凭证号、借贷分录、研发归集、风险标签、签署区），`window.print()` 配合 `@media print` 样式；研发项目/费用类型名称翻译失败时静默降级为 ID。

---

## 4. 报表、税务与研发

### 4.1 报表中心 `/reports`

- **API**：`POST /api/report/monthly`（月度简报）、`GET /api/tax-report/xiaoqiye/month-quarter/export`（XLS blob）。
- **双视图规范**：
  - **老板视图**：展示经营白话驾驶舱、核心财务指标、现金/应收/应付透视与 AI 诊断，隐藏专业账簿底稿，底部提供「切换到会计视图」；
  - **会计视图**：展示小企业会计准则标准四表（资产负债表、利润表、现金流量表、所有者权益变动表）与专业核算对比工具。
- **账簿与税辅合流**：
  - 五大总明细与辅助账簿（总账、明细账、序时账、多栏账、核算项目余额表）已合并迁往 `/ledger/books` 统一电子账簿工作台；
  - 增值税与企业所得税申报辅助表已整合入 `/tax-filing` 税务申报中心，原路由保持平滑重定向。

### 4.2 子报表页（统一「返回 + 期间选择器 + 只读展示」结构）

| 页面 | API（均 POST /api/report/...） | 特色交互 |
|---|---|---|
| health | `/health-score`、`/ai-diagnosis` | ECharts 仪表盘总分 + 5 维条形图；AI 点评按需生成 |
| archive | `/archive-checklist` | 合规状态标签统计卡 |
| tax-summary | `/tax-summary` | 季度 4 格统计 + 申报截止日状态 + 阻断告警 |
| balance-sheet / cash-flow | `/balance-sheet`、`/cash-flow` | 标准小企业会计准则原生表格，支持老板白话模式切换 |
| income-statement | `/income-statement` | `viewMode: summary/quarterly` 总表/季报切换，多级科目缩进 |
| equity-changes | `/equity-changes` | 所有者权益变动表 |
| comparison | `/comparison` | 唯一 NDataTable 报表页；环比/同比，变动比例着色 |
| advance-balances | GET `/api/ledger/advance-balances` | 垫付台账：按 `entry.payer` 汇总老板/员工期初未报、本期垫付与报销、期末未报 |
| ar-aging / ap-aging | `/ar-aging`、`/ap-aging` | 孪生页，账龄桶分组卡片，顶部 Radio 互跳应收/应付 |

> 注：总分类账、明细账、序时账、多栏账与项目余额表见 §4.5 电子账簿工作台。

### 4.3 税务申报 `/tax-filing`

- **API**：`GET /api/tax-filing/packages/{period}`、`POST /api/tax-filing/items/{deadlineId}/file`（标记已申报/零申报，带 evidence）、XLS 导出复用小企业报表接口。
- **申报辅助整合**：原 `/vat-assist` 与 `/cit-assist` 的填报测算、开票对比与减免税计算已全量沉淀进申报中心任务卡内部。
- **交互**：季度选择器；**顶层全局看板**（本期预计总税额免税/缴税大字、征期倒计时与待办办结进度环、免税红线紧凑监控槽、AI 税情简报胶囊、前置三方核验一致性状态条与差异就地展开）；**极简 3 步申报作业清单流**：
  - 任务 1「增值税小规模申报」：醒目展示**极速抄报手卡**（核心 1~2 个关键数值一键复制），完整栏次按需折叠展开，底部直接提供「已在电子税局申报，标记办结」；
  - 任务 2「企业所得税季度预缴」：A200000 核心数值手卡（收入、利润、5%小微预缴税额）+ 完整行次折叠 + 标记办结；
  - 任务 3「财务报表报送与附税」：一键导出税局标准 XLS 模板 + 标记报送完成，附税事项清单支持无税源快速核销。
  - **经营所得个税**（仅非公司主体）：A 表预缴按主体配置月/季频率，B 表汇算截止次年 3/31，C 表两处以上给出提示与诊断；核定征收不适用。
- **老板视图摘要**：页面顶部展示“本期预计应缴、申报期、待办数、最近截止日”，专业申报表和行次保留在下方；老板可先看结论，再切换到会计视图处理明细。

### 4.4 研发费用 `/rd-expenses`

- **API**：`/api/rd/projects|expense-types|ledger|unallocated|reconciliation-summary|evidence` 全套；未归集更正 `POST /api/rd/unallocated/{entryId}/correct` 带 **Idempotency-Key 头**，已过账项自动生成冲销凭证。
- **组件化架构**：页面采用四大标签页解构：
  - `RdProjectsTab`：研发项目维护与预算生命周期管理；
  - `RdLedgerTab`：研发费用明细台账与项目交叉筛选；
  - `RdReconciliationTab`：台账与总账科目勾稽核对看板；
  - `RdUnallocatedTab`：未分配费用清单与智能分摊推荐；
  - 配套弹窗：`RdProjectModal`（项目立项）、`RdEvidenceModal`（佐证登记）、`RdCorrectionModal`（多项目依据分摊拆分更正）。

### 4.5 统一电子账簿工作台 `/ledger/books`

- **架构理念**：将以往分散在 5 个子页面的账册统一收敛为单工作台（总分类账、明细分类账、序时账、多栏账、辅助核算项目余额表 5 合 1）。
- **交互**：左侧账册导航树 / 切换器 + 右侧标准三栏式或数量金额式账页；支持会计科目与核算项目即时穿透到明细账及凭证源头；支持账页标准打印与 Excel 批量导出。

---

## 5. 资金、结账与资产

### 5.1 月度结账向导 `/closing-wizard`（最复杂页面，5 步 NSteps；第 4 步为「智能流水认领与配平」）

1. **凭证导入**：发票 XLSX 前端解析（数电格式 + 模糊列名、红冲负数）→ parse 校验 → import；支持「仅归档不记账」。
2. **银行流水导入**：银行 Excel 自动定位表头（评分制）、列名映射收入/支出、过滤合计行、多格式日期归一 → `POST /api/bank/import-json`；微信账单走 parse → 预览（可删行/滤重）→ import。
3. **工资计提与发放确认**：读 `GET /api/salary/sheets/{month}`；工资按权责发生制，所属月必须计提，有在职员工未计提或计提凭证待复核时阻断下一步。已计提未发放、社保/公积金/个税挂账作为负债结转，不阻断结账；个人代扣社保公积金计入 `221104`，不再挂 `122101`。发放可延至次月，页面会列出 `GET /api/salary/payments/suggestions` 返回的银行流水建议，确认后调用 `POST /api/salary/payments/apply` 生成发放凭证并按流水日期核销。社保、公积金可分开缴纳（`pay-social` / `pay-housing-fund` / `pay-tax`），银行流水建议走 `GET /api/salary/liabilities/suggestions` 与 `POST /api/salary/liabilities/apply`，确认后按流水日期生成缴款凭证；金额为 0 的税种返回 `not_required`，不生成 0 元凭证。
4. **智能流水认领与配平**：以银行流水为客观事实，系统自动识别平台提现划转、银行利息等确定性流水；其余每笔流水以卡片流呈现，可选择「确认配账」（匹配已有账目/发票）、「认可入账」（按 AI 建议分类新建）或「快捷切换用途」；检测到未关联的等额发票时可直接「关联发票入账」。本月流水按认领完成率展示进度；历史月份遗留未认领流水折叠在独立区域，按历史遗留单独处理。配平完成前不允许进入关账。往来小账本汇总支持侧滑抽屉穿透查看单据明细并一键直达对应台账。
5. **合规检查与关账**：前置校验（工资已计提、零申报勾选 `confirmZeroActivity`、高风险对账、canClose 或「可批量过账」）→ `POST /api/periods/{month}/close`（带 **Idempotency-Key: close-{month}**）。**交互优化**：检查项凭证与未过账任务支持**向导内就地弹窗审阅/立即过账**以及**一键批量过账本月凭证**，彻底杜绝跳往 `/vouchers` 造成的流程断层；后端在同一事务内批量过账本期可过账草稿再关账，任一失败整体回滚；成功后刷新账目缓存并拉利润表 + AI 诊断展示 CFO 报告；readiness 检查项可点击跳转对应步骤。

- **老板视图摘要**：页面顶部显示“本月是否可关账、还有几项待处理、待过账凭证数、阻断数、下一个待办”，并可切换到会计视图查看完整五步流程。
- 已结账月份（`periodStatus==='closed'`）直接落到第 5 步只读展示。
- 内嵌 **ClosingAgentPanel**（AI 结账顾问）：SSE 流式对话，工具含 run_close_checks / list_unmatched / propose_match 等，可产生待审批动作卡。

### 5.2 银行流水 `/bank`

- **API**：`GET /api/bank/statements`、`GET /api/bank/difference?month=`、`POST /api/bank/import`（CSV 粘贴）/ `import-json`（Excel）、`/api/bank/match`（规则匹配）、`/api/bank/ai-suggest` + `confirm-suggestion`（匹配确认与微信结算统一走后端 `bank_settlement_service`，产生结算凭证草稿需到凭证页过账）、`POST /api/bank/repair-effect`（差异修复，可能同时生成结算凭证草稿）、`DELETE /api/bank/statements/{id}`。
- **交互**：关键字/日期/收支/匹配状态多维筛选分页；差异报告展示余额勾稽与异常账银影响，可跳 `/ledger/{entryId}`；差异修复与建议确认的提示会区分「已生成结算凭证草稿，请前往凭证页过账」与「修复完成」。
- **工资流水守卫**：支出流水摘要或分类被识别为工资发放时，`POST /api/bank/confirm-suggestion`（`type=new`）返回 409 `SALARY_REQUIRES_PAYROLL`，禁止在银行页直接记工资费用；应到工资模块先计提，再按流水确认发放，由系统生成 `221101` 应付职工薪酬核销凭证。单笔 AI 分类也会把工资类支出归到 `221101` 并标记 `requiresSalarySettlement`。
- **社保/公积金流水守卫**：支出流水摘要或分类被识别为社保、公积金缴纳时，银行页直接记账返回 409 `PAYROLL_LIABILITY_REQUIRES_PAYROLL`，必须到工资模块按 `221102`/`221104` 缴款凭证核销，避免重复计入费用。

### 5.3 统一资产台账 `/assets`（固定资产与无形资产合流）

- **统一台账页面 `/assets`**：采用标签页合流架构，包含 `FixedAssetTab.vue`（固定资产）与 `IntangibleAssetTab.vue`（无形资产）。顶部统计卡统一呈现原值、累计折旧/摊销、净值与有效资产卡片数。原 `/intangibles` 自动平滑重定向至 `/assets?tab=intangible`。
- **双栏录入工作台（`/assets/create` 与 `/intangibles/create`）**：
  - **布局结构**：左栏 640px 基础参数与分摊设置，右栏粘性吸顶实时推演折旧/摊销排期看板；
  - **分级折叠与渐进披露**：基础信息默认展开，研发归集、残值率与纳税调整按需折叠；
  - **残值率快捷选项**：提供 `0%`（电子设备/无形资产常用）与 `5%`（传统机具常用）一键快捷切换按钮；
  - **来源交易锚定**：携带 `sourceEntryId` 时激活来源凭证锚定卡（`source-anchor`），展示入账凭证号、原值与记账日期，防止重复做账；
  - **一键补提**：未提满历史期间支持直接测算补提，生成标准计提凭证。

### 5.4 待办 `/todos`

- **API**：`GET /api/todos`、`PATCH /api/todos/{key}`（done/dismissed）。
- **交互**：统计卡（待处理/高优/已完成）+ 过滤 Tab；**支持直接点击复选框即时打勾办结**，税费申报按申报期合并展示；按 meta 智能跳转——账目类 `/ledger/:id`、流水类 `/bank`、资产方案确认类跳转至双栏录入工作台；来源凭证已建卡后待办自动消失。

### 5.5 周期账模块（已下线清理）

- 遵循 YAGNI 原则与极简架构治理，周期记账（原 `/recurring` 系列路由与后端接口）已彻底移除，常态经营收支全部交由流水导入与工作台任务闭环驱动。

### 5.6 老板私账 `/private-expenses`

- **架构演进**：淘汰独立的 `/private-expenses/create` 录入大页面，重构为台账内置的侧滑抽屉（`PrivateExpenseDrawer.vue`）。外链访问创建路由自动重定向并唤起抽屉。
- **列表**：汇总卡（笔数/收入/支出/可收回押金/可冲销金额与净额）+ 归属人 pill 筛选 + 分页表格；状态列区分押金可收回、收入可用/受限、支出无票/有票/补票冲抵（关联正式账目名）。
- **抽屉式填报**：方向切换联动分类与票据状态；支出可选 `no_invoice/has_invoice/substitute`（substitute 需从正式支出账目中选择冲抵项）；收入可选资金可用/受限；归属人 tag 自动补齐与记忆。

### 5.8 通用归集 `/aggregation`

- **工作台**：按会计期间展示归集进度、待归集金额与维度分布。
- **子页**：维度与对象（辅助核算档案）、归集规则、待归集清单、归集报表、报表行口径对比。
- **口径**：归集属于管理口径，不替代法定账簿；报表行规则用于在法定报表与管理报表之间对齐展示。

---

## 6. 首页与系统设置

### 6.1 工作台首页 `/`

Agent 驾驶舱式布局，按「先处理、再看数」排列：

1. 问候 + 老板视图的三张高频卡片：上传发票、上传流水、记一笔；会计视图保留「录入凭证 / 问 AI 会计」主按钮和四个专业入口。
2. 经营概览（净利润、收入、支出、本年/上月切换），不再展示硬编码健康结论。
3. 底部双栏：左侧「今天要处理」统一任务列表（`useTaskInbox` / `TaskInbox`），右侧最近 5 条账目。任务列表聚合 AI 待确认草稿、月度结账阻断、税务征期、普通待办，最多 5 条，按逾期、截止日、阻断程度和优先级排序，标注来源。同截止日的多个税种合并为一条“税务申报有 N 项待处理”，避免重复占满列表。

任一任务来源失败时其余来源正常展示，不再把结账、税务、待办和 AI 草稿拆成四个并列区块。

老板视图与会计视图的差异：

- 老板视图：标题为“老板工作台”，页头下方三张高频卡片：上传发票、上传流水、记一笔；再往下是经营概览，底部左侧任务、右侧最近账目。
- 会计视图：标题为“会计工作台”，主动作“录入凭证 / 问 AI 会计”，在任务列表上方增加凭证管理、总账、月末结账、税务申报四个专业入口。

### 6.2 `/mine` 系列

| 页面 | 要点 |
|---|---|
| index | 个人中心总览，设置与快捷导航卡片 |
| companies | 公司列表/切换/新建/编辑（税号校验、纳税人类型、小微企业、CIT 预缴方式、经营所得个税预缴频率） |
| categories | 分类 CRUD，被流水引用的分类禁删 |
| accounts | 会计科目树（NTree + Tab 过滤），`/api/ledger/accounts*`；系统科目禁删；支持数量金额式核算开关 |
| contacts | 辅助核算档案（客户/供应商/项目/部门等），供凭证行辅助核算挂接 |
| opening | 期初余额 `GET/PUT /api/opening/balances`；XLSX 模板导入导出；已生成凭证则锁定；改动需手机验证码 **step-up**（`POST /api/auth/step-up`）；税务期初备忘存 localStorage |
| legacy-import | 旧账导入：历史账套导入、旧账透传（含数量/辅助核算），导入后需试算平衡 |
| backup | 备份与归档：数据备份、恢复与归档记录 |
| salary | 组件化架构：员工花名册（`EmployeeRosterTab`）/ 月度工资表（`SalarySheetTab`）/ 部门、定薪、计税、研发分摊模态窗 |
| mcp | MCP 令牌管理：PAT、scope、限流与调用留痕 |
| audit-log | 审计日志只读分页表（`GET /api/audit/logs`） |

---

## 7. 全局交互模式约定

- **AI 动作审批**：AI 产生的一切入账操作先成为 pending `AgentActionItem`，经 `AgentActionCard` 人工批准/拒绝后才执行；批准接口支持批量剔除明细与逐行修正。全链路口径「AI 起草、人工确认」。
- **过账底线**：standard 凭证过账必须有有效原始业务来源（`source_transaction`，缺失/作废返回 409）；银行导入且摘要含微信的凭证过账前做疑似重复收入检查（`POSSIBLE_DUPLICATE_WECHAT_INCOME`，候选返回前端核对）；微信结算单已有收入/手续费账目时拦截重复核销（`EXISTING_WECHAT_ENTRIES`）；工资类支出流水不得直接记费用（`SALARY_REQUIRES_PAYROLL`），社保/公积金类支出不得直接记费用（`PAYROLL_LIABILITY_REQUIRES_PAYROLL`），必须走工资计提、发放与缴款核销。
- **制单复核分离**：`posting_policy=maker_checker` 的公司，制单人不能复核自己的凭证；过账能力以 `posting-options` 接口为准。
- **SSE 流式**：Agent 会话统一走 fetch + SSE（非 axios），事件协议见 §3.1。
- **删除确认**：统一 `useDeleteConfirm` 组合式函数（NModal 二次确认）。
- **幂等**：关账与研发分摊更正等写操作携带 Idempotency-Key 头。
- **敏感操作 step-up**：期初余额修改等需手机验证码二次验证。
- **导出**：Excel/XLS 走后端 blob 或前端 SheetJS；PDF 走 html2canvas + jsPDF 截图。
- **错误处理**：axios 统一拦截（401 跳登录），业务错误优先展示后端 `detail`；AI 流式中断/失败在消息内联标记。
- **缓存策略**：流水类页面共享 entryStore，页面挂载时缓存优先、按需刷新。

---

## 8. 主要跳转关系（速查）

```
未登录访问 / ──► /landing 官网 ──► /login
登录成功 ────► / 工作台（按登录态分流）
首页主按钮 ──► /copilot（记账/票据/咨询统一入口）
今天要处理 ──► /todos、/copilot、/closing-wizard、/tax-filing（按来源分流）
顶部视图开关 ──► 老板 / 会计（按公司记忆；深链会计页面时显示切换提示）
全局搜索 ────► /copilot?q=… / 各详情页
发票台账 ────► /invoices（内置 InvoiceImportDrawer 抽屉导入，旧导入路由重定向至此）
统一资产 ────► /assets（Fixed / Intangible 双 Tab 并行；/intangibles 重定向至此）
资产录入 ────► /assets/create、/intangibles/create（双栏工作台 + 实时测算）
电子账簿 ────► /ledger/books?book=...（总账/明细账/序时账/多栏账/项目余额 5 合 1）
报表中心 ────► /reports（法定四表 / 经营体检 / 账龄 / 垫付台账 / 口径对比）
税务申报 ────► /tax-filing（整合增值税与企业所得税测算、一键抄报手卡与零申报）
私账管理 ────► /private-expenses（内置 PrivateExpenseDrawer 抽屉新建/编辑）
待办中心 ────► /todos（复选框直接打勾办结；直跳 /ledger/:id、/bank、资产双栏录入）
账本列表 ────► /ledger/:id ──► 提交复核/过账/冲销/编辑
结账向导 ────► /closing-wizard（往来小账本抽屉穿透 + 流水认领 + ClosingAgentPanel）
管理员 ───────► /console（统一 useAdminTable 架构：概览/用量/用户/主体/审计/运维）
```
