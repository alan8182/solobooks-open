/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { beforeEach, describe, expect, it } from 'vitest'
import {
  loadOpeningMapping,
  openingMappingStorageKey,
  pickConfirmedMappings,
  saveOpeningMapping,
} from '@/utils/openingMapping'

describe('opening mapping storage helpers', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('derives a per-company storage key with the repo-wide solo: prefix convention', () => {
    expect(openingMappingStorageKey('c1')).toBe('solo:opening-mapping:c1')
    expect(openingMappingStorageKey(null)).toBe('solo:opening-mapping:default')
  })

  // 根因回归：映射必须按账套隔离，A 账套的旧科目映射不能套到 B 账套
  it('isolates persisted mappings per company', () => {
    saveOpeningMapping('c1', { '2211': '221102' })
    saveOpeningMapping('c2', { '2211': '5602016' })

    expect(loadOpeningMapping('c1')['2211']).toBe('221102')
    expect(loadOpeningMapping('c2')['2211']).toBe('5602016')
  })

  it('round-trips mappings and returns empty when nothing stored', () => {
    expect(loadOpeningMapping('c1')).toEqual({})
    const mapping = { '2211001': '221101', '2221001': '222100101' }
    saveOpeningMapping('c1', mapping)
    expect(loadOpeningMapping('c1')).toEqual(mapping)
  })

  it('drops malformed entries instead of crashing', () => {
    localStorage.setItem(
      openingMappingStorageKey('c1'),
      JSON.stringify({ good: '221101', bad: 42, empty: '', '': 'x' }),
    )
    expect(loadOpeningMapping('c1')).toEqual({ good: '221101' })
  })

  it('returns empty for corrupted json', () => {
    localStorage.setItem(openingMappingStorageKey('c1'), '{broken')
    expect(loadOpeningMapping('c1')).toEqual({})
  })

  // 回归：持久化的映射本该「下次导入自动套用」，此前只预填在面板上仍要手点一次。
  // 只有用户确认过的才自动套用，名称猜测的建议值不能替他做主。
  it('auto-applies only mappings the user confirmed, never bare suggestions', () => {
    const prefilled = { '2211001': '221101', '5602021': '5602016' }
    expect(pickConfirmedMappings(prefilled, { '2211001': '221101' })).toEqual({
      '2211001': '221101',
    })
    // 存的目标与本次预填不一致（科目表变了）时按未确认处理
    expect(pickConfirmedMappings(prefilled, { '2211001': '221199' })).toEqual({})
    expect(pickConfirmedMappings(prefilled, {})).toEqual({})
  })
})
