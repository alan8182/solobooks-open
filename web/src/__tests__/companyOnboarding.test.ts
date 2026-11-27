/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import {
  companyCreateLandingPath,
  createSuccessSwitchNotice,
  deriveStartDateFromIncorporation,
  parseCompanyCreateQuery,
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

  // 回归 2026-11-21 事故：建账后未自动切换且弹窗对「当前账套」保持沉默，
  // 用户带着旧账套的心智去结账/同步税费种认定，数据落错账。
  it('创建成功弹窗必须说明当前停留在哪个账套，切换失败时点名旧账套', () => {
    expect(createSuccessSwitchNotice(true, '陕西澜晞文化传媒有限责任公司')).toBe(
      '当前账套已自动切换至该主体。',
    )
    expect(createSuccessSwitchNotice(false, '陕西澜晞文化传媒有限责任公司')).toBe(
      '自动切换失败，当前仍停留在「陕西澜晞文化传媒有限责任公司」，请先在主体列表手动切换再继续操作。',
    )
    expect(createSuccessSwitchNotice(false, null)).toBe(
      '自动切换失败，当前仍停留在「原账套」，请先在主体列表手动切换再继续操作。',
    )
  })
})

describe('新成立公司启用日期默认带入成立日期', () => {
  const base = {
    onboardingType: 'new' as const,
    incorporationDate: '2026-03-15',
    startDate: null,
    startDateAuto: true,
  }

  it('未填启用日期时带入成立日期', () => {
    expect(deriveStartDateFromIncorporation(base)).toEqual({
      startDate: '2026-03-15',
      startDateAuto: true,
    })
  })

  it('成立日期变化时同步仍为自动值的启用日期', () => {
    expect(
      deriveStartDateFromIncorporation({
        ...base,
        incorporationDate: '2026-04-01',
        startDate: '2026-03-15',
      }),
    ).toEqual({ startDate: '2026-04-01', startDateAuto: true })
  })

  it('用户手改过启用日期后不再被成立日期覆盖', () => {
    expect(
      deriveStartDateFromIncorporation({
        ...base,
        startDate: '2026-06-01',
        startDateAuto: false,
      }),
    ).toBeNull()
  })

  it('已记账公司不参与带入，没有成立日期时也不改动', () => {
    expect(deriveStartDateFromIncorporation({ ...base, onboardingType: 'migration' })).toBeNull()
    expect(deriveStartDateFromIncorporation({ ...base, incorporationDate: null })).toBeNull()
  })

  it('自动值与成立日期已一致时不重复写入', () => {
    expect(
      deriveStartDateFromIncorporation({ ...base, startDate: '2026-03-15' }),
    ).toBeNull()
  })
})
