/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import {
  companyCreateLandingPath,
  parseCompanyCreateQuery,
  shouldSwitchAfterCreate,
} from '@/utils/companyOnboarding'

describe('company onboarding entry', () => {
  it('只认 new / migration 两种建账场景，脏链接不开弹窗', () => {
    expect(parseCompanyCreateQuery('new')).toBe('new')
    expect(parseCompanyCreateQuery('migration')).toBe('migration')
    expect(parseCompanyCreateQuery('')).toBeNull()
    expect(parseCompanyCreateQuery(undefined)).toBeNull()
    expect(parseCompanyCreateQuery(['new', 'migration'])).toBeNull()
    expect(parseCompanyCreateQuery('onboarding')).toBeNull()
  })

  it('代账转入落到期初建账工作台，新成立公司优先前往税费种认定', () => {
    expect(companyCreateLandingPath('migration', '2026-11-01')).toBe('/mine/opening')
    expect(companyCreateLandingPath('new', '2026-11-01')).toBe('/tax-registration')
    expect(companyCreateLandingPath('new')).toBe('/tax-registration')
  })

  it('演示账套与建账引导都必须切到新主体，普通新增不打断当前账套', () => {
    expect(shouldSwitchAfterCreate(true, false)).toBe(true)
    expect(shouldSwitchAfterCreate(true, true)).toBe(true)
    expect(shouldSwitchAfterCreate(false, true)).toBe(true)
    expect(shouldSwitchAfterCreate(false, false)).toBe(false)
  })
})
