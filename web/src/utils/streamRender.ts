/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 流式回复的渲染节流（自适应间隔）。
 *
 * 背景：AI 聊天每收一个 token 就把累计文本全量重跑 markdown 解析 + 消毒，
 * 长回复会把主线程吃满。节流策略借鉴 DeepSeek chat-demo 的
 * useThrottleMarkdownRender：按「上次渲染实际耗时」自适应安排下次渲染——
 * 耗时 × TIME_CONSUMING_RATE，下限一帧、上限 MAX_WAIT_MS。解析越慢
 * （长报告大表格、低端机）退让越多，快则贴近逐 token 上屏。
 *
 * 不变量：数据（msg.content）始终是完整权威文本，节流只作用于渲染值；
 * 渲染值最多滞后一个间隔，流式结束后调用方切回完整直渲，尾巴不会丢。
 */

/** 两次渲染的最长间隔（上次解析极慢时限速到 0.5Hz） */
const MAX_WAIT_MS = 2000
/** 一帧的下限，短文本解析很便宜时避免以极高频率重复解析 */
const MIN_INTERVAL_MS = 16
/** 下次间隔 = 上次渲染耗时 × 该倍数 */
const TIME_CONSUMING_RATE = 2

/** 按上次渲染耗时估算下次允许渲染的间隔 */
export function nextRenderIntervalMs(elapsedMs: number): number {
  return Math.min(MAX_WAIT_MS, Math.max(MIN_INTERVAL_MS, elapsedMs * TIME_CONSUMING_RATE))
}

/** 流式渲染节流器：复位后首字立即渲染，其后按自适应间隔合并渲染。 */
export interface StreamRenderThrottle {
  /** token 到达：登记最新源文本并按自适应间隔安排渲染 */
  push(raw: string): void
  /** 新一轮流开始前 / 流结束后复位：丢弃未渲染尾巴与定时器 */
  reset(): void
  /** 组件卸载时清理定时器 */
  dispose(): void
}

/**
 * @param render 解析源文本为渲染值（解析耗时在这里被测量）
 * @param notify 渲染完成回调（页面侧把结果写进 ref → 模板绑定）
 * @param opts.now 时间源，默认 performance.now()；测试注入手动时钟
 */
export function createStreamRenderThrottle<T>(
  render: (raw: string) => T,
  notify: (value: T) => void,
  opts?: { now?: () => number },
): StreamRenderThrottle {
  const now = opts?.now ?? (() => performance.now())
  let timer: ReturnType<typeof setTimeout> | null = null
  let pending = ''
  let renderedRaw = ''
  let nextUpdatableAt = 0

  function doRender() {
    timer = null
    if (pending === renderedRaw) return
    const raw = pending
    const start = now()
    notify(render(raw))
    renderedRaw = raw
    nextUpdatableAt = now() + nextRenderIntervalMs(now() - start)
  }

  return {
    push(raw: string) {
      pending = raw
      if (renderedRaw === '' && !timer) {
        // 复位后的首字立即上屏
        doRender()
        return
      }
      if (timer) return
      const delay = nextUpdatableAt - now()
      if (delay <= 0) {
        doRender()
      } else {
        timer = setTimeout(doRender, delay)
      }
    },
    reset() {
      if (timer) {
        clearTimeout(timer)
        timer = null
      }
      pending = ''
      renderedRaw = ''
      nextUpdatableAt = 0
    },
    dispose() {
      if (timer) {
        clearTimeout(timer)
        timer = null
      }
    },
  }
}

/**
 * 按消息对象记忆 markdown 渲染结果：内容没变就不重解析。
 * 流式期间整条消息列表随每个 token 重渲染，静态历史消息
 * 不能跟着反复跑解析与消毒。
 */
export function createMarkdownMemo<T>(render: (raw: string) => T) {
  const cache = new WeakMap<object, { raw: string; value: T }>()
  return (key: object, raw: string): T => {
    const hit = cache.get(key)
    if (hit && hit.raw === raw) return hit.value
    const value = render(raw)
    cache.set(key, { raw, value })
    return value
  }
}
