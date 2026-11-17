/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import CopilotPage from '@/pages/copilot/index.vue'

// 回归背景：流式回复每收一个 token 就对累计文本全量重跑 markdown 解析 +
// 消毒（v-html 直接绑 renderCopilotMarkdown(msg.content)），长回复把主线程吃满。
// 现在的规则：流式消息走节流渲染（首字立即、其后按 120ms 尾沿合并），
// msg.content 始终是完整权威文本；流式结束切回完整直渲，节流尾巴不丢字；
// 历史消息内容不变时不重复解析。
const harness = vi.hoisted(() => ({
  onEvent: null as ((ev: Record<string, unknown>) => void) | null,
  streamPromise: Promise.resolve() as Promise<void>,
  resolveStream: null as (() => void) | null,
  get: vi.fn(),
  post: vi.fn(),
  patch: vi.fn(),
  push: vi.fn(),
  replace: vi.fn(),
  warning: vi.fn(),
  error: vi.fn(),
  success: vi.fn(),
  info: vi.fn(),
  route: { query: {} as Record<string, string> },
  entryStore: null as any,
  loadCopilotChat: vi.fn(() => [] as unknown[]),
  saveCopilotChat: vi.fn(),
  clearCopilotChat: vi.fn(),
}))

vi.mock('@/api/agent', () => ({
  chatAgentStream: vi.fn((_history: unknown, onEvent: (ev: Record<string, unknown>) => void) => {
    harness.onEvent = onEvent
    return harness.streamPromise
  }),
  listAgentActions: vi.fn(async () => []),
  getAgentAction: vi.fn(async () => null),
}))
vi.mock('@/api/record', () => ({ recordReceipt: vi.fn(), recordSpeech: vi.fn() }))
vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({ currentCompanyId: 'c_test_company', companies: [] }),
}))
vi.mock('@/stores/entry', () => ({ useEntryStore: () => harness.entryStore }))
vi.mock('vue-router', () => ({
  useRoute: () => harness.route,
  useRouter: () => ({ push: harness.push, replace: harness.replace }),
}))
vi.mock('@/utils/copilotChat', () => ({
  loadCopilotChat: harness.loadCopilotChat,
  saveCopilotChat: harness.saveCopilotChat,
  clearCopilotChat: harness.clearCopilotChat,
}))
vi.mock('naive-ui', async (importOriginal) => ({
  ...await importOriginal<typeof import('naive-ui')>(),
  useMessage: () => ({
    warning: harness.warning,
    error: harness.error,
    success: harness.success,
    info: harness.info,
  }),
}))
// 解析计数探针：包一层透传 spy，统计真实解析次数
vi.mock('@/utils/markdown', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/markdown')>()
  return { ...actual, renderMarkdown: vi.fn(actual.renderMarkdown) }
})

import { renderMarkdown } from '@/utils/markdown'
const renderSpy = vi.mocked(renderMarkdown)

function mountPage() {
  return mount(CopilotPage, {
    global: {
      stubs: {
        AgentActionCard: true,
        CopilotEvidenceBoard: true,
        NTooltip: true,
      },
    },
  })
}

async function sendMessage(wrapper: ReturnType<typeof mountPage>) {
  await wrapper.find('textarea').setValue('盘点一下账上的问题')
  const sendBtn = wrapper.findAll('button').find((b) => b.text().includes('发送'))
  expect(sendBtn).toBeTruthy()
  await sendBtn!.trigger('click')
  await flushPromises()
  expect(harness.onEvent).toBeTruthy()
}

