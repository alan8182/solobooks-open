/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import AskAiPanel from '@/pages/closing-wizard/AskAiPanel.vue'

// 回归背景：问 AI 弹窗与 copilot 同款热点——token 逐字累加 + 每 token 全量
// markdown 解析。现在与 copilot 共用 utils/streamRender 的节流策略：
// 流式占位消息走节流渲染，结束切回完整直渲；error 另推的消息不能串到
// 流式占位上（所以承接目标必须显式标记，不能按「最后一条」猜）。
const harness = vi.hoisted(() => ({
  onEvent: null as ((ev: Record<string, unknown>) => void) | null,
  streamPromise: Promise.resolve() as Promise<void>,
  resolveStream: null as (() => void) | null,
  warning: vi.fn(),
}))

vi.mock('@/api/agent', () => ({
  chatAgentStream: vi.fn((_history: unknown, onEvent: (ev: Record<string, unknown>) => void) => {
    harness.onEvent = onEvent
    return harness.streamPromise
  }),
}))
vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return {
    ...actual,
    // NModal 默认 teleport 到 body，替换成就地渲染的假壳才能断言内部气泡
    NModal: { props: ['show'], template: '<div v-if="show"><slot /></div>' },
    useMessage: () => ({ warning: harness.warning }),
  }
})

function mountPanel() {
  return mount(AskAiPanel, {
    props: {
      stmt: null,
      stmtTitle: '测试流水',
      prompt: '这笔怎么记？',
      candidateLines: [],
      fallbackCategory: '',
      confirming: false,
      show: false,
    },
  })
}

async function openPanel(wrapper: ReturnType<typeof mountPanel>) {
  await wrapper.setProps({ show: true })
  await flushPromises()
  expect(harness.onEvent).toBeTruthy()
}

beforeEach(() => {
  let resolveStream!: () => void
  harness.streamPromise = new Promise<void>((resolve) => { resolveStream = resolve })
  harness.resolveStream = resolveStream
  harness.onEvent = null
})

afterEach(() => {
  vi.useRealTimers()
  vi.clearAllMocks()
})

describe('问 AI 弹窗流式渲染节流', () => {
  it('流式结束后正文完整直渲，节流尾巴不丢字', async () => {
    vi.useFakeTimers()
    const wrapper = mountPanel()
    await flushPromises()
    await openPanel(wrapper)

    harness.onEvent!({ type: 'token', content: '建议记入' })
    await flushPromises()
    harness.onEvent!({ type: 'token', content: '**备用金**科目。' })
    await flushPromises()

    // 尾巴「**备用金**科目。」还在节流等待中；流式结束必须切回完整渲染
    harness.resolveStream!()
    await flushPromises()

    const bubbles = wrapper.findAll('.ask-ai-bubble')
    expect(bubbles.length).toBeGreaterThan(0)
    const last = bubbles[bubbles.length - 1]
    expect(last.text()).toContain('建议记入')
    expect(last.text()).toContain('备用金')
    expect(last.text()).not.toContain('**') // 渲染的是 markdown 结果，不是原文
    wrapper.unmount()
  })

  it('error 另推的消息独立成气泡，不串流式占位内容', async () => {
    vi.useFakeTimers()
    const wrapper = mountPanel()
    await flushPromises()
    await openPanel(wrapper)

    harness.onEvent!({ type: 'token', content: '部分内容' })
    await flushPromises()
    harness.onEvent!({ type: 'error', message: 'AI 处理失败' })
    harness.resolveStream!()
    await flushPromises()

    const texts = wrapper.findAll('.ask-ai-bubble').map((b) => b.text())
    expect(texts[texts.length - 1]).toBe('⚠ AI 处理失败')
    expect(texts.some((t) => t.includes('部分内容'))).toBe(true)
    wrapper.unmount()
  })

  it('中文输入法选词的回车不发送，普通回车发送', async () => {
    const wrapper = mountPanel()
    await flushPromises()
    await openPanel(wrapper)
    harness.resolveStream!()
    await flushPromises()
    const bubblesBefore = wrapper.findAll('.ask-ai-bubble').length

    const textarea = wrapper.find('textarea')
    await textarea.setValue('再问一句')
    await textarea.trigger('keydown', { key: 'Enter', isComposing: true })
    expect(wrapper.findAll('.ask-ai-bubble').length).toBe(bubblesBefore) // 选词回车：未发送

    await textarea.trigger('keydown', { key: 'Enter', isComposing: false })
    await flushPromises()
    // 普通回车：已发送，新增用户气泡（占位气泡内容为空暂不渲染）
    expect(wrapper.findAll('.ask-ai-bubble').length).toBe(bubblesBefore + 1)
    wrapper.unmount()
  })
})
