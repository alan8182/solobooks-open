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