beforeEach(() => {
  let resolveStream!: () => void
  harness.streamPromise = new Promise<void>((resolve) => { resolveStream = resolve })
  harness.resolveStream = resolveStream
  harness.onEvent = null
  harness.route.query = {}
  harness.loadCopilotChat.mockReset().mockReturnValue([])
  harness.saveCopilotChat.mockReset()
  harness.clearCopilotChat.mockReset()
  harness.entryStore = reactive({ fetchEntries: vi.fn(async () => {}), monthlyIncome: 0, monthlyExpense: 0, netProfit: 0 })
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('copilot 流式渲染节流', () => {
  it('流式期间不再逐 token 全量解析 markdown，间隔内合并渲染', async () => {
    vi.useFakeTimers()
    const wrapper = mountPage()
    await flushPromises()
    await sendMessage(wrapper)
    renderSpy.mockClear()

    harness.onEvent!({ type: 'token', content: '盘点结果如下：' })
    await flushPromises()
    expect(renderSpy).toHaveBeenCalledTimes(1) // 首字立即渲染

    for (let i = 1; i <= 9; i += 1) {
      harness.onEvent!({ type: 'token', content: `跟进${i}。` })
    }
    await flushPromises()
    expect(renderSpy).toHaveBeenCalledTimes(1) // 间隔内不再逐 token 解析

    vi.advanceTimersByTime(130)
    await flushPromises()
    expect(renderSpy).toHaveBeenCalledTimes(2) // 尾沿合并为一次
    wrapper.unmount()
  })

  it('流式结束后正文完整直渲，节流尾巴不丢字', async () => {
    vi.useFakeTimers()
    const wrapper = mountPage()
    await flushPromises()
    await sendMessage(wrapper)

    harness.onEvent!({ type: 'token', content: '第一句。' })
    await flushPromises()
    harness.onEvent!({ type: 'token', content: '**第二句**' })
    await flushPromises()

    // 尾巴「**第二句**」还在节流等待中；流式结束必须切回完整渲染
    harness.resolveStream!()
    await flushPromises()

    const bubbles = wrapper.findAll('.md-body')
    expect(bubbles.length).toBeGreaterThan(0)
    const last = bubbles[bubbles.length - 1]
    expect(last.text()).toContain('第一句。')
    expect(last.text()).toContain('第二句')
    expect(last.text()).not.toContain('**') // 渲染的是 markdown 结果，不是原文
    wrapper.unmount()
  })

  it('历史消息内容不变时，流式期间整表重渲染不重复解析', async () => {
    vi.useFakeTimers()
    const wrapper = mountPage()
    await flushPromises()
    await sendMessage(wrapper)

    harness.onEvent!({ type: 'token', content: '第一轮回答内容' })
    harness.resolveStream!()
    await flushPromises()

    const round1Calls = () => renderSpy.mock.calls.filter(([raw]) => raw === '第一轮回答内容').length
    const before = round1Calls()
    expect(before).toBeGreaterThanOrEqual(1)

    // 第二轮流式渲染触发整条消息列表重渲染，第一轮消息走 memo
    await wrapper.find('textarea').setValue('第二个问题')
    const sendBtn = wrapper.findAll('button').find((b) => b.text().includes('发送'))
    await sendBtn!.trigger('click')
    harness.onEvent!({ type: 'token', content: '第二轮回答内容' })
    await flushPromises()
    vi.advanceTimersByTime(130)
    await flushPromises()

    expect(round1Calls()).toBe(before)
    wrapper.unmount()
  })
})

describe('copilot 输入框 IME 回车守卫', () => {
  // 回归背景：@keydown.enter.prevent 直发 send()，中文输入法选词的回车
  // （isComposing=true）会把拼音串/半截话直接发出去。
  it('中文输入法选词的回车不发送，普通回车发送', async () => {
    const wrapper = mountPage()
    await flushPromises()
    const textarea = wrapper.find('textarea')
    await textarea.setValue('盘点一下账上的问题')

    await textarea.trigger('keydown', { key: 'Enter', isComposing: true })
    expect(harness.onEvent).toBeNull() // 选词回车：未发送

    await textarea.trigger('keydown', { key: 'Enter', isComposing: false })
    await flushPromises()
    expect(harness.onEvent).toBeTruthy() // 普通回车：已发送
    wrapper.unmount()
  })
})
