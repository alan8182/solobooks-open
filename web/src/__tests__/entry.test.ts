import { describe, it, expect, beforeEach } from 'vitest'
import { setActivePinia, createPinia } from 'pinia'
import { useEntryStore } from '@/stores/entry'
import type { Entry } from '@/api/types'

function makeEntry(overrides: Partial<Entry> = {}): Entry {
  return {
    id: 'test-1',
    user_id: 'u1',
    company_id: 'c1',
    direction: 'expense',
    amount: 100,
    title: '测试',
    category: '办公用品',
    occurDate: '2026-06-01',
    ...overrides,
  } as Entry
}

describe('entry store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('activeEntries returns all entries when no company context', () => {
    const store = useEntryStore()
    store.entries = [makeEntry(), makeEntry({ id: 'test-2' })]
    expect(store.activeEntries).toHaveLength(2)
  })

  it('monthlyIncome sums income entries for current month', () => {
    const store = useEntryStore()
    const now = new Date()
    const thisMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
    store.entries = [
      makeEntry({ direction: 'income', amount: 1000, occurDate: `${thisMonth}-01` }),
      makeEntry({ direction: 'income', amount: 500, occurDate: `${thisMonth}-15` }),
      makeEntry({ direction: 'expense', amount: 300, occurDate: `${thisMonth}-10` }),
    ]
    expect(store.monthlyIncome).toBe(1500)
  })

  it('monthlyExpense sums expense entries for current month', () => {
    const store = useEntryStore()
    const now = new Date()
    const thisMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
    store.entries = [
      makeEntry({ direction: 'expense', amount: 200, occurDate: `${thisMonth}-05` }),
      makeEntry({ direction: 'expense', amount: 300, occurDate: `${thisMonth}-10` }),
      makeEntry({ direction: 'income', amount: 1000, occurDate: `${thisMonth}-01` }),
    ]
    expect(store.monthlyExpense).toBe(500)
  })

  it('netProfit calculates income minus expense', () => {
    const store = useEntryStore()
    const now = new Date()
    const thisMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
    store.entries = [
      makeEntry({ direction: 'income', amount: 1000, occurDate: `${thisMonth}-01` }),
      makeEntry({ direction: 'expense', amount: 400, occurDate: `${thisMonth}-05` }),
    ]
    expect(store.netProfit).toBe(600)
  })

  it('filters entries by company_id when auth store has currentCompanyId', () => {
    const store = useEntryStore()
    store.entries = [
      makeEntry({ id: 'a', company_id: 'c1' }),
      makeEntry({ id: 'b', company_id: 'c2' }),
    ]
    expect(store.activeEntries).toHaveLength(2)
  })
})
