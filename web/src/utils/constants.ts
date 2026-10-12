export const DIRECTIONS = [
  { value: 'income', label: '收入' },
  { value: 'expense', label: '支出' },
  { value: 'owner_transfer', label: '资本与往来' },
] as const

export const PAYER_OPTIONS = [
  { value: 'company_account', label: '公司账户' },
  { value: 'boss_personal', label: '股东个人' },
] as const

export const BILLING_STATUS = [
  { value: 'has_invoice', label: '有发票' },
  { value: 'has_receipt', label: '有收据' },
  { value: 'no_document', label: '无票据' },
] as const

export const EXPENSE_CATEGORIES = [
  '餐饮',
  '交通',
  '办公',
  '业务招待',
  '差旅',
  '研发支出-费用化支出',
  '房租物业',
  '人力成本',
  '设备采购',
  '原材料采购',
  '商品采购',
  '推广费',
  '信息技术服务费',
  '运费及物流',
  '水电通讯费',
  '银行及金融手续费',
  '税费缴纳',
  '员工福利',
  '生产成本',
  '预付账款',
  '固定资产',
  '其他支出',
] as const

export const INCOME_CATEGORIES = [
  '主营业务收入',
  '其他业务收入',
  '营业外收入',
  '利息收入',
] as const

export const OWNER_TRANSFER_CATEGORIES = [
  '股东投入',
  '股东垫付',
  '股东取现',
  '资本公积',
  '股东垫付报销',
  '员工垫付报销',
  '其他应收款',
  '其他应付款',
  '往来款',
] as const

/** @deprecated 用 getCategoriesByDirection(direction) 替代 */
export const categoryOptions = [
  ...EXPENSE_CATEGORIES,
  ...INCOME_CATEGORIES,
  ...OWNER_TRANSFER_CATEGORIES,
]

export function getCategoriesByDirection(direction: string): readonly string[] {
  if (direction === 'income') return INCOME_CATEGORIES
  if (direction === 'owner_transfer') return OWNER_TRANSFER_CATEGORIES
  return EXPENSE_CATEGORIES
}

export const PRIORITY_LABELS: Record<string, { label: string; color: string }> = {
  high: { label: '紧急', color: '#ef4444' },
  medium: { label: '重要', color: '#f59e0b' },
  low: { label: '普通', color: '#94a3b8' },
}

export interface NavChildItem {
  path: string
  label: string
  icon: string
}

export interface NavItem {
  path?: string
  label: string
  icon: string
  group: '日常' | '周期' | '管理'
  children?: NavChildItem[]
}

// ponytail: streamlined from 20+ scattered entries into 3 temporal groups: Daily, Periodic, Admin
export const NAV_ITEMS: NavItem[] = [
  // 日常协同（以 AI 会计为核心）
  { path: '/', label: '工作台', icon: 'ri-dashboard-3-line', group: '日常' },
  { path: '/copilot', label: 'AI 会计', icon: 'ri-sparkling-fill', group: '日常' },
  { path: '/ledger', label: '经营账本', icon: 'ri-book-open-line', group: '日常' },
  { path: '/todos', label: '待办事项', icon: 'ri-checkbox-circle-line', group: '日常' },

  // 周期核算（月末结账与季末税报）
  { path: '/closing-wizard', label: '月度结账', icon: 'ri-calendar-check-line', group: '周期' },
  { path: '/tax-filing', label: '税务申报', icon: 'ri-file-shield-2-line', group: '周期' },
  { path: '/reports', label: '经营报表', icon: 'ri-bar-chart-box-line', group: '周期' },

  // 底层工具与设置（二级收纳，保持侧栏简约）
  {
    label: '财务工具',
    icon: 'ri-briefcase-4-line',
    group: '管理',
    children: [
      { path: '/record/invoice-import', label: '发票管理', icon: 'ri-receipt-line' },
      { path: '/bank', label: '银行流水', icon: 'ri-bank-line' },
      { path: '/vouchers', label: '记账凭证', icon: 'ri-file-list-3-line' },
      { path: '/assets', label: '固定资产', icon: 'ri-archive-line' },
      { path: '/rd-expenses', label: '研发费用', icon: 'ri-flask-line' },
      { path: '/mine/salary', label: '薪酬管理', icon: 'ri-coins-line' },
      { path: '/private-expenses', label: '小私账', icon: 'ri-wallet-3-line' },
    ],
  },
  {
    label: '系统设置',
    icon: 'ri-settings-4-line',
    group: '管理',
    children: [
      { path: '/mine', label: '设置总览', icon: 'ri-dashboard-line' },
      { path: '/mine/companies', label: '公司信息', icon: 'ri-building-line' },
      { path: '/mine/categories', label: '分类管理', icon: 'ri-price-tag-3-line' },
      { path: '/mine/accounts', label: '会计科目', icon: 'ri-node-tree' },
      { path: '/mine/opening', label: '期初设置', icon: 'ri-calendar-check-line' },
      { path: '/mine/mcp', label: 'AI 连接（MCP）', icon: 'ri-plug-line' },
      { path: '/mine/audit-log', label: '审计日志', icon: 'ri-file-history-line' },
    ],
  },
]
