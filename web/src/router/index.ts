import { createRouter, createWebHistory } from 'vue-router'
import type { RouteRecordRaw } from 'vue-router'

const routes: RouteRecordRaw[] = [
  {
    path: '/login',
    name: 'Login',
    component: () => import('@/pages/auth/login.vue'),
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
      {
        path: 'record/text',
        name: 'RecordText',
        component: () => import('@/pages/record/text.vue'),
      },
      {
        path: 'record/receipt',
        name: 'RecordReceipt',
        component: () => import('@/pages/record/receipt.vue'),
      },
      {
        path: 'record/batch',
        name: 'RecordBatch',
        component: () => import('@/pages/record/batch.vue'),
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
})

// Navigation guard
router.beforeEach((to, _from, next) => {
  const token = localStorage.getItem('auth_token')
  if (to.meta.requiresAuth && !token) {
    next('/login')
    return
  }
  if (to.path === '/login' && token) {
    next('/')
    return
  }
  next()
})

export default router
