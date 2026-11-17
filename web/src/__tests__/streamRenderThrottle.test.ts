/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  createMarkdownMemo,
  createStreamRenderThrottle,
  nextRenderIntervalMs,
} from '@/utils/streamRender'

describe('nextRenderIntervalMs', () => {
  it('渲染很便宜时取一帧下限 16ms', () => {
    expect(nextRenderIntervalMs(0)).toBe(16)
    expect(nextRenderIntervalMs(8)).toBe(16)
  })

  it('按渲染耗时的 2 倍抬门限', () => {
    expect(nextRenderIntervalMs(100)).toBe(200)
  })

  it('耗时长时封顶 2000ms', () => {
    expect(nextRenderIntervalMs(1500)).toBe(2000)
    expect(nextRenderIntervalMs(2000)).toBe(2000)
  })
})

describe('createStreamRenderThrottle', () => {
  /** 手动时钟：render 内推进 clock 模拟解析耗时，push 时的门限判定全部走同一时钟 */
  function makeClock() {
    let t = 0
    return {
      get: () => t,
      add: (ms: number) => {
        t += ms
      },
    }
  }

  beforeEach(() => {
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  it('复位后首字 token 立即渲染，不等节流间隔', () => {
    const seen: string[] = []
    const throttle = createStreamRenderThrottle((raw: string) => raw, (v) => seen.push(v))

    throttle.push('你好')

    expect(seen).toEqual(['你好'])
    throttle.dispose()
  })

  it('门限内连续 token 合并渲染；门限过后 push 立即渲染', () => {
    const clock = makeClock()
    const seen: string[] = []
    const throttle = createStreamRenderThrottle(
      (raw: string) => {
        clock.add(10) // 模拟每次解析耗时 10ms → 门限 = 20ms
        return raw
      },
      (v) => seen.push(v),
      { now: clock.get },
    )

    throttle.push('a') // 首字立即渲染；clock 0→10，门限到 t=30
    expect(seen).toEqual(['a'])

    clock.add(5) // t=15，门限未到
    throttle.push('ab') // 挂起定时器（15ms 后到点）
    throttle.push('abc') // 门限内合并到尾沿
    expect(seen).toEqual(['a'])

    vi.advanceTimersByTime(15) // 定时器到点，渲染最新内容；clock 15→25，门限到 t=45
    expect(seen).toEqual(['a', 'abc'])

    clock.add(20) // t=45，门限已过
    throttle.push('ad') // 不再挂定时器，立即渲染
    expect(seen).toEqual(['a', 'abc', 'ad'])
    throttle.dispose()
  })

  it('没有新内容时到点不重复渲染', () => {
    const seen: string[] = []
    const throttle = createStreamRenderThrottle((raw: string) => raw, (v) => seen.push(v))

    throttle.push('a')
    throttle.push('a') // 同内容 push 不触发第二次渲染
    vi.advanceTimersByTime(100)
    expect(seen).toEqual(['a'])
    throttle.dispose()
  })

  it('reset 丢弃未渲染尾巴并清掉定时器，新一轮流式从零开始', () => {
    const seen: string[] = []
    const throttle = createStreamRenderThrottle((raw: string) => raw, (v) => seen.push(v))

    throttle.push('a')
    throttle.push('ab')
    throttle.reset()
    vi.advanceTimersByTime(300)

    expect(seen).toEqual(['a'])

    throttle.push('新流首字')
    expect(seen).toEqual(['a', '新流首字']) // 复位后首字仍立即渲染
    throttle.dispose()
  })

  it('dispose 后定时器不再触发渲染（页面卸载清理）', () => {
    const seen: string[] = []
    const throttle = createStreamRenderThrottle((raw: string) => raw, (v) => seen.push(v))

    throttle.push('a')
    throttle.push('ab')
    throttle.dispose()
    vi.advanceTimersByTime(300)

    expect(seen).toEqual(['a'])
  })
})

describe('createMarkdownMemo', () => {
  it('同一消息内容未变不重复解析，内容变化才重解析', () => {
    let parsed = 0
    const memo = createMarkdownMemo((raw: string) => {
      parsed += 1
      return `<p>${raw}</p>`
    })
    const msg = {}

    expect(memo(msg, '第一段')).toBe('<p>第一段</p>')
    expect(memo(msg, '第一段')).toBe('<p>第一段</p>')
    expect(parsed).toBe(1)

    expect(memo(msg, '第一段第二段')).toBe('<p>第一段第二段</p>')
    expect(parsed).toBe(2)
  })

  it('不同消息对象各自缓存互不影响', () => {
    let parsed = 0
    const memo = createMarkdownMemo((raw: string) => {
      parsed += 1
      return raw
    })
    const msgA = {}
    const msgB = {}

    memo(msgA, '相同内容')
    memo(msgB, '相同内容')
    expect(parsed).toBe(2)
  })
})
