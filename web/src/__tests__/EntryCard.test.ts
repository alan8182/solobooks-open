/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import EntryCard from '@/components/business/EntryCard.vue'
import type { Entry } from '@/api/types'

function makeEntry(overrides: Partial<Entry> = {}): Entry {
  return {
    id: 'test-1',
    user_id: 'u1',
    company_id: 'c1',
    direction: 'expense',
    amount: 100,
    title: '买办公用品',
    category: '办公用品',
    occurDate: '2026-06-01',
    ...overrides,
  } as Entry
}

describe('EntryCard', () => {
  it('renders entry title', () => {
    const wrapper = mount(EntryCard, {
      props: { entry: makeEntry() },
    })
    expect(wrapper.text()).toContain('买办公用品')
  })

  it('renders formatted amount with minus for expense', () => {
    const wrapper = mount(EntryCard, {
      props: { entry: makeEntry({ amount: 500 }) },
    })
    expect(wrapper.text()).toContain('-')
  })

  it('renders formatted amount with plus for income', () => {
    const wrapper = mount(EntryCard, {
      props: { entry: makeEntry({ direction: 'income', amount: 1000 }) },
    })
    expect(wrapper.text()).toContain('+')
  })

  it('shows category icon text', () => {
    const wrapper = mount(EntryCard, {
      props: { entry: makeEntry({ category: '餐饮' }) },
    })
    expect(wrapper.text()).toContain('餐饮')
  })

  it('shows shareholder investment as capital, not current account', () => {
    const wrapper = mount(EntryCard, {
      props: { entry: makeEntry({ direction: 'owner_transfer', category: '股东投入' }) },
    })
    expect(wrapper.text()).toContain('资本')
    expect(wrapper.text()).not.toContain('往来')
  })

  it('keeps repayable owner transfers labelled as current account', () => {
    const wrapper = mount(EntryCard, {
      props: { entry: makeEntry({ direction: 'owner_transfer', category: '股东垫付' }) },
    })
    expect(wrapper.text()).toContain('往来')
  })

  it('emits click event on click', async () => {
    const wrapper = mount(EntryCard, {
      props: { entry: makeEntry() },
    })
    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toBeTruthy()
  })
})
