/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// T02 页面级轻量渲染单测。覆盖三条红线：
//  ① 无维度时只渲染引导空状态（不渲染概览表与统计卡）
//  ② meta 未加载时表单禁用（kind/status 选项全部来自 GET /meta，前端不硬编码）
//  ③ 维度归档后强制重拉列表（软删，走 PATCH status='archived'）

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { createPinia, setActivePinia } from 'pinia'

const harness = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  push: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
  success: vi.fn(),
  dialogWarning: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  default: { get: harness.get, post: harness.post, patch: harness.patch, put: vi.fn(), delete: vi.fn() },
  makeIdempotencyKey: () => 'test-only',
}))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: harness.push }) }))
vi.mock('naive-ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('naive-ui')>()),
  useMessage: () => ({ warning: harness.warning, error: harness.error, success: harness.success, info: vi.fn() }),
  useDialog: () => ({ warning: harness.dialogWarning, error: vi.fn(), info: vi.fn() }),
}))

const AggregationIndex = (await import('@/pages/aggregation/index.vue')).default
const AggregationDimensions = (await import('@/pages/aggregation/dimensions.vue')).default

const META = {
  kinds: [
    { value: 'project', label: '项目' },
    { value: 'department', label: '部门' },
    { value: 'custom', label: '自定义' },
  ],
  methods: [{ value: 'direct', label: '直接归属' }],
  memberStatuses: [
    { value: 'active', label: '启用' },
    { value: 'archived', label: '已归档' },
  ],
}

function dimension(id: string, code: string, name: string, kind = 'project', status = 'active') {
  return { id, code, name, kind, status, createdAt: '', updatedAt: '' }
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
})

afterEach(() => {
  vi.clearAllMocks()
})

describe('aggregation workbench', () => {
  it('renders only the three-step empty state when no dimension exists', async () => {
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/aggregation/meta') return META
      if (url.startsWith('/api/aggregation/dimensions')) return []
      return []
    })
    const wrapper = mount(AggregationIndex)
    await flushPromises()

    expect(wrapper.text()).toContain('建立维度')
    expect(wrapper.text()).toContain('添加归集对象')
    expect(wrapper.text()).toContain('配置规则')
    expect(wrapper.text()).toContain('创建示例维度')
    // 空态下不得出现概览表与统计卡——那对用户是噪音
    expect(wrapper.text()).not.toContain('维度概览')
    expect(wrapper.text()).not.toContain('规则总数')
    expect(wrapper.find('.n-data-table').exists()).toBe(false)
    wrapper.unmount()
  })

  it('seeds one dimension with two members via the sample-dimension shortcut', async () => {
    const created = reactive<any[]>([])
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/aggregation/meta') return META
      if (url.startsWith('/api/aggregation/dimensions')) return created
      return []
    })
    harness.post.mockImplementation(async (url: string, body: any) => {
      if (url === '/api/aggregation/dimensions') {
        const record = dimension('d1', body.code, body.name, body.kind)
        created.push(record)
        return record
      }
      return {}
    })
    const wrapper = mount(AggregationIndex)
    await flushPromises()

    const button = wrapper.findAll('button').find((b) => b.text().includes('创建示例维度'))!
    await button.trigger('click')
    await flushPromises()

    expect(harness.success).toHaveBeenCalled()
    const dimensionCalls = harness.post.mock.calls.filter(([url]) => url === '/api/aggregation/dimensions')
    const memberCalls = harness.post.mock.calls.filter(([url]) => url.includes('/members'))
    expect(dimensionCalls).toHaveLength(1)
    expect(memberCalls).toHaveLength(2)
    wrapper.unmount()
  })

  it('shows the overview table and rule statistic once a dimension exists', async () => {
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/aggregation/meta') return META
      if (url === '/api/aggregation/dimensions') return [dimension('d1', 'RD', '研发项目')]
      if (url.includes('/members')) return [{ id: 'm1', code: 'A', name: '对象A' }]
      if (url.includes('/rules')) return [{ id: 'r1' }, { id: 'r2' }]
      return []
    })
    const wrapper = mount(AggregationIndex)
    await flushPromises()

    expect(wrapper.text()).toContain('维度概览')
    expect(wrapper.text()).toContain('RD')
    // kind 的中文标签来自 meta，不是源码硬编码
    expect(wrapper.text()).toContain('项目')
    expect(wrapper.find('.n-statistic').exists()).toBe(true)
    wrapper.unmount()
  })
})

describe('aggregation dimension configuration', () => {
  it('disables the create form and warns while meta options are still loading', async () => {
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/aggregation/meta') return new Promise(() => {})
      if (url.startsWith('/api/aggregation/dimensions')) return [dimension('d1', 'RD', '研发项目')]
      if (url.includes('/members')) return []
      return []
    })
    const wrapper = mount(AggregationDimensions)
    await flushPromises()

    expect(wrapper.text()).toContain('正在加载，请稍候再操作')
    const createButton = wrapper
      .findAll('button')
      .find((b) => b.text().includes('新建维度'))!
    expect(createButton.attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('archives a dimension with a PATCH then forces the dimension list to reload', async () => {
    let archived = false
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/aggregation/meta') return META
      if (url.startsWith('/api/aggregation/dimensions')) {
        return archived ? [] : [dimension('d1', 'RD', '研发项目')]
      }
      if (url.includes('/members')) return []
      return []
    })
    harness.patch.mockImplementation(async (url: string, body: any) => {
      if (url.includes('/dimensions/d1') && body.status === 'archived') archived = true
      return {}
    })

    const wrapper = mount(AggregationDimensions)
    await flushPromises()
    expect(wrapper.text()).toContain('RD')

    // 归档走二次确认：先断言确认弹层被触发，再手动执行其 onPositiveClick
    const archiveButton = wrapper.findAll('button').find((b) => b.text() === '归档')!
    await archiveButton.trigger('click')
    expect(harness.dialogWarning).toHaveBeenCalled()
    const config = harness.dialogWarning.mock.calls[0][0] as { onPositiveClick: () => Promise<void> }
    await config.onPositiveClick()
    await flushPromises()

    expect(harness.patch).toHaveBeenCalledWith('/api/aggregation/dimensions/d1', {
      status: 'archived',
    })
    expect(harness.success).toHaveBeenCalled()
    // 列表已刷新，归档项不再渲染
    expect(wrapper.text()).not.toContain('RD')
    wrapper.unmount()
  })

  it('lists members of the selected dimension and labels their status from meta', async () => {
    harness.get.mockImplementation(async (url: string) => {
      if (url === '/api/aggregation/meta') return META
      // ⚠️ members 的 URL 以 /dimensions 开头，必须先判 members
      if (url.includes('/members')) {
        return [
          {
            id: 'm1',
            dimensionId: 'd1',
            code: 'RD-2026-01',
            name: '研发项目A',
            startDate: '2026-01-01',
            endDate: null,
            status: 'active',
            metadata: {},
            createdAt: '',
            updatedAt: '',
          },
        ]
      }
      if (url.startsWith('/api/aggregation/dimensions')) return [dimension('d1', 'RD', '研发项目')]
      return []
    })
    const wrapper = mount(AggregationDimensions)
    await flushPromises()

    expect(wrapper.text()).toContain('RD-2026-01')
    // memberStatus 的中文来自 meta（'active' → '启用'）
    expect(wrapper.text()).toContain('启用')
    wrapper.unmount()
  })
})