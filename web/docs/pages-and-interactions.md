# SoloBooks 前端页面与交互逻辑总览

> 基于代码实际实现整理（2026-10-11，对应远端 `ad42153`，含官网首页、密码登录、管理后台、品牌更名「梭罗记账 SoloBooks」）。
> 技术栈：Vue 3.5 + TypeScript + Vite 8 + Naive UI + Tailwind CSS 4 + Pinia + ECharts。
> 品牌色定稿 `#1747B0`（`variables.css` 与 `App.vue` themeOverrides 已统一）。

---

## 1. 全局架构

### 1.1 路由与守卫（`src/router/index.ts` + `src/router/guards.ts`）

- 共 57 条 path 定义，四大块：公开页（`/landing`、`/login`、`/agreement`、`/privacy`）+ `/admin` 管理后台（嵌套 7 个子页）+ `/` 下 AppLayout 嵌套的业务子路由。
- 全局守卫逻辑抽为纯函数 `guards.ts::resolveAuthRedirect(to, token)`（可单测，见 `__tests__/navigation.test.ts`）：未登录访问 `/` 跳官网 `/landing`；未登录访问受保护页跳 `/login`；已登录访问 `/login` 跳 `/`。
- `scrollBehavior`：带 hash 的导航平滑滚动并预留吸顶导航高度（`top: 72`），用于官网锚点导航。
- 旧的手动记账页（`/record/text|batch`）已重定向到 `/copilot`（统一 AI Agent 工作台），`/record/receipt` 重定向到 `/copilot?mode=receipt`。

### 1.2 布局壳（`src/pages/layout/`）

- **AppLayout.vue**：挂载时校验登录态 → `fetchProfile()` → `entryStore.fetchEntries()`，失败跳登录。固定侧边栏 + 顶栏 + `<router-view>`；支持侧栏折叠（切换 `--sidebar-width`）、`Ctrl/Cmd+K` 唤起全局搜索；≤1024px 隐藏侧栏。容器宽度采用分层响应式标准（见 `web/docs/design_guidelines.md`）：外层上限 1440px，多列财务报表、银行对账、发票导入向导及台账全面支持 1200px~1240px 宽屏舒展展示，表单详情页保持 640px~720px 居中聚焦。
- **Sidebar.vue**：菜单定义在 `utils/constants.ts` 的 `NAV_ITEMS`，分三组：
  - **日常**：工作台 `/`、AI 会计 `/copilot`、经营账本 `/ledger`、待办 `/todos`
  - **周期**：月度结账 `/closing-wizard`、税务申报 `/tax-filing`、经营报表 `/reports`
  - **管理**：两个子菜单——财务工具（发票导入 `/record/invoice-import`、银行流水 `/bank`、凭证 `/vouchers`、资产 `/assets`、研发费用 `/rd-expenses`、薪酬 `/mine/salary`、老板私账 `/private-expenses`）与系统设置（`/mine` 系列）；另有周期账 `/recurring`
- **Navbar.vue**：公司切换（NDropdown → `POST /api/auth/switch-company`，换发新 token 并重拉 profile）、用户菜单（公司管理 / 退出）。

### 1.3 全局状态（Pinia，`src/stores/`）

| Store | 持久化 | 职责 |
|---|---|---|
| `auth.ts` | localStorage（仅 token/userId，另写 `auth_token`） | 会话、profile、公司切换、step-up |
| `company.ts` | 否 | 公司列表及增改 |
| `entry.ts` | 否 | 流水缓存、按当前公司过滤的 `activeEntries`、本月收支/利润计算属性、流水增删改 |

多页面默认优先读 entryStore 缓存，为空才 `fetchEntries()`。

### 1.4 API 层（`src/api/`，20 个业务模块）

`client.ts` 为 axios 实例并统一注入 token。模块清单：

