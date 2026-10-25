/**
 * Copyright (c) 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { GLOSSARY, lookupTerm } from '@/utils/glossary'

describe('glossary', () => {
  it('has the ten onboarding terms', () => {
    const terms = GLOSSARY.map((entry) => entry.term)
    ;[
      '期初余额',
      '末级科目',
      '计提',
      '暂估入账',
      '销项',
      '进项',
      '纳税义务时间',
      '未开票收入',
      '往来单位',
      '冲销',
    ].forEach((term) => {
      expect(terms, `缺少词条：${term}`).toContain(term)
    })
  })

  it('keeps terms unique and tooltips short', () => {
    const terms = GLOSSARY.map((entry) => entry.term)
    expect(new Set(terms).size).toBe(terms.length)
    GLOSSARY.forEach((entry) => {
      expect(entry.term.trim().length).toBeGreaterThan(0)
      expect(entry.plain.length, `${entry.term} 的 plain 要塞得进 tooltip`).toBeLessThanOrEqual(50)
    })
  })

  it('resolves terms and ignores blanks', () => {
    expect(lookupTerm('期初余额')?.plain).toContain('已有的钱')
    expect(lookupTerm(' 期初余额 ')?.term).toBe('期初余额')
    expect(lookupTerm('不存在的词')).toBeUndefined()
    expect(lookupTerm('')).toBeUndefined()
  })

  it('points see-links at real routes', () => {
    GLOSSARY.forEach((entry) => {
      if (entry.see) expect(entry.see.startsWith('/'), `${entry.term} 的 see 必须是站内路径`).toBe(true)
    })
  })
})
