/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// 工资表金额归一化守卫。
//
// 回归背景：后端 Decimal 一律以字符串返回（"925.00"），响应拦截器按字段名后缀
// 把命中的值转 Number。工资表字段是 snake_case，原先只有 personal_tax 命中 tax$，
// 其余 base_salary / bonus / allowance / deduction / personal_social_security /
// company_social_security / net_salary 全部漏网，字符串直接进了 NInputNumber。
// naive-ui 的 format() 见 typeof value !== 'number' 就返回空串，草稿工资表 5 列
// 金额因此永远显示 placeholder「请输入」——用户以为没填，手工改掉系统算好的社保。
//
// 这个测试锁住两件事：
//   1) 工资金额字段必须被转成 number（NInputNumber 才能显示）；
//   2) 展示用的单据串（socialSecurityDue 等）必须保持字符串（前端拼 ¥ 显示）。

import { describe, expect, it } from 'vitest'
import apiClient from '@/api/client'

function money(value: number): string {
  return value.toFixed(2)
}

/** 用自定义 adapter 让 axios 直接回放响应体，从而走真实响应拦截器。 */
async function fetchWithInterceptor(url: string, body: unknown): Promise<any> {
  const response = await apiClient.get(url, {
    adapter: async (config) => ({
      data: body,
      status: 200,
      statusText: 'OK',
      headers: {},
      config,
    }),
  })
  // 拦截器返回的是 response.data，运行时不是 AxiosResponse
  return response as unknown as any
}

const SHEET_PAYLOAD = {
  id: 'sht-1',
  month: '2026-10',
  status: 'draft',
  socialSecurityDue: '1420.00',
  housingFundDue: '640.00',
  taxDue: '0.00',
  ssPaymentStatus: 'unpaid',
  hfPaymentStatus: 'unpaid',
  taxPaymentStatus: 'not_required',
  records: [
    {
      id: 'srec-1',
      employee_id: 'emp-1',
      employee_name: '陈默',
      base_salary: money(6500),
      bonus: money(0),
      allowance: money(0),
      deduction: money(0),
      personal_social_security: money(925),
      personal_tax: money(0),
      company_social_security: money(1650),
      net_salary: money(5575),
      total_cost: money(8150),
    },
  ],
}

describe('工资表金额归一化', () => {
  it('明细金额字段都转成 number，NInputNumber 才能显示出来', async () => {
    const sheet = await fetchWithInterceptor('/api/salary/sheets/2026-10', SHEET_PAYLOAD)
    const record = sheet.records[0]
    for (const key of [
      'base_salary', 'bonus', 'allowance', 'deduction',
      'personal_social_security', 'personal_tax', 'company_social_security',
      'net_salary', 'total_cost',
    ]) {
      expect(typeof record[key], `${key} 应为 number`).toBe('number')
    }
    expect(record.personal_social_security).toBe(925)
    expect(record.company_social_security).toBe(1650)
    expect(record.net_salary).toBe(5575)
  })

  it('缴纳金额展示串保持字符串（前端拼 ¥ 显示）', async () => {
    const sheet = await fetchWithInterceptor('/api/salary/sheets/2026-10', SHEET_PAYLOAD)
    expect(sheet.socialSecurityDue).toBe('1420.00')
    expect(sheet.housingFundDue).toBe('640.00')
    expect(sheet.taxDue).toBe('0.00')
    expect(sheet.ssPaymentStatus).toBe('unpaid')
  })

  it('员工名册的工资与社保基数也归一化（编辑弹窗回填需要）', async () => {
    const employees = await fetchWithInterceptor('/api/salary/employees', [
      {
        id: 'emp-1',
        name: '陈默',
        department: '总经办',
        base_salary: money(6500),
        social_security_base: money(5000),
        special_additional_deduction: money(2000),
        is_owner: 1,
      },
    ])
    expect(typeof employees[0].base_salary).toBe('number')
    expect(typeof employees[0].social_security_base).toBe('number')
    expect(typeof employees[0].special_additional_deduction).toBe('number')
    // 非金额字段不能被误转
    expect(employees[0].department).toBe('总经办')
    expect(employees[0].is_owner).toBe(1)
  })

  it('缴纳类型枚举不受影响（social_security 不是金额）', async () => {
    const payload = await fetchWithInterceptor('/api/salary/liabilities/suggestions', {
      total: 1,
      suggestions: [
        {
          month: '2026-10',
          kind: 'social_security',
          label: '社保',
          amount: money(1775),
          payDate: '2026-11-10',
        },
      ],
    })
    expect(payload.suggestions[0].kind).toBe('social_security')
    expect(payload.suggestions[0].amount).toBe(1775)
  })
})
