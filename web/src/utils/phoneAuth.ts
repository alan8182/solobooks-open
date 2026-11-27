/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { ref, type Ref } from 'vue'

/**
 * 登录页与「微信首次登录绑定手机号」弹窗共用的手机号校验 / 倒计时。
 *
 * 抽出来是为了让两个入口使用同一套规则：手机号格式不一致会出现
 * 登录页能发码、绑定弹窗报格式错误的割裂体验。
 */

export const PHONE_PATTERN = /^1\d{10}$/

export function isValidPhone(value: string): boolean {
  return PHONE_PATTERN.test((value || '').trim())
}

export interface CountdownHandle {
  /** 发送中 / 倒计时进行中，用于禁用「获取验证码」按钮 */
  sending: Ref<boolean>
  /** 剩余秒数 */
  countdown: Ref<number>
  /** 开始倒计时（通常在验证码发送成功后调用） */
  start: () => void
  /** 停止并重置（组件卸载时务必调用，避免定时器泄漏） */
  stop: () => void
}

/** 创建一个 60 秒短信验证码倒计时；同一页面可创建多个互不影响。 */
export function createCountdown(seconds = 60): CountdownHandle {
  const sending = ref(false)
  const countdown = ref(0)
  let timer: ReturnType<typeof setInterval> | null = null

  function stop() {
    if (timer) clearInterval(timer)
    timer = null
    sending.value = false
    countdown.value = 0
  }

  function start() {
    if (timer) clearInterval(timer)
    sending.value = true
    countdown.value = seconds
    timer = setInterval(() => {
      countdown.value--
      if (countdown.value <= 0) {
        stop()
      }
    }, 1000)
  }

  return { sending, countdown, start, stop }
}

/** 从后端 / axios 错误里取可展示的 detail，取不到就用兜底文案。 */
export function apiDetail(err: unknown, fallback: string): string {
  const resp = err as { response?: { data?: { detail?: string } } }
  return resp?.response?.data?.detail || fallback
}
