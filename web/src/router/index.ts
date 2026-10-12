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
        path: 'ledger',
        name: 'Ledger',
        component: () => import('@/pages/ledger/index.vue'),
      },
      {
        path: 'ledger/:id',
        name: 'LedgerDetail',
        component: () => import('@/pages/ledger/detail.vue'),
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
        component: () => import('@/pages/report/vat-assist.vue'),
      },
      {
        path: 'reports/cit',
        name: 'CitAssist',
        component: () => import('@/pages/report/cit-assist.vue'),
      },
      {
        path: 'reports/archive',
        name: 'ArchiveChecklist',
        component: () => import('@/pages/report/archive.vue'),
      },
      {
        path: 'reports/tax-summary',
        name: 'TaxSummary',
        component: () => import('@/pages/report/tax-summary.vue'),
      },
      {
        path: 'reports/general-ledger',
        name: 'GeneralLedger',
        component: () => import('@/pages/report/general-ledger.vue'),
      },
      {
        path: 'reports/sub-ledger',
        name: 'SubLedger',
        component: () => import('@/pages/report/sub-ledger.vue'),
      },
      {
        path: 'reports/ar-aging',
        name: 'ArAging',
        component: () => import('@/pages/report/ar-aging.vue'),
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
        component: () => import('@/pages/voucher/index.vue'),
      },
      {
        path: 'vouchers/:id',
        name: 'VoucherDetail',
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
        component: () => import('@/pages/mine/categories.vue'),
      },
      {
        path: 'mine/accounts',
        name: 'Accounts',
        component: () => import('@/pages/mine/accounts.vue'),
      },
      {
        path: 'mine/opening',
        name: 'Opening',
        component: () => import('@/pages/mine/opening.vue'),
      },
      {
        path: 'mine/salary',
        name: 'Salary',
        component: () => import('@/pages/mine/salary.vue'),
      },
      {
        path: 'recurring',
        name: 'Recurring',
        component: () => import('@/pages/recurring/index.vue'),
      },
      {
        path: 'recurring/create',
        name: 'RecurringCreate',
        component: () => import('@/pages/recurring/create.vue'),
      },
      {
        path: 'recurring/:id',
        name: 'RecurringEdit',
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
        component: () => import('@/pages/mine/audit-log.vue'),
      },
      {
        path: 'mine/mcp',
        name: 'McpTokens',
        component: () => import('@/pages/mine/mcp-tokens.vue'),
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
