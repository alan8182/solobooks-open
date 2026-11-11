/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { submitHealthScan, type HealthScanPayload, type HealthScanReport } from '@/api/healthScan'

const { post } = vi.hoisted(() => ({ post: vi.fn() }))
vi.mock('@/api/client', () => ({ default: { post } }))

const payloads: HealthScanPayload[] = [
  { mode: 'quiz', answers: { companyType: 'OPC', q1: 'A' } },
  { mode: 'balance', rows: [{ account_code: '1001', balance: '123.45' }] },
]

function backendReport(mode: string): HealthScanReport {
  return {
    totalScore: 73,
    healthLevel: 'MEDIUM_RISK',
    levelLabel: '待关注',
    oneSentenceVerdict: '后端分析结果',
    taxBureauView: { ruleId: 'T1', riskLevel: 'medium', details: '税务分析', checkChain: '税务依据' },
    bankFunderView: { ruleId: 'B1', creditLevel: 'medium', details: '银行分析', checkChain: '银行依据' },
    dimensions: [],
    flags: [],
    checks: [{ code: 'C1', name: '检查', status: 'UNKNOWN', observed: '资料不足', missing: ['凭证'], fix: '补充凭证', source: 'backend', dimension: 'voucher', deduction: 0 }],
    coverage: { completed: 0, applicable: 1, percent: 0 },
    scoreBreakdown: { baseline: 73 },
    urgentAction: '补充资料',
    recommendedFix: '核对资料',
    meta: { mode, ruleCount: 1, ephemeral: true, baselineVersion: 'test', analysisSource: 'deterministic' },
  }
}

describe('submitHealthScan', () => {
  beforeEach(() => { post.mockReset() })

  it.each(payloads)('returns the backend report and tags its source for $mode', async (payload) => {
    const report = backendReport(payload.mode)
    post.mockResolvedValue({ ok: true, report })

    await expect(submitHealthScan(payload)).resolves.toEqual({
      ...report,
      meta: { ...report.meta, source: 'backend' },
    })
    expect(post).toHaveBeenCalledOnce()
    expect(post).toHaveBeenCalledWith('/api/health-scan/analyze', payload)
    expect(report.meta.source).toBeUndefined()
  })

  it.each(payloads)('rejects network failures without generating a report for $mode', async (payload) => {
    const error = new Error('Network Error')
    post.mockRejectedValue(error)

    await expect(submitHealthScan(payload)).rejects.toThrow('Network Error')
    expect(post).toHaveBeenCalledOnce()
  })

  it.each([
    { status: 400, detail: '提交数据无效' },
    { status: 429, detail: '体检请求过于频繁，请稍后再试。' },
  ])('preserves the HTTP $status error and detail', async ({ status, detail }) => {
    const error = Object.assign(new Error(`Request failed with status code ${status}`), {
      response: { status, data: { detail } },
    })
    post.mockRejectedValue(error)

    await expect(submitHealthScan(payloads[0]!)).rejects.toMatchObject({
      response: { status, data: { detail } },
    })
    expect(post).toHaveBeenCalledOnce()
  })

  it.each([
    { ok: false, report: backendReport('quiz') },
    { ok: true },
    { ok: true, report: { ...backendReport('quiz'), checks: undefined } },
    { ok: true, report: { ...backendReport('quiz'), coverage: undefined } },
  ])('rejects incomplete backend responses without generating a report', async (response) => {
    post.mockResolvedValue(response)

    await expect(submitHealthScan(payloads[0]!)).rejects.toThrow('初筛服务返回的数据不完整')
    expect(post).toHaveBeenCalledOnce()
  })
})
