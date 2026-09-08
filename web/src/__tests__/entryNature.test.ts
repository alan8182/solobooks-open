import { describe, expect, it } from 'vitest'
import {
  getEntryNature,
  getEntryNaturePresentation,
  matchesEntryNature,
} from '@/utils/entryNature'

describe('entry business nature', () => {
  it.each(['股东投入', '老板投入', '资本公积'])(
    'maps %s to capital instead of current account',
    (category) => {
      expect(getEntryNature('owner_transfer', category)).toBe('capital')
      expect(matchesEntryNature({ direction: 'owner_transfer', category }, 'current_account')).toBe(false)
      expect(matchesEntryNature({ direction: 'owner_transfer', category }, 'capital')).toBe(true)
    },
  )

  it.each(['股东垫付', '股东取现', '员工垫付报销', '其他应收款', '其他应付款', '往来款'])(
    'keeps %s in current accounts',
    (category) => {
      expect(getEntryNature('owner_transfer', category)).toBe('current_account')
      expect(matchesEntryNature({ direction: 'owner_transfer', category }, 'current_account')).toBe(true)
      expect(matchesEntryNature({ direction: 'owner_transfer', category }, 'capital')).toBe(false)
    },
  )

  it('keeps income and expense semantics unchanged', () => {
    expect(getEntryNature('income', '主营业务收入')).toBe('income')
    expect(getEntryNature('expense', '办公')).toBe('expense')
  })

  it('uses the capital category as the semantic source for legacy direction values', () => {
    expect(getEntryNature('income', '股东投入')).toBe('capital')
    expect(getEntryNature('owner_transfer', '实收资本')).toBe('capital')
  })

  it('provides precise capital labels for detail views', () => {
    expect(getEntryNaturePresentation('owner_transfer', '股东投入').detailLabel).toBe('资本投入')
    expect(getEntryNaturePresentation('owner_transfer', '资本公积').detailLabel).toBe('资本公积')
    expect(getEntryNaturePresentation('owner_transfer', '股东垫付').detailLabel).toBe('往来')
  })
})
