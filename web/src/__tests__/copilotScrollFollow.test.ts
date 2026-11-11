/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { reactive } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import CopilotPage from '@/pages/copilot/index.vue'

// 回归背景：AI 流式回复时每个 token 都无条件 scrollTop = scrollHeight，
// 用户上翻阅读历史/思考过程会被不断拽回底部（跳动）。
// 现在的规则：scrollTop 减小 = 用户上翻 → 暂停跟随；滚回底部附近 / 点
// 「回到底部」按钮 / 发送新消息 → 恢复跟随。
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

// jsdom 没有布局，滚动几何全部手动接管；必须在 mount 同步返回后、
// flushPromises（onMounted / nextTick 回调执行）之前绑定，才能观察到钉底写入。
function bindScrollGeometry(el: HTMLElement) {
  const geo = { scrollTop: 0, scrollHeight: 600, clientHeight: 400 }
  Object.defineProperty(el, 'scrollTop', {
    configurable: true,
    get: () => geo.scrollTop,
    set: (v: number) => { geo.scrollTop = v },
  })
  Object.defineProperty(el, 'scrollHeight', {
    configurable: true,
    get: () => geo.scrollHeight,
  })
  Object.defineProperty(el, 'clientHeight', {
    configurable: true,
    get: () => geo.clientHeight,
  })
  return geo
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
  vi.clearAllMocks()
})

describe('copilot 流式输出贴底跟随', () => {
  it('用户停在底部时流式输出持续贴底，且不出现回底按钮', async () => {
    const wrapper = mountPage()
    const el = wrapper.find('.chat-card').element as HTMLElement
    const geo = bindScrollGeometry(el)
    await flushPromises()

    await sendMessage(wrapper)
    expect(geo.scrollTop).toBe(600) // 发送后强制回底

    geo.scrollHeight = 900
    harness.onEvent!({ type: 'reasoning', content: '先查公司画像' })
    await flushPromises()
    expect(geo.scrollTop).toBe(900)

    geo.scrollHeight = 1100
    harness.onEvent!({ type: 'token', content: '盘点结果如下：' })
    await flushPromises()
    expect(geo.scrollTop).toBe(1100)
    expect(wrapper.find('.scroll-bottom-btn').exists()).toBe(false)

    harness.resolveStream!()
    await flushPromises()
    expect(wrapper.text()).toContain('发送') // 流结束后恢复发送按钮
    wrapper.unmount()
  })

  it('用户上翻阅读后流式输出不再拽回底部，点回底按钮才回去', async () => {
    const wrapper = mountPage()
    const el = wrapper.find('.chat-card').element as HTMLElement
    const geo = bindScrollGeometry(el)
    await flushPromises()

    await sendMessage(wrapper)

    geo.scrollHeight = 900
    harness.onEvent!({ type: 'reasoning', content: '查公司画像与账龄' })
    await flushPromises()
    expect(geo.scrollTop).toBe(900)

    // 用户向上滚动：scrollTop 减小、scrollHeight 不变
    geo.scrollTop = 120
    el.dispatchEvent(new Event('scroll'))
    await flushPromises()

    // 内容继续增长（scrollHeight 变大，scrollTop 未动）
    geo.scrollHeight = 1000
    harness.onEvent!({ type: 'token', content: '盘点结果：' })
    await flushPromises()

    // 根因回归断言：流式事件不得改变用户当前阅读位置
    expect(geo.scrollTop).toBe(120)
    // 思考框不被自动折叠（展开态显示「收起」）
    expect(wrapper.find('.reasoning-toggle').text()).toContain('收起')
    // 出现回底按钮
    const btn = wrapper.find('.scroll-bottom-btn')
    expect(btn.exists()).toBe(true)

    await btn.trigger('click')
    await flushPromises()
    expect(geo.scrollTop).toBe(1000)
    expect(wrapper.find('.scroll-bottom-btn').exists()).toBe(false)
    wrapper.unmount()
  })

  it('上翻后手动滚回底部附近即恢复自动跟随', async () => {
    const wrapper = mountPage()
    const el = wrapper.find('.chat-card').element as HTMLElement
    const geo = bindScrollGeometry(el)
    await flushPromises()

    await sendMessage(wrapper)
    geo.scrollHeight = 1000
    harness.onEvent!({ type: 'reasoning', content: '思考中' })
    await flushPromises()

    geo.scrollTop = 100
    el.dispatchEvent(new Event('scroll'))
    await flushPromises()
    harness.onEvent!({ type: 'token', content: '一段' })
    await flushPromises()
    expect(geo.scrollTop).toBe(100) // 未跟随

    // 用户滚回底部（max scrollTop = 1000 - 400）
    geo.scrollTop = 600
    el.dispatchEvent(new Event('scroll'))
    await flushPromises()
    expect(wrapper.find('.scroll-bottom-btn').exists()).toBe(false)

    geo.scrollHeight = 1200
    harness.onEvent!({ type: 'token', content: '继续' })
    await flushPromises()
    expect(geo.scrollTop).toBe(1200) // 已恢复贴底
    wrapper.unmount()
  })

  it('内容收缩导致的滚动钳位不误判为用户上翻', async () => {
    const wrapper = mountPage()
    const el = wrapper.find('.chat-card').element as HTMLElement
    const geo = bindScrollGeometry(el)
    await flushPromises()

    await sendMessage(wrapper)
    geo.scrollHeight = 900
    harness.onEvent!({ type: 'reasoning', content: '思考中' })
    await flushPromises()
    expect(geo.scrollTop).toBe(900)

    // 思考框折叠 → 内容收缩，scrollTop 被钳位减小（scrollHeight 同减）
    geo.scrollHeight = 700
    geo.scrollTop = 300
    el.dispatchEvent(new Event('scroll'))
    await flushPromises()
    expect(wrapper.find('.scroll-bottom-btn').exists()).toBe(false)

    geo.scrollHeight = 800
    harness.onEvent!({ type: 'token', content: '正文' })
    await flushPromises()
    expect(geo.scrollTop).toBe(800) // 仍在跟随
    wrapper.unmount()
  })

  it('恢复历史会话后直接落在最新一条', async () => {
    harness.loadCopilotChat.mockReturnValue([
      { role: 'user', content: '上一轮问题' },
      { role: 'assistant', content: '上一轮回答' },
    ])
    const wrapper = mountPage()
    const el = wrapper.find('.chat-card').element as HTMLElement
    const geo = bindScrollGeometry(el)
    geo.scrollHeight = 900
    await flushPromises()

    expect(geo.scrollTop).toBe(900)
    expect(wrapper.find('.scroll-bottom-btn').exists()).toBe(false)
    wrapper.unmount()
  })
})
