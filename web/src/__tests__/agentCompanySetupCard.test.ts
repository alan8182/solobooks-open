/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AgentActionCard from '@/components/business/AgentActionCard.vue'

const harness = vi.hoisted(() => ({
  approve: vi.fn(),
  warning: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}))

vi.mock('@/api/agent', () => ({
  approveAgentAction: harness.approve,
  rejectAgentAction: vi.fn(),
}))
vi.mock('@/api/accounting', () => ({ getBackfillTarget: vi.fn() }))
vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ token: '', fetchProfile: vi.fn() }),
}))
vi.mock('@/composables/usePayeeEmployees', async () => {
  const { ref } = await import('vue')
  return {
    usePayeeEmployees: () => ({
      loading: ref(false),
      loaded: ref(true),
      list: ref([]),
      ensure: vi.fn(),
      options: () => [],
    }),
  }
})
vi.mock('@/composables/startDateGuide', () => ({ guideStartDate: () => false }))
vi.mock('naive-ui', async (importOriginal) => ({
  ...await importOriginal<typeof import('naive-ui')>(),
  useMessage: () => ({
    warning: harness.warning,
    success: harness.success,
    error: harness.error,
    info: vi.fn(),
  }),
}))

function makeAction(taxId = '') {
  return {
    id: 'a-company-setup',
    actionType: 'propose_company_setup',
    status: 'pending',
    title: '新公司从零建账确认',
    payload: {
      onboardingType: 'new',
      name: '杭州测试科技有限公司',
      taxId,
      type: '有限责任公司',
      taxpayerType: '小规模纳税人',
      startDate: '2026-11-01',
      industry: '软件和信息技术服务业',
      isFromDemo: true,
    },
  } as any
}

function mountCard(taxId = '') {
  return mount(AgentActionCard, { props: { action: makeAction(taxId) } })
}

async function clickApprove(wrapper: ReturnType<typeof mountCard>) {
  const submit = wrapper.findAll('button').find((btn) => btn.text().includes('确认创建正式账套'))
  expect(submit).toBeTruthy()
  await submit!.trigger('click')
  await flushPromises()
}

beforeEach(() => {
  harness.approve.mockReset()
  harness.warning.mockReset()
  harness.success.mockReset()
  harness.error.mockReset()
  localStorage.clear()
})

describe('对话建账卡税号必填', () => {
  it('税号留空不能确认，也不发审批请求', async () => {
    const wrapper = mountCard()

    await clickApprove(wrapper)

    expect(harness.warning).toHaveBeenCalledWith('请填写统一社会信用代码')
    expect(harness.approve).not.toHaveBeenCalled()
  })

  it('税号格式不对不能确认', async () => {
    const wrapper = mountCard()
    await wrapper.find('input[placeholder="15/17/18/20 位字母数字"]').setValue('abc')

    await clickApprove(wrapper)

    expect(harness.warning).toHaveBeenCalledWith('统一社会信用代码格式不正确（15/17/18/20 位字母数字）')
    expect(harness.approve).not.toHaveBeenCalled()
  })

  it('填全税号后按大写提交', async () => {
    harness.approve.mockResolvedValue({ result: { switchedFromDemo: false } })
    const wrapper = mountCard()
    await wrapper.find('input[placeholder="15/17/18/20 位字母数字"]').setValue('91330100ma2test001')

    await clickApprove(wrapper)

    expect(harness.approve).toHaveBeenCalledWith(
      'a-company-setup',
      { edits: { '0': expect.objectContaining({ taxId: '91330100MA2TEST001' }) } },
    )
  })
})
