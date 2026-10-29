/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import type { WorkspaceView } from '@/utils/workspace'

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
  '无形资产',
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
  workspace?: WorkspaceView[]
}

export type NavGroup = '日常' | '账务' | '周期' | '工具'
// 「设置」在老板视图里也要留着：税费种认定、个人设置都是老板自己要看的东西（公司信息通过顶部公司管理进入）
export type BossGroup = '每天' | '每月' | '复盘' | '设置'

export interface NavItem {
  path?: string
  label: string
  bossLabel?: string
  bossGroup?: BossGroup
  icon: string
  group: NavGroup
  workspace?: WorkspaceView[]
  children?: NavChildItem[]
}

// 按用户工作阶段分组：日常协同 → 账务处理 → 周期核算 → 低频工具。
// 不按系统模块堆功能，避免出现“财务工具”这类混合分类。
export const NAV_ITEMS: NavItem[] = [
  // 日常协同（以 AI 会计为核心）
  { path: '/', label: '工作台', bossLabel: '工作台', bossGroup: '每天', icon: 'ri-dashboard-3-line', group: '日常' },
  { path: '/copilot', label: 'AI 会计', bossGroup: '每天', icon: 'ri-sparkling-fill', group: '日常' },
  { path: '/todos', label: '待办', bossGroup: '每天', icon: 'ri-checkbox-circle-line', group: '日常' },
  { path: '/private-expenses', label: '小私账', bossLabel: '小私账', bossGroup: '每天', icon: 'ri-wallet-3-line', group: '日常', workspace: ['boss'] },

  // 账务处理（原始单据 → 流水 → 凭证 → 账簿）
  { path: '/ledger', label: '经营账本', icon: 'ri-book-open-line', group: '账务', workspace: ['accounting'] },
  { path: '/invoices', label: '发票台账', icon: 'ri-receipt-line', group: '账务', workspace: ['accounting'] },
  { path: '/bank', label: '银行流水', icon: 'ri-bank-line', group: '账务', workspace: ['accounting'] },
  {
    label: '凭证与账簿',
    icon: 'ri-file-list-3-line',
    group: '账务',
    workspace: ['accounting'],
    children: [
      { path: '/vouchers', label: '记账凭证', icon: 'ri-file-list-3-line' },
      // 会计账簿＝查数工作台（五本账共用左侧导航）；电子账簿＝整册签发与打印。两者是「查」和「出」的分工
      { path: '/ledger/books', label: '会计账簿', icon: 'ri-book-3-line' },
      { path: '/ledger/book', label: '电子账簿', icon: 'ri-book-2-line' },
      { path: '/reports/archive', label: '单据归档', icon: 'ri-archive-line' },
    ],
  },

  // 周期核算（月末结账与季末税报）
  { path: '/closing-wizard', label: '月度结账', bossGroup: '每月', icon: 'ri-calendar-check-line', group: '周期' },
  { path: '/tax-filing', label: '税务申报', bossLabel: '报税', bossGroup: '每月', icon: 'ri-file-shield-2-line', group: '周期' },
  { path: '/reports', label: '经营报表', bossLabel: '经营', bossGroup: '复盘', icon: 'ri-bar-chart-box-line', group: '周期' },
  // 项目利润不占导航位：入口收敛到「经营报表」页的项目利润卡片（老板/会计视图都有）。

  // 低频工具与设置（折叠收纳，保持主流程清爽）
  {
    label: '专项核算',
    icon: 'ri-stack-line',
    group: '工具',
    workspace: ['accounting'],
    children: [
      { path: '/assets', label: '长期资产', icon: 'ri-archive-line' },
      { path: '/rd-expenses', label: '研发费用', icon: 'ri-flask-line', workspace: ['accounting'] },
      { path: '/mine/salary', label: '薪酬管理', icon: 'ri-coins-line' },
      { path: '/private-expenses', label: '老板私账', icon: 'ri-wallet-3-line', workspace: ['accounting'] },
      { path: '/aggregation', label: '归集与分摊', icon: 'ri-node-tree', workspace: ['accounting'] },
    ],
  },
  {
    // 不再限制 workspace：老板视图默认首页，藏掉设置会让「税费种认定 / 上手引导」无从进入。
    // 组内仍然按 workspace 过滤，会计科目、期初、备份这类专业项在老板视图看不到。
    label: '设置',
    icon: 'ri-settings-4-line',
    group: '工具',
    bossGroup: '设置',
    children: [
      { path: '/tax-registration', label: '税费种认定', icon: 'ri-government-line' },
      { path: '/mine/accounts', label: '会计科目', icon: 'ri-node-tree', workspace: ['accounting'] },
      { path: '/mine/contacts', label: '往来单位', icon: 'ri-contacts-book-line', workspace: ['accounting'] },
      { path: '/mine/opening', label: '期初建账', icon: 'ri-calendar-check-line', workspace: ['accounting'] },
      { path: '/mine/backup', label: '备份与归档', icon: 'ri-archive-drawer-line', workspace: ['accounting'] },
      { path: '/mine/mcp', label: 'AI 连接（MCP）', icon: 'ri-plug-line', workspace: ['accounting'] },
      { path: '/mine', label: '个人设置', icon: 'ri-user-settings-line' },
    ],
  },
]

// AI 功能场景的中文标签：对应后端 llm_usage 埋点的 scene 字段（services/llm.py）。
// 个人设置页「AI 用量」与管理后台用量页共用，新增 scene 时两处一起生效。
export const AI_SCENE_LABELS: Record<string, string> = {
  agent_chat: 'AI 会计对话',
  copilot: '经营助手',
  invoice_ocr: '单据识别',
  classify: '智能分类',
  batch_split: '连续日志拆分',
  report: '报表洞察',
  bank_statement: '银行流水',
  invoice: '发票处理',
  tax_registration_ocr: '税费种认定识别',
  todo_advice: '待办建议',
  asr: '语音转写',
  other: '其他',
}

export function aiSceneLabel(scene: string): string {
  return AI_SCENE_LABELS[scene] ?? scene
}
