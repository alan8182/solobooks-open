/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { summarizeWechatImportFailures, type WechatImportResult } from '@/utils/wechatImport'

describe('summarizeWechatImportFailures', () => {
  it('returns empty for empty or all-success results', () => {
    expect(summarizeWechatImportFailures([])).toEqual([])
    const ok: WechatImportResult[] = [
      { status: 'success', title: 'A' },
      { status: 'success', title: 'B' },
    ]
    expect(summarizeWechatImportFailures(ok)).toEqual([])
  })

  it('ignores failed rows without error text and non-failed rows', () => {
    const rows: WechatImportResult[] = [
      { status: 'success', title: 'A' },
      { status: 'failed', title: 'B' },
    ]
    expect(summarizeWechatImportFailures(rows)).toEqual([])
  })

  it('merges start-date rejections from different dates into one group', () => {
    const rows: WechatImportResult[] = [
      { status: 'failed', title: 'A', error: '记账日期 2026-06-11 早于账套启用日期 2026-10-18：请调整启用日期' },
      { status: 'failed', title: 'B', error: '记账日期 2026-06-17 早于账套启用日期 2026-10-18：请调整启用日期' },
      { status: 'failed', title: 'C', error: '记账日期 2026-06-18 早于账套启用日期 2026-10-18：请调整启用日期' },
    ]
    const groups = summarizeWechatImportFailures(rows)
    expect(groups).toHaveLength(1)
    expect(groups[0].count).toBe(3)
    expect(groups[0].isStartDateIssue).toBe(true)
    expect(groups[0].message).toContain('YYYY-MM-DD')
    expect(groups[0].message).not.toContain('2026-06-11')
  })

  it('keeps distinct failure reasons as separate groups and flags only start-date ones', () => {
    const rows: WechatImportResult[] = [
      { status: 'failed', title: 'A', error: '记账日期 2026-06-11 早于账套启用日期 2026-10-18' },
      { status: 'failed', title: 'B', error: '科目不存在：5602016' },
      { status: 'failed', title: 'C', error: '科目不存在：5602016' },
    ]
    const groups = summarizeWechatImportFailures(rows)
    expect(groups).toHaveLength(2)
    const startDateGroup = groups.find((g) => g.isStartDateIssue)
    const accountGroup = groups.find((g) => !g.isStartDateIssue)
    expect(startDateGroup?.count).toBe(1)
    expect(accountGroup?.count).toBe(2)
    expect(accountGroup?.message).toBe('科目不存在：5602016')
  })
})
