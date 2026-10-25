/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { normalizedPostingStatus, postingStatusPresentation } from '@/utils/postingStatus'

describe('postingStatusPresentation', () => {
  it('maps posted to 已过账，且正常态不上色', () => {
    expect(postingStatusPresentation('posted')).toEqual({
      label: '已过账',
      tagType: 'success',
      tone: 'neutral',
      needsAttention: false,
    })
  })

  it('maps draft to null so the default state stays unlabeled', () => {
    expect(postingStatusPresentation('draft')).toBeNull()
  })

  it('maps pending_review and reversed for future-proofing', () => {
    expect(postingStatusPresentation('pending_review')).toEqual({
      label: '待复核',
      tagType: 'warning',
      tone: 'warning',
      needsAttention: true,
    })
    expect(postingStatusPresentation('reversed')).toEqual({
      label: '已冲销',
      tagType: 'default',
      tone: 'neutral',
      needsAttention: false,
    })
  })

  it('只有待处理状态才 needsAttention，正常态一律不上色', () => {
    const attention = (s: string) => postingStatusPresentation(s)?.needsAttention
    expect(attention('posted')).toBe(false)
    expect(attention('reversed')).toBe(false)
    expect(attention('pending_review')).toBe(true)
  })

  it('returns null for unknown or missing values', () => {
    expect(postingStatusPresentation(undefined)).toBeNull()
    expect(postingStatusPresentation(null)).toBeNull()
    expect(postingStatusPresentation('future_state')).toBeNull()
  })
})

describe('normalizedPostingStatus', () => {
  it('treats missing status as draft', () => {
    expect(normalizedPostingStatus(undefined)).toBe('draft')
    expect(normalizedPostingStatus('')).toBe('draft')
  })

  it('passes known statuses through', () => {
    expect(normalizedPostingStatus('posted')).toBe('posted')
    expect(normalizedPostingStatus('draft')).toBe('draft')
  })
})