| 模块 | 资源 |
|---|---|
| `auth.ts` | 手机验证码/密码登录、密码设置/重置、验证码绑定、profile、公司增改/切换、step-up 验证 |
| `admin.ts` | 管理后台：概览、用户/公司管理、token 用量统计、审计与数据运维 |
| `record.ts` | 文字/票据/语音/批量记账、确认入账、流水增删改查、提交复核、过账选项 |
| `accounting.ts` | 过账/审批/提交复核/过账选项/冲销、期间结账检查/关账/重开 |
| `agent.ts` | AI Agent 会话（SSE 流式）+ 动作草稿列表/批准/拒绝 |
| `ledger.ts` | 生成凭证、总账/明细账 |
| `invoice.ts` / `wechat-bill.ts` | 发票 Excel / 微信账单 解析与导入 |
| `report.ts` / `tax-report.ts` | 月报、健康分、三大报表、税务汇总、AI 诊断；小规模申报导出 |
| `rd.ts` | 研发项目/费用类型/台账/未分摊更正/佐证 |
| `salary.ts` | 员工、工资表、计提/发放、社保个税 |
| `asset.ts` / `recurring.ts` / `todo.ts` / `private-expense.ts` / `category.ts` / `audit.ts` / `copilot.ts` | 资产折旧 / 周期模板 / 待办 / 私账 / 分类 / 审计日志 / 旧版 AI 对话 |

### 1.5 官网首页（`src/pages/landing/index.vue`，公开）

- 面向访客的营销页：吸顶白色通栏导航（≤640px 收起链接）、Hero + 可切换三场景的「梭罗 AI 会计工作台」mockup、方案对比、Agent 能力、适用场景、合规资质（4 层架构）、页脚（ICP 预留位 `ICP_LICENSE`）。
- 「免注册 · 一键试用」直接调 `loginByPhoneCode(13800000000/123456)` 免密进入工作台，失败兜底跳登录页；已登录访问时导航显示「进入工作台」。
- 锚点板块带 `scroll-margin-top` 防吸顶导航遮挡。注意：`.landing-page` 不可加 `overflow-x: hidden`，否则 sticky 导航失效（已踩坑）。

### 1.6 登录（`src/pages/auth/login.vue`）

双页签登录 + 全部辅助流：

- **验证码登录**：`POST /api/auth/phone/send-code`（60s 倒计时）→ `POST /api/auth/phone/login`。
- **密码登录**：`POST /api/auth/password/login`；「忘记密码」弹窗走验证码重置 `POST /api/auth/password/reset`。
- 登录成功统一 `finishLogin`：`setSession` + `fetchProfile` → `window.location.replace('/')`；未设密码（`has_password === false`）或昵称为空时写 `sessionStorage['solo:setup-password-hint']`，由登录后首页弹「完善账号信息」引导。
- **测试账号一键试用**（13800000000 / 123456）。
- 视觉遵循安静经营台（纯白卡片、无光晕/毛玻璃）；≤960px 收起左侧「梭罗实景协作卡」（三场景切换的纯 CSS 展示卡），登录卡内展示品牌区（定位语 + 口号 + 三个特色胶囊）；左上角「← 返回官网首页」。

### 1.7 管理后台（`src/pages/admin/`，需平台管理员）

- 独立于业务布局的 `AdminLayout.vue`：挂载时调 `GET /api/admin/overview` 探测权限（403 即拒绝），管理员身份不落 localStorage。
- 子页：概览 `AdminOverview`、token 用量 `AdminUsage`、用户 `AdminUsers`、公司 `AdminCompanies`、审计日志 `AdminAuditLogs`、Agent 动作审计 `AdminAgentAudit`、数据运维 `AdminDataOps`；API 封装在 `src/api/admin.ts`。
- 后端权限模型：`auth_users.is_platform_admin` 每次查库校验（不进 JWT claim），`is_disabled` 用户全接口拦截。

---

## 2. 页面清单

