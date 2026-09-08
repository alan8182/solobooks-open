import { describe, expect, it } from 'vitest'
import { NAV_ITEMS } from '@/utils/constants'

describe('sidebar navigation structure', () => {
  it('keeps frequent work at the first level and removes the duplicate record action', () => {
    const topLevelPaths = NAV_ITEMS.flatMap((item) => item.path ? [item.path] : [])

    expect(topLevelPaths).toEqual(['/', '/ledger', '/todos', '/copilot', '/reports'])
    expect(topLevelPaths).not.toContain('/record/text')
  })

  it('groups infrequent finance and settings destinations into secondary menus', () => {
    const financeTools = NAV_ITEMS.find((item) => item.label === '财务工具')
    const settings = NAV_ITEMS.find((item) => item.label === '系统设置')

    expect(financeTools?.children?.map((item) => item.path)).toEqual([
      '/record/invoice-import',
      '/bank',
      '/vouchers',
      '/assets',
      '/rd-expenses',
      '/mine/salary',
      '/private-expenses',
    ])
    expect(settings?.children?.map((item) => item.path)).toEqual([
      '/mine',
      '/mine/companies',
      '/mine/categories',
      '/mine/accounts',
      '/mine/opening',
      '/mine/audit-log',
    ])
  })
})
