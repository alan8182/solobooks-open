/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { NMessageProvider } from 'naive-ui'
import BackupPage from '@/pages/mine/backup.vue'
import router from '@/router'

// Mock api calls
vi.mock('@/api/backup', () => ({
  createArchive: vi.fn(),
  downloadBackupRecord: vi.fn(),
  getBackupRecords: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}))

vi.mock('@/api/audit', () => ({
  getAuditLogs: vi.fn().mockResolvedValue({ items: [], total: 0 }),
}))

describe('backup and audit log integration (Plan A)', () => {
  it('redirects /mine/audit-log route to /mine/backup?tab=audit', async () => {
    const resolved = router.resolve('/mine/audit-log')
    const leaf = resolved.matched[resolved.matched.length - 1]
    expect(leaf?.redirect).toBe('/mine/backup?tab=audit')
  })

  it('renders backup and audit log tabs on backup page', async () => {
    const testRouter = createRouter({
      history: createMemoryHistory(),
      routes: [
        {
          path: '/mine/backup',
          component: BackupPage,
        },
      ],
    })
    await testRouter.push('/mine/backup')
    await testRouter.isReady()

    const wrapper = mount({
      render: () => h(NMessageProvider, null, { default: () => h(BackupPage) }),
    }, {
      global: {
        plugins: [testRouter],
        stubs: {
          SoloEmpty: true,
        },
      },
    })

    expect(wrapper.text()).toContain('数据归档与备份')
    expect(wrapper.text()).toContain('审计日志')
  })
})
