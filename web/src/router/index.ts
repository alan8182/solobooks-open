/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'
import { resolveAuthRedirect } from './guards'

export const routes: RouteRecordRaw[] = [
  {
    path: '/landing',
    name: 'Landing',
    component: () => import('@/pages/landing/index.vue'),
    meta: { requiresAuth: false },
  },
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/pages/auth/login.vue'),
    meta: { requiresAuth: false },
  },
  {
    // 协议类公开页面：登录页与备案信息均可直达
    path: '/agreement',
    name: 'Agreement',
    component: () => import('@/pages/agreement/service.vue'),
    meta: { requiresAuth: false },
  },
  {
    path: '/privacy',
    name: 'Privacy',
    component: () => import('@/pages/agreement/privacy.vue'),
    meta: { requiresAuth: false },
  },
  {
    // AI 连接中心（MCP 与 Agent Skills 完整实战指南，支持公开免登录访问与外部分享）
    //
    // 路径刻意避开 /mcp 前缀：那个前缀在生产环境被 nginx 整段代理给后端 MCP 端点
    // （deploy/nginx/default.conf 的 location /mcp），vite dev 的 proxy 同样按前缀匹配。
    // 任何 /mcp-xxx 形式的前端路由都会被转发到后端并 404，前端路由永远拿不到请求。
    path: '/ai-connect',
    alias: ['/docs/mcp-skills'],
    name: 'McpSkillsGuide',
    component: () => import('@/pages/mcp-guide/index.vue'),
    meta: { requiresAuth: false },
  },
  {
    // Solo Audit Share 协审链接：CPA 免注册只读体检视图（独立布局，不进 AppLayout）
    path: '/share/:token',
    name: 'AuditShareView',
    component: () => import('@/pages/share/index.vue'),
    meta: { requiresAuth: false },
  },
  {
    // 平台管理后台：独立于业务布局；管理员身份由 AdminLayout 调 /api/admin/overview 探测（403 拒绝），不落 localStorage
    path: '/admin',
    component: () => import('@/pages/admin/AdminLayout.vue'),
    meta: { requiresAuth: true },
    children: [
      {
        path: '',
        name: 'AdminOverview',
        component: () => import('@/pages/admin/AdminOverview.vue'),
      },
      {
        path: 'usage',
        name: 'AdminUsage',
        component: () => import('@/pages/admin/AdminUsage.vue'),
      },
      {
        path: 'users',
        name: 'AdminUsers',
        component: () => import('@/pages/admin/AdminUsers.vue'),
      },
      {
        path: 'companies',
        name: 'AdminCompanies',
        component: () => import('@/pages/admin/AdminCompanies.vue'),
      },
      {
        path: 'audit-logs',
        name: 'AdminAuditLogs',
        component: () => import('@/pages/admin/AdminAuditLogs.vue'),
      },
      {
        path: 'agent-audit',
        name: 'AdminAgentAudit',
        component: () => import('@/pages/admin/AdminAgentAudit.vue'),
      },
      {
        path: 'data-ops',
        name: 'AdminDataOps',
        component: () => import('@/pages/admin/AdminDataOps.vue'),
      },
    ],
  },
  {
    path: '/',
    component: () => import('@/pages/layout/AppLayout.vue'),
    meta: { requiresAuth: true },
    children: [
      {
        path: '',
        name: 'Home',
        component: () => import('@/pages/home/index.vue'),
      },
      {
        // 上手引导总页：首页清单就绪后折叠，想再看只能从这里进。
        // 不加 meta.workspace——老板与会计两个视图都要能进。
        path: 'guide',
        name: 'Guide',
        component: () => import('@/pages/guide/index.vue'),
      },
      {
        path: 'ledger',
        name: 'Ledger',
        component: () => import('@/pages/ledger/index.vue'),
      },
      {
        path: 'ledger/:id',
        name: 'LedgerDetail',
        component: () => import('@/pages/ledger/detail.vue'),
      },
      {
        // 电子账簿：封面 → 目录 → 科目余额表 → 明细账，连续排版后 window.print()
        path: 'ledger/book',
        name: 'LedgerBook',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/ledger/book.vue'),
      },
      {
        // 账簿工作台：五本账共用一个 path，靠 ?book= 切换内容。
        // 不用子路由是因为 KeepAlive 按路由 name 缓存，子路由会让五本账各占一个缓存壳、
        // 页内标签也建不出来；单 path 单 name 才能让「会计账簿」始终是一个标签。
        path: 'ledger/books',
        name: 'LedgerBooks',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/ledger/books.vue'),
      },
      {
        // 发票台账（P0 WP1）：进销项主数据，行点击直达凭证
        path: 'invoices',
        name: 'InvoiceLedger',
        component: () => import('@/pages/invoice/ledger.vue'),
      },
      // ponytail: consolidated scattered manual recording pages into the unified AI Agent workspace
      {
        path: 'record/text',
        name: 'RecordText',
        redirect: '/copilot',
      },
      {
        path: 'record/receipt',
        name: 'RecordReceipt',
        redirect: '/copilot?mode=receipt',
      },
      {
        path: 'record/batch',
        name: 'RecordBatch',
        redirect: '/copilot',
      },
      {
        path: 'record/invoice-import',
        name: 'InvoiceImport',
        component: () => import('@/pages/record/invoice-import.vue'),
      },
      {
        path: 'reports',
        name: 'Reports',
        component: () => import('@/pages/report/index.vue'),
      },
      {
        path: 'rd-expenses',
        name: 'RdExpenses',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/rd/index.vue'),
      },
      {
        path: 'reports/health',
        name: 'HealthScore',
        component: () => import('@/pages/report/health.vue'),
      },
      {
        path: 'reports/vat',
        name: 'VatAssist',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/report/vat-assist.vue'),
      },
      {
        path: 'reports/cit',
        name: 'CitAssist',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/report/cit-assist.vue'),
      },
      {
        path: 'reports/archive',
        name: 'ArchiveChecklist',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/report/archive.vue'),
      },
      // 这五本账已并入账簿工作台（/ledger/books?book=...）。旧地址保留为重定向，
      // 首页快捷入口、资产负债表下钻、报表中心 chip 的链接都不必逐个改。
      // meta.workspace 要留在重定向记录上：router.resolve() 不执行 redirect，
      // 少了它任何按 meta 判断「老板视图该不该看到」的地方都会漏判。
      {
        path: 'reports/general-ledger',
        name: 'GeneralLedger',
        meta: { workspace: ['accounting'] },
        redirect: (to) => ({ path: '/ledger/books', query: { ...to.query, book: 'general-ledger' } }),
      },
      {
        path: 'reports/sub-ledger',
        name: 'SubLedger',
        meta: { workspace: ['accounting'] },
        redirect: (to) => ({ path: '/ledger/books', query: { ...to.query, book: 'sub-ledger' } }),
      },
      {
        path: 'reports/chronological',
        name: 'ChronologicalLedger',
        meta: { workspace: ['accounting'] },
        redirect: (to) => ({ path: '/ledger/books', query: { ...to.query, book: 'chronological' } }),
      },
      {
        path: 'reports/multi-column',
        name: 'MultiColumnLedger',
        meta: { workspace: ['accounting'] },
        redirect: (to) => ({ path: '/ledger/books', query: { ...to.query, book: 'multi-column' } }),
      },
      {
        path: 'reports/equity-changes',
        name: 'EquityChanges',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/report/equity-changes.vue'),
      },
      {
        path: 'reports/ar-aging',
        name: 'ArAging',
        component: () => import('@/pages/report/ar-aging.vue'),
      },
      {
        // 核算项目余额表：按往来单位汇总往来科目，已并入账簿工作台「辅助账」
        path: 'reports/partner-balances',
        name: 'PartnerBalances',
        meta: { workspace: ['accounting'] },
        redirect: (to) => ({ path: '/ledger/books', query: { ...to.query, book: 'partner-balances' } }),
      },
      {
        // 垫付台账：按付款方归集老板/员工替公司垫的钱与未报余额
        path: 'reports/advance-balances',
        name: 'AdvanceBalances',
        component: () => import('@/pages/report/advance-balances.vue'),
      },
      {
        path: 'reports/ap-aging',
        name: 'ApAging',
        component: () => import('@/pages/report/ap-aging.vue'),
      },
      {
        path: 'reports/comparison',
        name: 'Comparison',
        component: () => import('@/pages/report/comparison.vue'),
      },
      {
        path: 'reports/balance-sheet',
        name: 'BalanceSheet',
        component: () => import('@/pages/report/balance-sheet.vue'),
      },
      {
        path: 'reports/income-statement',
        name: 'IncomeStatement',
        component: () => import('@/pages/report/income-statement.vue'),
      },
      {
        path: 'reports/cash-flow',
        name: 'CashFlow',
        component: () => import('@/pages/report/cash-flow.vue'),
      },
      {
        path: 'vouchers',
        name: 'Vouchers',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/voucher/index.vue'),
      },
      {
        // 凭证连续打印：按期间或勾选 id 生成打印视图，每张凭证一页
        path: 'vouchers/print',
        name: 'VoucherPrint',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/voucher/print.vue'),
      },
      {
        path: 'vouchers/:id',
        name: 'VoucherDetail',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/voucher/detail.vue'),
      },
      {
        path: 'assets',
        name: 'Assets',
        component: () => import('@/pages/asset/index.vue'),
      },
      {
        path: 'assets/create',
        name: 'AssetCreate',
        component: () => import('@/pages/asset/create.vue'),
      },
      {
        path: 'assets/:id',
        name: 'AssetDetail',
        component: () => import('@/pages/asset/detail.vue'),
      },
      {
        path: 'intangibles',
        name: 'Intangibles',
        component: () => import('@/pages/intangible/index.vue'),
      },
      {
        path: 'intangibles/create',
        name: 'IntangibleCreate',
        component: () => import('@/pages/intangible/create.vue'),
      },
      {
        path: 'intangibles/:id',
        name: 'IntangibleDetail',
        component: () => import('@/pages/intangible/detail.vue'),
      },
      {
        path: 'todos',
        name: 'Todos',
        component: () => import('@/pages/todo/index.vue'),
      },
      {
        path: 'copilot',
        name: 'Copilot',
        component: () => import('@/pages/copilot/index.vue'),
      },
      {
        path: 'tax-filing',
        name: 'TaxFiling',
        component: () => import('@/pages/tax-filing/index.vue'),
      },
      {
        // 税费种认定：把电子税务局的认定表搬进系统，征期日历按它生成
        path: 'tax-registration',
        name: 'TaxRegistration',
        component: () => import('@/pages/tax-registration/index.vue'),
      },
      {
        path: 'mine',
        name: 'Mine',
        component: () => import('@/pages/mine/index.vue'),
      },
      {
        path: 'mine/companies',
        name: 'Companies',
        component: () => import('@/pages/mine/companies.vue'),
      },
      {
        path: 'mine/categories',
        name: 'Categories',
        redirect: '/mine/accounts',
      },
      {
        path: 'mine/accounts',
        name: 'Accounts',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/mine/accounts.vue'),
      },
      {
        path: 'mine/opening',
        name: 'Opening',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/mine/opening.vue'),
      },
      {
        // 辅助核算档案（P0 WP2）：往来单位 / 部门 / 项目
        path: 'mine/contacts',
        name: 'AuxiliaryContacts',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/mine/contacts.vue'),
      },
      {
        // 旧账导入（P0 WP4）：科目余额表 → 期初 / 凭证分录 → 历史凭证
        path: 'mine/legacy-import',
        name: 'LegacyImport',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/mine/legacy-import.vue'),
      },
      {
        // 备份与归档（P0 WP5）
        path: 'mine/backup',
        name: 'BackupArchive',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/mine/backup.vue'),
      },
      {
        // 专家协审（Solo Audit Share）：生成/撤销协审链接 + CPA 批注回流处理
        path: 'mine/audit-share',
        name: 'AuditShareManage',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/mine/audit-share.vue'),
      },
      {
        path: 'mine/salary',
        name: 'Salary',
        component: () => import('@/pages/mine/salary.vue'),
      },
      {
        path: 'recurring',
        name: 'Recurring',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/recurring/index.vue'),
      },
      {
        path: 'recurring/create',
        name: 'RecurringCreate',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/recurring/create.vue'),
      },
      {
        path: 'recurring/:id',
        name: 'RecurringEdit',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/recurring/create.vue'),
      },
      {
        path: 'private-expenses',
        name: 'PrivateExpenses',
        component: () => import('@/pages/private-expense/index.vue'),
      },
      {
        path: 'private-expenses/create',
        name: 'PrivateExpenseCreate',
        component: () => import('@/pages/private-expense/create.vue'),
      },
      {
        path: 'private-expenses/:id',
        name: 'PrivateExpenseEdit',
        component: () => import('@/pages/private-expense/create.vue'),
      },
      {
        path: 'mine/audit-log',
        name: 'AuditLog',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/mine/audit-log.vue'),
      },
      {
        path: 'mine/mcp',
        name: 'McpTokens',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/mine/mcp-tokens.vue'),
      },
      {
        // 通用归集（T01 建路由与占位页，页面实现在 T02–T05）
        path: 'aggregation',
        name: 'Aggregation',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/aggregation/index.vue'),
      },
      {
        path: 'aggregation/dimensions',
        name: 'AggregationDimensions',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/aggregation/dimensions.vue'),
      },
      {
        path: 'aggregation/rules',
        name: 'AggregationRules',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/aggregation/rules.vue'),
      },
      {
        path: 'aggregation/pending',
        name: 'AggregationPending',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/aggregation/pending.vue'),
      },
      {
        path: 'aggregation/report',
        name: 'AggregationReport',
        component: () => import('@/pages/aggregation/report.vue'),
      },
      {
        path: 'aggregation/report-line-rules',
        name: 'AggregationLineRules',
        meta: { workspace: ['accounting'] },
        component: () => import('@/pages/aggregation/report-line-rules.vue'),
      },
      {
        path: 'bank',
        name: 'Bank',
        component: () => import('@/pages/bank/index.vue'),
      },
      {
        path: 'closing-wizard',
        name: 'ClosingWizard',
        component: () => import('@/pages/closing-wizard/index.vue'),
      },
      {
        // 兜底 404：地址写错不该只剩一个空壳布局，要明确告诉用户并给一条出路
        path: ':pathMatch(.*)*',
        name: 'NotFound',
        component: () => import('@/pages/NotFound.vue'),
      },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  // 官网锚点导航平滑滚动，顶部预留吸顶导航高度
  scrollBehavior(to) {
    if (to.hash) return { el: to.hash, top: 72, behavior: 'smooth' }
    return { top: 0 }
  },
})

// Navigation guard
// ponytail: unauthenticated root access directs to landing; deep protected routes to login; logged-in users go to home workbench
router.beforeEach((to, _from, next) => {
  const redirect = resolveAuthRedirect(to, localStorage.getItem('auth_token'))
  if (redirect) {
    next(redirect)
    return
  }
  next()
})

export default router
