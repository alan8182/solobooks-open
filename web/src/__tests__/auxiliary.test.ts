/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import {
  AUXILIARY_KIND_OPTIONS,
  COMBINATION_PRESETS,
  accountsForAuxiliaryKind,
  formatAuxiliaryLineText,
  isAuxiliaryKind,
  isCombinationDim,
} from '@/utils/auxiliary'
import type { LedgerAccountOption } from '@/api/ledger'

describe('auxiliary helpers (A3 single source of truth)', () => {
  const sampleAccounts: LedgerAccountOption[] = [
    {
      code: '1122',
      name: '应收账款',
      type: '流动资产',
      parentCode: '资产类',
      level: 1,
      isLeaf: true,
      quantityEnabled: false,
      unit: null,
      auxiliaryKinds: ['partner'],
    },
    {
      code: '2241',
      name: '其他应付款',
      type: '流动负债',
      parentCode: '负债类',
      level: 1,
      isLeaf: true,
      quantityEnabled: false,
      unit: null,
      auxiliaryKinds: ['employee', 'partner'],
    },
    {
      code: '5602',
      name: '管理费用',
      type: '费用',
      parentCode: '损益-费用类',
      level: 1,
      isLeaf: false,
      quantityEnabled: false,
      unit: null,
      auxiliaryKinds: ['department', 'employee', 'project'],
    },
    {
      code: '3001',
      name: '实收资本',
      type: '所有者权益',
      parentCode: '权益类',
      level: 1,
      isLeaf: true,
      quantityEnabled: false,
      unit: null,
      auxiliaryKinds: ['partner'],
    },
    {
      code: '资产类',
      name: '资产类',
      type: '',
      parentCode: null,
      level: 0,
      isLeaf: false,
      quantityEnabled: false,
      unit: null,
      auxiliaryKinds: [],
    },
  ]

  it('filters accounts strictly by backend auxiliaryKinds (including 3001 and employee)', () => {
    expect(accountsForAuxiliaryKind(sampleAccounts, 'partner').map((a) => a.code)).toEqual([
      '1122',
      '2241',
      '3001',
    ])
    expect(accountsForAuxiliaryKind(sampleAccounts, 'employee').map((a) => a.code)).toEqual([
      '2241',
      '5602',
    ])
    expect(accountsForAuxiliaryKind(sampleAccounts, 'department').map((a) => a.code)).toEqual(['5602'])
    expect(accountsForAuxiliaryKind(sampleAccounts, 'project').map((a) => a.code)).toEqual(['5602'])
  })

  it('formats all four auxiliary dimensions consistently for snake_case and camelCase', () => {
    expect(
      formatAuxiliaryLineText({
        partner_name: '杭州甲客户',
        department_name: '研发部',
        project_name: '核心系统',
        employee_name: '张三',
      }),
    ).toBe('杭州甲客户 / 研发部 / 核心系统 / 张三')

    expect(
      formatAuxiliaryLineText({
        departmentName: '市场部',
        employeeName: '李四',
      }),
    ).toBe('市场部 / 李四')

    expect(formatAuxiliaryLineText(null)).toBe('')
  })

  it('exposes all 4 auxiliary kinds and non-duplicate combination presets', () => {
    expect(AUXILIARY_KIND_OPTIONS.map((o) => o.value)).toEqual([
      'partner',
      'department',
      'project',
      'employee',
    ])
    for (const preset of COMBINATION_PRESETS) {
      expect(preset.rowDim).not.toBe(preset.colDim)
      expect(isCombinationDim(preset.rowDim)).toBe(true)
      expect(isCombinationDim(preset.colDim)).toBe(true)
    }
    expect(isAuxiliaryKind('employee')).toBe(true)
    expect(isAuxiliaryKind('account')).toBe(false)
  })
})
