// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0.
import { describe, it, expect } from 'vitest'
import {
  buildTodoDisplayRows,
  extractFilingPeriod,
  extractTaxName,
  getPeriodBundles,
} from '@/utils/todoAction'
import type { TodoItem } from '@/api/types'

function filingTodo(period: string, name: string, desc: string, key?: string): TodoItem {
  return {
    id: key || `${period}-${name}`,
    key: key || `${period}-${name}`,
    type: 'filing',
    title: `逾期未申报 - ${name}（${period}）`,
    desc,
    priority: 'high',
    done: false,
    dismissed: false,
    meta: { ruleId: 'filing_deadline', deadlineId: key || `${period}-${name}` },
  }
}

function otherTodo(key: string): TodoItem {
  return {
    id: key,
    key,
    type: 'receipt',
    title: '办公用品采购 - 补充票据',
    priority: 'high',
    done: false,
    dismissed: false,
    meta: { ruleId: 'missing_document' },
  }
}

describe('Todo Page UI Enhancements', () => {
  it('correctly extracts tax period from todo titles including nested parentheses', () => {
    expect(extractFilingPeriod('逾期未申报 - 增值税及附加税费（2026-Q3）')).toBe('2026-Q3')
    expect(extractFilingPeriod('逾期未申报 - 企业所得税预缴（A 类）（2026-Q3）')).toBe('2026-Q3')
    expect(extractFilingPeriod('征期临近 - 个税扣缴（2026-10）')).toBe('2026-10')
    expect(extractFilingPeriod('同步税费种认定表')).toBe('本期')
  })

  it('correctly extracts clean tax names for compact bundle tags', () => {
    expect(extractTaxName('逾期未申报 - 增值税及附加税费（2026-Q3）')).toBe('增值税及附加税费')
    expect(extractTaxName('逾期未申报 - 企业所得税预缴（A 类）（2026-Q3）')).toBe('企业所得税预缴（A 类）')
    expect(extractTaxName('征期临近 - 个税扣缴（2026-10）')).toBe('个税扣缴')
  })

  it('聚合同一征期的申报待办，不足两项的仍按单卡展示', () => {
    const { bundles, singles } = getPeriodBundles([
      filingTodo('2026-10', '社保费申报缴纳', '已逾期 39 天，截止 2026-10-15', 'oct-ss'),
      filingTodo('2026-10', '个税扣缴', '已逾期 39 天，截止 2026-10-15', 'oct-iit'),
      filingTodo('2026-11', '增值税及附加税费', '剩 3 天，截止 2026-11-15', 'nov-vat'),
      otherTodo('receipt-1'),
    ])

    expect(bundles).toHaveLength(1)
    expect(bundles[0].period).toBe('2026-10')
    expect(bundles[0].items.map((i) => i.key)).toEqual(['oct-ss', 'oct-iit'])
    expect(bundles[0].isOverdue).toBe(true)
    expect(bundles[0].statusLabel).toBe('已逾期 39 天')
    // 非申报类待办先入 singles，只有一项的征期在其后展开（保持原有顺序）
    expect(singles.map((i) => i.key)).toEqual(['receipt-1', 'nov-vat'])
  })

  it('折叠时只渲染聚合卡本身，不渲染其明细', () => {
    const rows = buildTodoDisplayRows(
      [
        filingTodo('2026-10', '社保费申报缴纳', '已逾期 39 天，截止 2026-10-15', 'oct-ss'),
        filingTodo('2026-10', '个税扣缴', '已逾期 39 天，截止 2026-10-15', 'oct-iit'),
        filingTodo('2026-Q3', '增值税及附加税费', '已逾期 32 天，截止 2026-10-22', 'q3-vat'),
        filingTodo('2026-Q3', '企业所得税预缴（A 类）', '已逾期 32 天，截止 2026-10-22', 'q3-cit'),
        otherTodo('receipt-1'),
      ],
      new Set(),
    )

    expect(rows.map((r) => r.key)).toEqual(['bundle:2026-10', 'bundle:2026-Q3', 'receipt-1'])
  })

  it('展开的明细就地跟在自己的聚合卡下面，不会挂到下一张聚合卡', () => {
    const rows = buildTodoDisplayRows(
      [
        filingTodo('2026-10', '社保费申报缴纳', '已逾期 39 天，截止 2026-10-15', 'oct-ss'),
        filingTodo('2026-10', '个税扣缴', '已逾期 39 天，截止 2026-10-15', 'oct-iit'),
        filingTodo('2026-Q3', '增值税及附加税费', '已逾期 32 天，截止 2026-10-22', 'q3-vat'),
        filingTodo('2026-Q3', '企业所得税预缴（A 类）', '已逾期 32 天，截止 2026-10-22', 'q3-cit'),
        otherTodo('receipt-1'),
      ],
      new Set(['2026-10']),
    )

    expect(rows.map((r) => r.key)).toEqual([
      'bundle:2026-10',
      'oct-ss',
      'oct-iit',
      'bundle:2026-Q3',
      'receipt-1',
    ])
    expect(rows[0].meta?.bundle?.period).toBe('2026-10')
  })

  it('两张聚合卡各自展开互不串位，收起后明细随之消失', () => {
    const items = [
      filingTodo('2026-10', '社保费申报缴纳', '截止 2026-10-15', 'oct-ss'),
      filingTodo('2026-10', '个税扣缴', '截止 2026-10-15', 'oct-iit'),
      filingTodo('2026-Q3', '增值税及附加税费', '截止 2026-10-22', 'q3-vat'),
      filingTodo('2026-Q3', '企业所得税预缴（A 类）', '截止 2026-10-22', 'q3-cit'),
    ]

    const bothOpen = buildTodoDisplayRows(items, new Set(['2026-10', '2026-Q3']))
    expect(bothOpen.map((r) => r.key)).toEqual([
      'bundle:2026-10', 'oct-ss', 'oct-iit',
      'bundle:2026-Q3', 'q3-vat', 'q3-cit',
    ])

    const onlyQ3 = buildTodoDisplayRows(items, new Set(['2026-Q3']))
    expect(onlyQ3.map((r) => r.key)).toEqual([
      'bundle:2026-10',
      'bundle:2026-Q3', 'q3-vat', 'q3-cit',
    ])
  })
})
