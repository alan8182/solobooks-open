/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// 税费种认定解析 / 展示纯函数。重点锁三件事：
//  ① 从税局页面复制出来的 TSV（可能带序号列、表头、合计行）能正确落位；
//  ② 征收率/税率的百分比展示不能把 0.00025 显示成 0.03% 以外的怪数；
//  ③ 属期标签（2026-Q3 / 2026-09 / 2026）不能互相串。

import { describe, expect, it } from 'vitest'
import {
  TAX_REGISTRATION_COLUMNS,
  cycleLabel,
  deadlineStatusText,
  deadlineStatusType,
  formatRate,
  formatTaxRate,
  groupRowsByReturnGroup,
  monthLabel,
  parsePastedTable,
  periodLabel,
  summarizeImport,
} from '@/utils/taxRegistration'

describe('parsePastedTable', () => {
  it('解析带表头与序号列的税局表格', () => {
    const text = [
      '序号\t征收项目\t征收品目\t征收子目\t纳税期限\t申报期限\t征收率\t税率或单位税额\t主附税标志\t征收代理方式',
      '1\t增值税\t商业(3%)\t\t季\t期满之日起15日内\t0.03\t0.13\t主税\t自行申报',
      '2\t印花税\t营业账簿\t\t年\t期满之日起15日内\t0.03\t0.00025\t主税\t自行申报',
    ].join('\n')

    const result = parsePastedTable(text)

    expect(result.rows).toHaveLength(2)
    expect(result.skipped).toBe(1)
    expect(result.rows[0]).toMatchObject({
      序号: '1',
      征收项目: '增值税',
      征收品目: '商业(3%)',
      纳税期限: '季',
      申报期限: '期满之日起15日内',
      征收率: '0.03',
      '税率或单位税额': '0.13',
      主附税标志: '主税',
      征收代理方式: '自行申报',
    })
    expect(result.rows[1]['征收子目']).toBe('')
  })

  it('没有序号列时第一列就是征收项目', () => {
    const text = '地方教育附加\t增值税地方教育附加\t\t季\t期满之日起15日内\t0\t0.02\t增值税附税\t自行申报'
    const result = parsePastedTable(text)

    expect(result.rows).toHaveLength(1)
    expect(result.rows[0]['征收项目']).toBe('地方教育附加')
    expect(result.rows[0]['序号']).toBe('')
  })

  it('跳过合计行与不完整行，不做静默错位', () => {
    const text = [
      '合计\t\t\t\t\t\t\t\t',
      '3\t教育费附加\t增值税教育费附加\t\t季\t期满之日起15日内\t0\t0.03\t增值税附税\t自行申报',
      '残缺行',
    ].join('\n')

    const result = parsePastedTable(text)

    expect(result.rows).toHaveLength(1)
    expect(result.rows[0]['征收项目']).toBe('教育费附加')
    expect(result.skipped).toBe(2)
  })

  it('从 PDF 复制的多空格内容也能切分', () => {
    const text = '1  城市维护建设税  市区（增值税附征）   季  期满之日起15日内  0  0.07  增值税附税  自行申报'
    const result = parsePastedTable(text)

    expect(result.rows).toHaveLength(1)
    expect(result.rows[0]['征收项目']).toBe('城市维护建设税')
    expect(result.rows[0]['纳税期限']).toBe('季')
  })

  it('列常量覆盖税局页面的全部展示列', () => {
    expect(TAX_REGISTRATION_COLUMNS).toHaveLength(9)
    expect(TAX_REGISTRATION_COLUMNS[0]).toBe('征收项目')
    expect(TAX_REGISTRATION_COLUMNS[8]).toBe('征收代理方式')
  })
})

describe('展示格式化', () => {
  it('征收率与税率按百分比展示且不带浮点尾巴', () => {
    expect(formatRate(0.03)).toBe('3%')
    expect(formatRate(0.00025)).toBe('0.025%')
    expect(formatRate(0.0003)).toBe('0.03%')
    expect(formatRate(0.07)).toBe('7%')
    expect(formatRate(null)).toBe('—')
  })

  it('带单位的税率列原样展示单位文本', () => {
    expect(formatTaxRate({ taxRate: null, rateUnit: '8元/平方米' })).toBe('8元/平方米')
    expect(formatTaxRate({ taxRate: 0.0003, rateUnit: '' })).toBe('0.03%')
  })

  it('属期标签区分季度 / 月度 / 年度', () => {
    expect(periodLabel('2026-Q3')).toBe('2026 Q3')
    expect(periodLabel('2026-09')).toBe('2026年9月')
    expect(periodLabel('2026')).toBe('2026年度')
    expect(monthLabel('2026-04')).toBe('2026年4月')
  })

  it('纳税期限与征期状态文案', () => {
    expect(cycleLabel('quarter')).toBe('季')
    expect(cycleLabel('year')).toBe('年')
    expect(deadlineStatusText('pending')).toBe('待申报')
    expect(deadlineStatusType('filed')).toBe('success')
    expect(deadlineStatusType('pending')).toBe('warning')
  })

  it('导入摘要按实际数量拼句', () => {
    expect(summarizeImport({ created: 10, updated: 0, deactivated: 0 })).toBe('新增 10 行，更新 0 行')
    expect(summarizeImport({ created: 1, updated: 2, deactivated: 3 })).toBe('新增 1 行，更新 2 行，停用 3 行')
  })

  it('按申报表组归并认定行', () => {
    const base = {
      id: null,
      seq: 1,
      levyCategory: '',
      levySubItem: '',
      filingCycle: 'quarter' as const,
      filingCycleText: '季',
      deadlineText: '',
      deadlineRule: 'quarter_15',
      deadlineRuleLabel: '',
      dueMonthOffset: null,
      dueDay: null,
      levyRate: null,
      taxRate: null,
      rateUnit: '',
      mainFlag: 'main' as const,
      collectionMethod: 'self' as const,
      status: 'active' as const,
      source: 'manual',
      note: '',
    }
    const groups = groupRowsByReturnGroup([
      { ...base, levyItem: '增值税', returnGroup: 'vat', returnGroupLabel: '增值税及附加税费申报表' },
      { ...base, levyItem: '城市维护建设税', returnGroup: 'vat', returnGroupLabel: '增值税及附加税费申报表' },
      { ...base, levyItem: '印花税', returnGroup: 'stamp_duty', returnGroupLabel: '财产和行为税纳税申报表' },
    ])

    expect(groups).toHaveLength(2)
    expect(groups[0].rows).toHaveLength(2)
    expect(groups[1].label).toBe('财产和行为税纳税申报表')
  })
})
