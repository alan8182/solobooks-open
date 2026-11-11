/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { beforeEach, describe, expect, it } from 'vitest'
import {
  ARCHIVE_AFTER_DAYS,
  EXPAND_WINDOW_DAYS,
  emptyGuideState,
  guideStorageKey,
  isArchived,
  isDismissedToday,
  readGuideState,
  resetGuideState,
  resolveChecklistDisplay,
  shouldOpenL0Modal,
  writeGuideState,
} from '@/stores/guideState'
import type { SetupReadiness } from '@/api/types'

function readiness(overrides: Partial<SetupReadiness> = {}): SetupReadiness {
  return {
    companyId: 'c1',
    view: 'boss',
    fiscalYear: 2026,
    completed: 2,
    total: 5,
    ready: false,
    generatedAt: '2026-10-20T12:00:00+08:00',
    items: [],
    ...overrides,
  }
}

const CREATED_RECENTLY = '2026-10-18T09:00:00+08:00'
const NOW = new Date('2026-10-20T21:00:00+08:00')

describe('guide state storage', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('keys state per company under the solo: prefix', () => {
    expect(guideStorageKey('c1')).toBe('solo:guide:c1')
    expect(guideStorageKey(null)).toBe('solo:guide:default')
  })

  it('returns defaults when nothing is stored', () => {
    expect(readGuideState('c1')).toEqual(emptyGuideState())
  })

  it('merges patches and normalises waived keys', () => {
    writeGuideState('c1', { collapsed: true, waived: ['legacy_import', 'legacy_import'] })

    const state = readGuideState('c1')
    expect(state.collapsed).toBe(true)
    expect(state.waived).toEqual(['legacy_import'])
    expect(state.version).toBe(1)
  })

  it('does not leak state across companies', () => {
    writeGuideState('c1', { l0SeenAt: NOW.toISOString() })

    expect(readGuideState('c1').l0SeenAt).toBe(NOW.toISOString())
    expect(readGuideState('c2').l0SeenAt).toBeNull()
  })

  it('recovers from corrupted json instead of throwing', () => {
    localStorage.setItem(guideStorageKey('c1'), '{not json')

    expect(readGuideState('c1')).toEqual(emptyGuideState())
  })

  it('reset clears the stored state', () => {
    writeGuideState('c1', { collapsed: true })
    resetGuideState('c1')

    expect(readGuideState('c1').collapsed).toBe(false)
  })
})

describe('guide state timing rules', () => {
  it('archives only after the grace period', () => {
    const readyAt = '2026-10-20T09:00:00+08:00'
    const sixDays = new Date('2026-10-26T09:00:00+08:00')
    const nineDays = new Date('2026-10-29T09:00:00+08:00')

    expect(isArchived({ ...emptyGuideState(), readyAt }, sixDays)).toBe(false)
    expect(isArchived({ ...emptyGuideState(), readyAt }, nineDays)).toBe(true)
    expect(ARCHIVE_AFTER_DAYS).toBe(7)
  })

  it('hides the checklist only for the rest of the same day', () => {
    const state = { ...emptyGuideState(), dismissedAt: '2026-10-20T09:30:00+08:00' }

    expect(isDismissedToday(state, new Date('2026-10-20T23:59:00+08:00'))).toBe(true)
    expect(isDismissedToday(state, new Date('2026-10-21T00:05:00+08:00'))).toBe(false)
  })

  it('expands for a fresh book, summarises once it is ready', () => {
    const state = emptyGuideState()

    expect(resolveChecklistDisplay(state, readiness(), CREATED_RECENTLY, false, NOW)).toBe('expanded')
    expect(
      resolveChecklistDisplay(state, readiness({ ready: true }), CREATED_RECENTLY, false, NOW),
    ).toBe('summary')
  })

  it('hides progress for a demo book even when its sample data is ready', () => {
    expect(
      resolveChecklistDisplay(emptyGuideState(), readiness({ ready: true }), CREATED_RECENTLY, true, NOW),
    ).toBe('hidden')
  })

  it('degrades to a single line after the onboarding window', () => {
    const state = emptyGuideState()
    const oldBook = '2026-09-01T09:00:00+08:00'

    expect(EXPAND_WINDOW_DAYS).toBe(14)
    expect(resolveChecklistDisplay(state, readiness(), oldBook, false, NOW)).toBe('summary')
  })

  it('renders nothing when there is no data or the user closed it today', () => {
    expect(resolveChecklistDisplay(emptyGuideState(), null, CREATED_RECENTLY, false, NOW)).toBe('hidden')
    expect(
      resolveChecklistDisplay(
        { ...emptyGuideState(), dismissedAt: NOW.toISOString() },
        readiness(),
        CREATED_RECENTLY,
        false,
        NOW,
      ),
    ).toBe('hidden')
  })
})

describe('shouldOpenL0Modal 起跑线弹窗准入守卫', () => {
  const baseContext = {
    isReplay: false,
    hasManualCompany: false,
    isCurrentDemo: true,
    l0SeenAt: null,
    userL0Seen: false,
    legacyDismissed: false,
    sessionShown: false,
    companyCreatedAt: '2026-10-18T09:00:00+08:00',
    now: new Date('2026-10-20T21:00:00+08:00'),
  }

  it('全新演示账套用户首次进入，且未建真实账套，允许弹窗引导', () => {
    expect(shouldOpenL0Modal({ ...baseContext })).toBe(true)
  })

  it('已有手动设置的真实账套时，绝不自动弹建账引导', () => {
    expect(shouldOpenL0Modal({ ...baseContext, hasManualCompany: true })).toBe(false)
  })

  it('当前账套已是非演示账套（真实账套），绝不自动弹建账引导', () => {
    expect(shouldOpenL0Modal({ ...baseContext, isCurrentDemo: false })).toBe(false)
  })

  it('账号或账套已看过/关过，不再重复弹出', () => {
    expect(shouldOpenL0Modal({ ...baseContext, l0SeenAt: '2026-10-19T10:00:00+08:00' })).toBe(false)
    expect(shouldOpenL0Modal({ ...baseContext, userL0Seen: true })).toBe(false)
    expect(shouldOpenL0Modal({ ...baseContext, legacyDismissed: true })).toBe(false)
    expect(shouldOpenL0Modal({ ...baseContext, sessionShown: true })).toBe(false)
  })

  it('建账超过 14 天的用户，不予弹窗打扰', () => {
    expect(
      shouldOpenL0Modal({
        ...baseContext,
        companyCreatedAt: '2026-09-01T09:00:00+08:00',
      }),
    ).toBe(false)
  })

  it('在 /guide 点击重看新手引导时，始终允许重放', () => {
    expect(
      shouldOpenL0Modal({
        ...baseContext,
        isReplay: true,
        hasManualCompany: true,
        isCurrentDemo: false,
        userL0Seen: true,
      }),
    ).toBe(true)
  })
})
