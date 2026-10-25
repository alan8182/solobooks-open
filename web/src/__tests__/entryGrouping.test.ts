/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { dayLabel, entryDayKey, groupByDay, sortByDayDesc } from '@/utils/entryGrouping'

const NOW = new Date(2026, 9, 18) // 2026-10-18 周日（本地时区）

describe('entryDayKey', () => {
  it('优先业务日期，其次创建时间', () => {
    expect(entryDayKey({ occurDate: '2026-10-01T10:00:00', created_at: '2026-10-05' })).toBe('2026-10-01')
    expect(entryDayKey({ occurDate: null, created_at: '2026-10-05T09:00:00' })).toBe('2026-10-05')
    expect(entryDayKey({})).toBe('')
  })
})

describe('dayLabel', () => {
  it('今天 / 昨天用相对说法', () => {
    expect(dayLabel('2026-10-18', NOW)).toEqual({ label: '今天', weekday: '周日' })
    expect(dayLabel('2026-10-17', NOW)).toEqual({ label: '昨天', weekday: '周六' })
  })

  it('同年只给月日，跨年补年份', () => {
    expect(dayLabel('2026-10-01', NOW)).toEqual({ label: '10月1日', weekday: '周四' })
    expect(dayLabel('2025-12-31', NOW).label).toBe('2025年12月31日')
  })

  it('按本地时区解析：东八区的 10-18 不能变成 10-17', () => {
    // new Date('2026-10-18') 会按 UTC 解析，东八区取到的是 10-17
    expect(dayLabel('2026-10-18', NOW).label).toBe('今天')
  })

  it('无法解析的日期原样返回', () => {
    expect(dayLabel('', NOW)).toEqual({ label: '日期未知', weekday: '' })
    expect(dayLabel('2026/10/18', NOW)).toEqual({ label: '2026/10/18', weekday: '' })
  })
})

describe('groupByDay', () => {
  it('同日合并，保持入参顺序', () => {
    const items = [
      { occurDate: '2026-10-18', id: 1 },
      { occurDate: '2026-10-18', id: 2 },
      { occurDate: '2026-10-17', id: 3 },
    ]
    const groups = groupByDay(items, NOW)
    expect(groups).toHaveLength(2)
    expect(groups[0].date).toBe('2026-10-18')
    expect(groups[0].label).toBe('今天')
    expect(groups[0].items.map((i) => i.id)).toEqual([1, 2])
    expect(groups[1].items.map((i) => i.id)).toEqual([3])
  })

  it('无日期的归到一个组并沉底', () => {
    const items = [
      { occurDate: '', id: 9 },
      { occurDate: '2026-10-18', id: 1 },
    ]
    const groups = groupByDay(items, NOW)
    expect(groups.map((g) => g.date)).toEqual(['2026-10-18', ''])
    expect(groups[1].label).toBe('日期未知')
  })
})

describe('sortByDayDesc', () => {
  it('按日期倒序，无日期的沉底，同日保持原序', () => {
    const items = [
      { occurDate: '2026-10-16', id: 'a' },
      { occurDate: '', id: 'x' },
      { occurDate: '2026-10-18', id: 'b' },
      { occurDate: '2026-10-18', id: 'c' },
      { occurDate: '2026-10-17', id: 'd' },
    ]
    expect(sortByDayDesc(items).map((i) => i.id)).toEqual(['b', 'c', 'd', 'a', 'x'])
  })

  it('不修改入参', () => {
    const items = [{ occurDate: '2026-10-16' }, { occurDate: '2026-10-18' }]
    sortByDayDesc(items)
    expect(items[0].occurDate).toBe('2026-10-16')
  })
})
