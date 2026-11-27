/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import EmployeeRosterTab from '@/pages/mine/salary/components/EmployeeRosterTab.vue'

vi.mock('naive-ui', async (importOriginal) => ({
  ...await importOriginal<typeof import('naive-ui')>(),
  useMessage: () => ({ success: vi.fn(), warning: vi.fn(), error: vi.fn() }),
}))

// 离职确认框要 n-dialog-provider，本用例只验工具栏入口，把确认弹窗的依赖桩掉
vi.mock('@/composables/useDeleteConfirm', () => ({
  useDeleteConfirm: () => ({ confirmDelete: vi.fn() }),
}))

describe('员工花名册的参保指引入口', () => {
  it('把参保/停保指引放在花名册工具栏', () => {
    // 参保登记、停保在浙里办办理，和电子税务局的申报缴纳是两件事；
    // 入口只挂薪酬花名册，用户入职/离职时能就地看到。
    const pinia = createPinia()
    setActivePinia(pinia)
    const wrapper = mount(EmployeeRosterTab, {
      global: { plugins: [pinia] },
      props: { employees: [], departments: [] },
    })

    expect(wrapper.text()).toContain('员工参保/停保怎么办理？')
  })
})
