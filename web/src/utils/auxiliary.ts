/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import type { AuxiliaryKind } from '@/api/auxiliary'
import type { CombinationDim, CombinationMetric, LedgerAccountOption } from '@/api/ledger'

export const UNASSIGNED_AUX_ITEM_ID = '__unassigned__'

export const AUXILIARY_KIND_META: Record<
  AuxiliaryKind,
  { label: string; shortLabel: string; unassignedLabel: string }
> = {
  partner: { label: '往来单位', shortLabel: '往来', unassignedLabel: '未指定往来单位' },
  department: { label: '部门', shortLabel: '部门', unassignedLabel: '未指定部门' },
  project: { label: '项目', shortLabel: '项目', unassignedLabel: '未指定项目' },
  employee: { label: '职员', shortLabel: '职员', unassignedLabel: '未指定职员' },
}

export const AUXILIARY_KIND_OPTIONS: { label: string; value: AuxiliaryKind }[] = [
  { label: '往来单位', value: 'partner' },
  { label: '部门', value: 'department' },
  { label: '项目', value: 'project' },
  { label: '职员', value: 'employee' },
]

export const COMBINATION_DIM_OPTIONS: { label: string; value: CombinationDim }[] = [
  { label: '会计科目', value: 'account' },
  { label: '往来单位', value: 'partner' },
  { label: '部门', value: 'department' },
  { label: '项目', value: 'project' },
  { label: '职员', value: 'employee' },
]

export const COMBINATION_METRIC_OPTIONS: { label: string; value: CombinationMetric }[] = [
  { label: '本期净发生', value: 'net' },
  { label: '本期借方', value: 'debit' },
  { label: '本期贷方', value: 'credit' },
  { label: '期末余额', value: 'closing' },
]

export interface CombinationPreset {
  key: string
  label: string
  rowDim: CombinationDim
  colDim: CombinationDim
  metric: CombinationMetric
  accountCode?: string
}

export const COMBINATION_PRESETS: CombinationPreset[] = [
  { key: 'dept-project', label: '部门 × 项目', rowDim: 'department', colDim: 'project', metric: 'debit' },
  { key: 'acct-dept', label: '科目 × 部门', rowDim: 'account', colDim: 'department', metric: 'debit' },
  { key: 'acct-project', label: '科目 × 项目', rowDim: 'account', colDim: 'project', metric: 'debit' },
  { key: 'acct-employee', label: '科目 × 职员', rowDim: 'account', colDim: 'employee', metric: 'debit' },
  { key: 'partner-acct', label: '往来单位 × 科目', rowDim: 'partner', colDim: 'account', metric: 'closing' },
]

/** 根据后端下发的 auxiliaryKinds 过滤科目；不做前端硬编码第二真值。 */
export function accountsForAuxiliaryKind(
  accounts: LedgerAccountOption[],
  kind: AuxiliaryKind,
): LedgerAccountOption[] {
  return accounts
    .filter((acct) => acct.level > 0 && (acct.auxiliaryKinds || []).includes(kind))
    .sort((a, b) => a.code.localeCompare(b.code))
}

/** 统一格式化分录行的辅助核算文本（兼容 snake_case 与 camelCase）。 */
export function formatAuxiliaryLineText(aux: Record<string, unknown> | null | undefined): string {
  if (!aux || typeof aux !== 'object') return ''
  const parts = [
    aux.partner_name ?? aux.partnerName,
    aux.department_name ?? aux.departmentName,
    aux.project_name ?? aux.projectName,
    aux.employee_name ?? aux.employeeName,
  ]
    .map((v) => String(v || '').trim())
    .filter(Boolean)
  return parts.join(' / ')
}

export function isAuxiliaryKind(value: unknown): value is AuxiliaryKind {
  return value === 'partner' || value === 'department' || value === 'project' || value === 'employee'
}

export function isCombinationDim(value: unknown): value is CombinationDim {
  return value === 'account' || isAuxiliaryKind(value)
}