| 路由 | 页面 | 一句话用途 |
|---|---|---|
| `/` | home/index.vue | Agent 驾驶舱：全能输入栏 + 待审草稿池 + 结账提醒 + 统计 |
| `/copilot` | copilot/index.vue | AI 会计对话工作台（查账/记账/票据/语音，流式） |
| `/ledger`、`/ledger/:id` | ledger/ | 经营账本流水列表与交易明细（编辑/冲销） |
| `/vouchers`、`/vouchers/:id` | voucher/ | 凭证生成管理与打印版凭证详情 |
| `/record/invoice-import` | record/invoice-import.vue | 税局发票 Excel 批量导入与记账 |
| `/reports` | report/index.vue | 报表中心门户（12 张子报表导航） |
| `/reports/health` 等 12 个子页 | report/*.vue | 体检、税辅、三大表、总/明细账、账龄、同比等 |
| `/tax-filing` | tax-filing/index.vue | 税务申报作业台（三页签 + 征期 checklist） |
| `/rd-expenses` | rd/index.vue | 研发项目台账、费用归集与勾稽 |
| `/bank` | bank/index.vue | 银行/微信流水台账、对账与差异修复 |
| `/closing-wizard` | closing-wizard/index.vue | 月度结账向导（5 步，最复杂页面） |
| `/assets`、`/assets/create` | asset/ | 固定资产台账与录入（自动折旧） |
| `/todos` | todo/index.vue | 智能待办（补票/税务/风险/待配） |
| `/recurring`（/create、/:id） | recurring/ | 周期账模板管理与手动应用 |
| `/private-expenses`（/create、/:id） | private-expense/ | 老板私账收支、押金与冲销 |
| `/mine` 系列 7 页 | mine/*.vue | 个人中心、公司、分类、科目、期初、薪酬、审计 |
| `/landing` | landing/index.vue | 官网首页（公开，未登录访问 `/` 的落点） |
| `/admin` 系列 7 页 | admin/*.vue | 平台管理后台（仅平台管理员） |
| `/login` | auth/login.vue | 验证码 / 密码登录 |

---

## 3. AI 记账与账本凭证

### 3.1 AI 会计工作台 `/copilot`

- **API**：`chatAgentStream`（POST `/api/agent/chat`，fetch + SSE 流式）、`listAgentActions`（GET `/api/agent/actions?status=pending`）、`recordReceipt`（POST `/api/record/receipt`，multipart 图片）、`recordSpeech`（POST `/api/record/speech`，录音）。
- **组件**：`AgentActionCard.vue`（待确认动作审批卡）；助手回复经 `utils/markdown.ts`（marked + DOMPurify 消毒）渲染。
- **交互逻辑**：
  - 发送后 assistant 消息进入 `streaming` 状态，按 SSE 事件处理：`token` 增量追加正文并自动滚动；`tool_call`/`tool_result` 增删工具步骤（running/ok/fail，带中文标签映射）；`action` 插入审批卡；`error` 标记失败。AbortController 支持「停止」。
  - 图片识别成功后将结果拼成文本自动发送；语音经 MediaRecorder 录音 → ASR → 自动发送（<2KB 视为误触丢弃）。
  - Enter 发送 / Shift+Enter 换行；支持清空对话与示例 prompt。
  - 接收外部跳入参数：`?mode=receipt` 自动打开选图，`?q=...&autoSend=1` 自动提问（来自首页输入栏与全局搜索）；**消费后立即调用 `router.replace` 清理 URL 参数**，避免用户刷新页面时重复触发提问或反复弹出选图框。
  - 顶部「待确认条」聚合未在当前对话流中出现的外部待办草稿（`stripActions.length > 0` 才渲染，支持一键展开/收起，杜绝「幽灵占位条」）；任一草稿审批（resolved）后，联动刷新 pendingActions 与 `entryStore.fetchEntries()`，确保全站账目与脉搏统计即时同步。

**SSE 流式协议**（`src/api/agent.ts`）：`data: {json}` 行以空行分隔。事件类型：`meta`（工具列表/maxSteps）、`token`（增量正文）、`tool_call`、`tool_result`（ok/summary）、`action`（完整 AgentActionItem，pending/approved/rejected/expired）、`final`、`done`、`error`。配套审批接口支持批量剔除明细（`excludeIndexes`）与逐行修正（`edits`）。

### 3.2 发票导入 `/record/invoice-import`

- **API**：`POST /api/invoice/parse`（解析校验）、`POST /api/invoice/import`（导入）、`POST /api/invoice/classify-entry/{id}`（AI 分类建议）、`PATCH /api/record/entries/{id}`（仅归档切换）、研发选项 `GET /api/rd/projects|expense-types`。
- **交互**：三步向导 `upload → mapping → preview`。前端 XLSX 解析，自动映射税务局「数电发票」标准列（精确 + 正则模糊匹配），红冲/负数发票取负。preview 可逐行改方向/分类，研发行必选项目与费用类别；导入时剔除 `is_duplicate` 行，全重复则拦截。已有发票列表支持关键词/日期/收支/对账状态过滤分页；「去记账」弹窗内 AI 分类建议需手动请求；删除走统一确认弹窗。

### 3.3 经营账本 `/ledger`

- **列表页**：数据全部来自 entryStore（GET `/api/record/entries`），筛选为纯前端 computed——关键词、时间片（本周/本月/本季/今年）、性质（收入/支出/往来垫付/股东投入，`utils/entryNature.ts`）、**过账状态**（`utils/postingStatus.ts`：草稿/待复核/已过账/已冲销标签与筛选）、分类、单据状态；筛选变化重置页码。顶部汇总收入/支出/净利润与待补单据数；导出 Excel 为纯前端（`utils/export.ts`）。行点击 → `/ledger/:id`。
- **明细页 `/ledger/:id`**：两段式加载（缓存未命中再 fetch，仍无则报错回列表）；`watch` 路由参数变化重新加载（同页跳转不残留旧数据）。按凭证状态分流：未过账 → 可编辑/删除/提交复核；已过账 → 可冲销（弹窗必填原因 + 可选冲销日）；已冲销 → 只读。删除/冲销均为二次确认弹窗。过账按钮按 `GET /api/record/entries/{id}/posting-options` 的 `canPost/canApprove/canSubmit` 与公司 `postingPolicy`（maker-checker 制衡：制单人不能自己复核）渲染，提交复核走 `POST /api/record/entries/{id}/submit-review`。复式凭证分录区默认折叠（无存储分录时由 `utils/accounting.ts` 前端生成借贷表）。

### 3.4 凭证 `/vouchers`

- **列表页**：按 `voucherStatus/voucherRef` 把账目分为「待生成凭证」（预览前 10 条）与「已生成凭证」两组；「批量生成 N 份凭证」→ POST `/api/ledger/voucher`（body `{entryIds}`），成功后刷新缓存。
- **详情页 `/vouchers/:id`**：打印版记账凭证（凭证号、借贷分录、研发归集、风险标签、签署区），`window.print()` 配合 `@media print` 样式；研发项目/费用类型名称翻译失败时静默降级为 ID。

---

## 4. 报表、税务与研发

### 4.1 报表中心 `/reports`

- **API**：`POST /api/report/monthly`（月度简报）、`GET /api/tax-report/xiaoqiye/month-quarter/export`（XLS blob）。
- **交互**：月份选择（近 12 个月）+ 独立的税务导出「月报/季报」期次选择器（与浏览月份解耦，避免误导）；ECharts 横向条形图展示支出分类占比；PDF 导出 = html2canvas + jsPDF 截图摘要卡（`utils/export.ts`）；卡片点击跳转各子报表。

### 4.2 子报表页（统一「返回 + 期间选择器 + 只读展示」结构）

| 页面 | API（均 POST /api/report/...） | 特色交互 |
|---|---|---|
| health | `/health-score`、`/ai-diagnosis` | ECharts 仪表盘总分 + 5 维条形图；AI 点评按需生成 |
| vat-assist | `/vat-assist` | 月/季切换；申报表字段一键复制（单个/全部）；起征点与阻断提示 |
| cit-assist | `/cit-assist` | 季度选择 + 成本/利润/资产损失三个确认项（勾选后重算）；跳 `/mine/companies` 完善税务信息 |
| archive | `/archive-checklist` | 合规状态标签统计卡 |
| tax-summary | `/tax-summary` | 季度 4 格统计 + 申报截止日状态 + 阻断告警 |
| balance-sheet / cash-flow | `/balance-sheet`、`/cash-flow` | 原生表格，无图表 |
| income-statement | `/income-statement` | `viewMode: summary/quarterly` 总表/季报切换，多级科目缩进 |
| comparison | `/comparison` | 唯一 NDataTable 报表页；环比/同比，变动比例着色 |
| general-ledger / sub-ledger | GET `/api/ledger/general`、`/api/ledger/sub-ledger` | 三栏式账页；明细账含对方科目；科目下拉前端硬编码（含 5301xx 研发明细）；月份为手输 YYYY-MM |
| ar-aging / ap-aging | `/ar-aging`、`/ap-aging` | 孪生页，账龄桶分组卡片，顶部 Radio 互跳应收/应付 |

### 4.3 税务申报 `/tax-filing`

- **API**：`GET /api/tax-filing/packages/{period}`、`POST /api/tax-filing/items/{deadlineId}/file`（标记已申报/零申报，带 evidence）、XLS 导出复用小企业报表接口。
- **交互**：季度选择器；**顶层全局看板**（本期预计总税额免税/缴税大字、征期倒计时与待办办结进度环、免税红线紧凑监控槽、AI 税情简报胶囊、前置三方核验一致性状态条与差异就地展开）；**极简 3 步申报作业清单流**：
  - 任务 1「增值税小规模申报」：醒目展示**极速抄报手卡**（核心 1~2 个关键数值一键复制），完整栏次按需折叠展开，底部直接提供「已在电子税局申报，标记办结」；
  - 任务 2「企业所得税季度预缴」：A200000 核心数值手卡（收入、利润、5%小微预缴税额）+ 完整行次折叠 + 标记办结；
  - 任务 3「财务报表报送与附税」：一键导出税局标准 XLS 模板 + 标记报送完成，附税事项清单支持无税源快速核销。
  彻底消除传统层叠仪表盘的认知过载，实现从上到下线性顺畅办结。

### 4.4 研发费用 `/rd-expenses`

- **API**：`/api/rd/projects|expense-types|ledger|unallocated|reconciliation-summary|evidence` 全套；未归集更正 `POST /api/rd/unallocated/{entryId}/correct` 带 **Idempotency-Key 头**，已过账项自动生成冲销凭证。
- **交互**：年度区间 + 项目筛选；三套表单弹窗（新建项目 / 未归集分摊更正——多项目拆分需填分摊依据且合计必须等于原金额 / 证据登记）；勾稽摘要展示研发台账与总账差异。

---

## 5. 资金、结账与资产

### 5.1 月度结账向导 `/closing-wizard`（最复杂页面，5 步 NSteps）

1. **凭证导入**：发票 XLSX 前端解析（数电格式 + 模糊列名、红冲负数）→ parse 校验 → import；支持「仅归档不记账」。
2. **银行流水导入**：银行 Excel 自动定位表头（评分制）、列名映射收入/支出、过滤合计行、多格式日期归一 → `POST /api/bank/import-json`；微信账单走 parse → 预览（可删行/滤重）→ import。
3. **工资发放确认**：读 `GET /api/salary/sheets/{month}`，计提/发放各生成凭证；有在职员工未完成则阻断下一步。
4. **智能对账分类**：`POST /api/bank/ai-suggest` 生成建议 + 人工匹配（支持多笔账目合并核销 `matched_entry_ids`，微信结算单命中已入账时后端返回 409 `EXISTING_WECHAT_ENTRIES` 并给出候选账目组，前端引导合并核销原收入与手续费）；无票流水可 AI 分类（`classify-statement`）后 type=new 记账并对账；研发支出强制选项目/费用类别；健康度检查断链/金额/方向/重复风险。**交互优化**：Step 4 自动扩展宽屏（1240px），选中流水/账目时自动双向高亮「金额吻合」候选对；生成的结算凭证支持点击「查看结算凭证」在**向导内弹窗就地查看借贷分录与一键过账**，无需跳离向导。
5. **合规检查与关账**：前置校验（工资完成、零申报勾选 `confirmZeroActivity`、高风险对账、canClose 或「可批量过账」）→ `POST /api/periods/{month}/close`（带 **Idempotency-Key: close-{month}**）。**交互优化**：检查项凭证与未过账任务支持**向导内就地弹窗审阅/立即过账**以及**一键批量过账本月凭证**，彻底杜绝跳往 `/vouchers` 造成的流程断层；后端在同一事务内批量过账本期可过账草稿再关账，任一失败整体回滚；成功提示「已批量过账 N 张凭证并完成结账」，失败按 `CLOSE_CHECK_FAILED`（回滚提示）/ `REVIEW_REQUIRED`（需复核人审批）区分文案；成功后刷新账目缓存并拉利润表 + AI 诊断展示 CFO 报告；readiness 检查项可点击跳转对应步骤。

- 已结账月份（`periodStatus==='closed'`）直接落到第 5 步只读展示。
- 内嵌 **ClosingAgentPanel**（AI 结账顾问）：SSE 流式对话，工具含 run_close_checks / list_unmatched / propose_match 等，可产生待审批动作卡。
- 核心接口：`GET/POST /api/periods/{month}/close-checks`（检查项读写）。

### 5.2 银行流水 `/bank`

- **API**：`GET /api/bank/statements`、`GET /api/bank/difference?month=`、`POST /api/bank/import`（CSV 粘贴）/ `import-json`（Excel）、`/api/bank/match`（规则匹配）、`/api/bank/ai-suggest` + `confirm-suggestion`（匹配确认与微信结算统一走后端 `bank_settlement_service`，产生结算凭证草稿需到凭证页过账）、`POST /api/bank/repair-effect`（差异修复，可能同时生成结算凭证草稿）、`DELETE /api/bank/statements/{id}`。
- **交互**：关键字/日期/收支/匹配状态多维筛选分页；差异报告展示余额勾稽与异常账银影响，可跳 `/ledger/{entryId}`；差异修复与建议确认的提示会区分「已生成结算凭证草稿，请前往凭证页过账」与「修复完成」。

### 5.3 固定资产 `/assets`

- **列表页**：统计卡（原值/累计折旧/净值/数量）+ 状态筛选 + 折旧进度条卡片；`POST /api/asset/depreciation/run?period=` 一键计提并弹结果（总额/处理数/跳过数/明细）。
- **创建页**：类别联动折旧年限（房屋 20 / 机器 10 / 器具 4 / 运输 3 / 电子 5 年）；残值率、研发归集（项目/使用比例/分摊依据，比例 >0 必填依据）；实时月折旧预览；成功回列表。

### 5.4 待办 `/todos`

- **API**：`GET /api/todos`、`PATCH /api/todos/{key}`（done/dismissed）。
- **交互**：统计卡（待处理/高优/已完成）+ 过滤 Tab；复选完成、忽略；按 meta 跳转——账目类 `/ledger/:id`、流水类 `/bank`、资产类 `/assets`。

### 5.5 周期账 `/recurring`

- 列表：NDataTable（频率标签、下次到期、启用态）；`POST /api/recurring/{id}/apply` 立即生成记账；删除走确认弹窗。
- 创建/编辑共用 create.vue：名称、频率（weekly~yearly）、下次到期、template_data 为 JSON 文本域（严格 JSON.parse 校验）。

### 5.6 老板私账 `/private-expenses`

- **列表**：汇总卡（笔数/收入/支出/可收回押金/可冲销金额与净额）+ 归属人 pill 筛选 + 分页表格；状态列区分押金可收回、收入可用/受限、支出无票/有票/补票冲抵（关联正式账目名）。
- **创建/编辑**：方向切换联动分类与票据状态；支出可选 `no_invoice/has_invoice/substitute`（substitute 需从正式支出账目中选择冲抵项）；收入可选资金可用/受限（保证金/待结算自动受限、不参与冲销）；归属人 tag 输入并记忆到 localStorage。

---

## 6. 首页与系统设置

### 6.1 工作台首页 `/`

Agent 驾驶舱式布局：问候 + 「AI 会计在线」徽章；全能输入栏（文字 → `/copilot?q=...&autoSend=1`；票据 → `POST /api/record/receipt` 识别后带结果跳 copilot）；Agent 待办草稿池（`GET /api/agent/actions?status=pending`，AgentActionCard 审批）；月度结账提醒条（close-checks，跳 `/closing-wizard`）；本月收支/利润统计 + 最近流水 + 待办预览双栏。

### 6.2 `/mine` 系列

| 页面 | 要点 |
|---|---|
| index | 个人中心总览，菜单跳各子页与 `/recurring`、`/bank` |
| companies | 公司列表/切换/新建/编辑（税号校验、纳税人类型、小微企业、CIT 预缴方式） |
| categories | 分类 CRUD，被流水引用的分类禁删 |
| accounts | 会计科目树（NTree + Tab 过滤），`/api/ledger/accounts*`；系统科目禁删 |
| opening | 期初余额 `GET/PUT /api/opening/balances`；XLSX 模板导入导出；已生成凭证则锁定；改动需手机验证码 **step-up**（`POST /api/auth/step-up`）；税务期初备忘存 localStorage |
| salary | 三 Tab：员工 CRUD / 月度工资表（查询/编辑/计提/发放/社保/个税）/ 研发薪酬分配弹窗 |
| audit-log | 审计日志只读分页表（`GET /api/audit/logs`） |

---

## 7. 全局交互模式约定

- **AI 动作审批**：AI 产生的一切入账操作先成为 pending `AgentActionItem`，经 `AgentActionCard` 人工批准/拒绝后才执行；批准接口支持批量剔除明细与逐行修正。全链路口径「AI 起草、人工确认」。
- **过账底线**：standard 凭证过账必须有有效原始业务来源（`source_transaction`，缺失/作废返回 409）；银行导入且摘要含微信的凭证过账前做疑似重复收入检查（`POSSIBLE_DUPLICATE_WECHAT_INCOME`，候选返回前端核对）；微信结算单已有收入/手续费账目时拦截重复核销（`EXISTING_WECHAT_ENTRIES`）。
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
未登录访问 / ──► /landing 官网 ──► /login（或一键试用直接进工作台）
登录页 ────────► ← 返回官网首页；登录成功 ──► / 工作台
首页输入栏 ──► /copilot?q=…&autoSend=1
全局搜索 ────► /copilot?q=… / 各详情页
报表中心 ────► 12 张子报表；cit-assist ──► /mine/companies
待办 ────────► /ledger/:id、/bank、/assets
账本列表 ────► /ledger/:id ──► 提交复核/过账/冲销/编辑
凭证列表 ────► /ledger/:id（待生成）、/vouchers/:id（已生成）
银行差异报告 ──► /ledger/:entryId；结算凭证草稿 ──► /vouchers 过账
结账提醒条 ──► /closing-wizard（含 ClosingAgentPanel AI 顾问）
旧记账页 ────► redirect /copilot
管理员 ───────► /admin（概览/用量/用户/公司/审计/运维）
```
