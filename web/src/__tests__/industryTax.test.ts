/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'

import {
  RESTRICTED_INDUSTRIES,
  filterIndustryGroups,
  isRestrictedIndustry,
  restrictedIndustryFor,
} from '@/utils/industryTax'

describe('industry option filtering', () => {
  const groups = [
    { label: '信息传输、软件和信息技术服务业', children: ['软件和信息技术服务业'] },
    { label: '采矿业', children: ['煤炭开采和洗选业'], hidden: true },
    { label: '制造业', children: ['食品制造业'], hidden: true },
  ]

  it('hides non-target groups by default', () => {
    const visible = filterIndustryGroups(groups, { showAll: false })
    expect(visible.map((g) => g.label)).toEqual(['信息传输、软件和信息技术服务业'])
  })

  it('reveals everything when showAll is on', () => {
    const visible = filterIndustryGroups(groups, { showAll: true })
    expect(visible).toHaveLength(3)
  })

  it('keeps the group of an already selected hidden industry', () => {
    const visible = filterIndustryGroups(groups, {
      showAll: false,
      selected: '煤炭开采和洗选业',
    })
    expect(visible.map((g) => g.label)).toContain('采矿业')
    expect(visible.map((g) => g.label)).not.toContain('制造业')
  })
})

describe('industry tax defaults', () => {
  it('marks restricted industries as yes', () => {
    expect(restrictedIndustryFor('煤炭开采和洗选业')).toBe('yes')
    expect(restrictedIndustryFor('烟草制品业')).toBe('yes')
    expect(restrictedIndustryFor('化学原料和化学制品制造业')).toBe('yes')
    expect(isRestrictedIndustry('有色金属冶炼和压延加工业')).toBe(true)
  })

  it('defaults common service industries to no', () => {
    expect(restrictedIndustryFor('软件和信息技术服务业')).toBe('no')
    expect(restrictedIndustryFor('商务服务业')).toBe('no')
    expect(restrictedIndustryFor('餐饮业')).toBe('no')
    expect(isRestrictedIndustry('互联网和相关服务')).toBe(false)
  })

  it('handles empty / unknown industry safely', () => {
    expect(restrictedIndustryFor('')).toBe('no')
    expect(restrictedIndustryFor(undefined)).toBe('no')
    expect(restrictedIndustryFor(null)).toBe('no')
    expect(restrictedIndustryFor('某个不在清单里的行业')).toBe('no')
  })

  it('does not contain broad ambiguous categories', () => {
    // 这些大类里限制类与鼓励类混杂，不整类默认「是」
    expect(RESTRICTED_INDUSTRIES.has('制造业')).toBe(false)
    expect(RESTRICTED_INDUSTRIES.has('电力、热力生产和供应业')).toBe(false)
    expect(RESTRICTED_INDUSTRIES.has('房地产业')).toBe(false)
    expect(RESTRICTED_INDUSTRIES.has('金融业')).toBe(false)
  })
})
