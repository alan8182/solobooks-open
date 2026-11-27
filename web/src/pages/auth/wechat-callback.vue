<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { exchangeWechatWebTicket } from '@/api/auth'
import type { WechatWebExchangeResult } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { safeRedirectTarget } from '@/router/guards'

/**
 * 微信开放平台回调中转页。
 *
 * 后端 callback 校验 state 后把 ticket 写进 HttpOnly `wx_web_pending` cookie，
 * 再 302 到这里；URL 里只有状态标记，拿不到 ticket。
 *
 * - 弹窗路径（window.opener 存在）：只给 opener 发「ready」，由登录页调 exchange；
 * - 内嵌 iframe 路径（无 opener、被登录页内嵌）：结果 postMessage 给 window.parent，
 *   不在 iframe 里自行跳转；微信侧报错以 error 消息回传，由登录页清码提示；
 * - 整页回退路径（既无 opener 也非内嵌）：自己调 exchange，成功后整页跳转。
 */

const WECHAT_MESSAGE_TYPE = 'solo-wechat-login'

const ERROR_TEXT: Record<string, string> = {
  access_denied: '你取消了微信授权，请返回登录页重试',
  state_invalid: '登录二维码已过期，请返回登录页重新扫码',
  state_expired: '登录二维码已过期，请返回登录页重新扫码',
  config_missing: '微信登录暂未开放，请使用手机号登录',
  code_missing: '未收到微信授权结果，请返回登录页重新扫码',
  ticket_expired: '登录已超时，请返回登录页重新扫码',
  ticket_invalid: '登录已失效，请返回登录页重新扫码',
  identity_conflict: '该微信已绑定其他账号，请使用手机号登录或联系客服',
  account_disabled: '该账号已被停用，请联系客服',
  network: '微信服务暂时不可用，请稍后重试',
  wechat_error: '微信授权失败，请返回登录页重试',
}
const ERROR_FALLBACK = '微信登录失败，请返回登录页重试或使用手机号登录'

const route = useRoute()
const authStore = useAuthStore()

const statusText = ref('正在完成微信登录…')
const statusHint = ref('')
const succeeded = ref(false)
/** 失败时展示「返回登录页」兜底链接（自动跳转被浏览器拦截时仍可点） */
const failedRedirect = ref('')

function queryString(key: string): string {
  const raw = route.query[key]
  if (typeof raw === 'string') return raw
  if (Array.isArray(raw) && raw.length > 0) return String(raw[0] ?? '')
  return ''
}

function errorText(code: string): string {
  return ERROR_TEXT[code] || ERROR_FALLBACK
}

function redirectTarget(): string {
  return safeRedirectTarget(queryString('redirect')) || '/'
}

/** 回登录页时保留安全 redirect，避免丢失用户原本要去的页面。 */
function loginUrl(error?: string): string {
  const params = new URLSearchParams()
  const redirect = safeRedirectTarget(queryString('redirect'))
  if (redirect) params.set('redirect', redirect)
  if (error) params.set('wechat_error', error)
  const qs = params.toString()
  return qs ? `/login?${qs}` : '/login'
}

function goLogin(errorCode: string) {
  const target = loginUrl(errorCode)
  failedRedirect.value = target
  statusText.value = errorText(errorCode)
  statusHint.value = '正在返回登录页…'
  window.location.replace(target)
}

function isPhoneRequired(
  result: WechatWebExchangeResult,
): result is { phoneRequired: true } {
  return Boolean((result as { phoneRequired?: boolean })?.phoneRequired)
}

/** 后端错误码优先；拿不到可读错误码时按 HTTP 状态归类，不把原始报文透给用户。 */
function exchangeErrorCode(err: unknown): string {
  const resp = err as {
    response?: { status?: number; data?: { detail?: unknown } }
  }
  const detail = resp?.response?.data?.detail
  if (typeof detail === 'string' && /^[a-z_]{3,40}$/.test(detail)) return detail
  const status = resp?.response?.status
  if (status === 410) return 'ticket_expired'
  // exchange 的 400 只有「pending cookie 缺失 / ticket 已消费或过期」一种语义
  if (status === 400) return 'ticket_invalid'
  if (status && status >= 500) return 'network'
  return 'exchange_failed'
}

onMounted(async () => {
  // 后端回调失败时可能直接带 wechat_error / error 落到这里；
  // 成功时后端只带 status=ready（ticket 在 HttpOnly cookie 里）。
  const status = queryString('status')
  const upstreamError =
    queryString('wechat_error') || queryString('error') || (status && status !== 'ready' ? status : '')
  if (window.opener) {
    if (upstreamError) {
      // 弹窗里直接导航回登录页展示错误（保持原行为）
      goLogin(upstreamError)
      return
    }
    // 弹窗路径：只通知 opener「可以 exchange 了」，ticket 留在 HttpOnly cookie 里
    try {
      window.opener.postMessage(
        { type: WECHAT_MESSAGE_TYPE, status: 'ready' },
        window.location.origin,
      )
    } catch {
      // opener 已关闭或不可访问：降级为提示用户在登录页重试
    }
    succeeded.value = true
    statusText.value = '微信授权成功'
    statusHint.value = '正在返回登录窗口完成登录；若本窗口未自动关闭，可手动关闭。'
    try {
      window.close()
    } catch {
      // 浏览器允许脚本关闭由脚本打开的窗口；被拦截时保留提示文案
    }
    return
  }

  if (window.parent && window.parent !== window) {
    // 内嵌 iframe 路径：结果交回登录页，不在 iframe 里自行跳转。
    // ticket 仍在 HttpOnly cookie（同站共享 cookie jar），登录页收到后自行 exchange。
    const message = upstreamError
      ? { type: WECHAT_MESSAGE_TYPE, status: 'error', code: upstreamError }
      : { type: WECHAT_MESSAGE_TYPE, status: 'ready' }
    try {
      window.parent.postMessage(message, window.location.origin)
    } catch {
      // 父窗口不可访问：用户可手动刷新登录页重试
    }
    succeeded.value = !upstreamError
    statusText.value = upstreamError ? errorText(upstreamError) : '微信授权成功'
    statusHint.value = '正在返回登录页…'
    return
  }

  // 整页回退路径：没有 opener 也不是内嵌，自己换登录态
  if (upstreamError) {
    goLogin(upstreamError)
    return
  }
  try {
    const result = await exchangeWechatWebTicket()
    if (isPhoneRequired(result)) {
      const params = new URLSearchParams({ wechat_pending: '1' })
      const redirect = safeRedirectTarget(queryString('redirect'))
      if (redirect) params.set('redirect', redirect)
      window.location.replace(`/login?${params.toString()}`)
      return
    }
    authStore.setSession(result)
    await authStore.fetchProfile()
    succeeded.value = true
    statusText.value = '登录成功'
    statusHint.value = '正在进入工作台…'
    window.location.replace(redirectTarget())
  } catch (err: unknown) {
    goLogin(exchangeErrorCode(err))
  }
})
</script>

<template>
  <div class="wechat-callback-page">
    <div class="callback-card">
      <div class="status-icon" :class="{ done: succeeded }" aria-hidden="true">
        <i :class="succeeded ? 'ri-check-line' : 'ri-wechat-fill'" />
      </div>
      <p class="status-text">{{ statusText }}</p>
      <p v-if="statusHint" class="status-hint">{{ statusHint }}</p>
      <a v-if="!succeeded && failedRedirect" class="back-link" :href="failedRedirect">
        返回登录页
      </a>
    </div>
  </div>
</template>

<style scoped>
.wechat-callback-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: var(--solo-bg-soft);
}

.callback-card {
  width: 100%;
  max-width: 360px;
  padding: 36px 28px;
  border-radius: var(--solo-radius-lg);
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  text-align: center;
}

.status-icon {
  width: 52px;
  height: 52px;
  margin: 0 auto 16px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 26px;
  color: var(--solo-success);
  background: var(--solo-bg-soft);
  border: 1px solid var(--solo-border-light);
}

.status-text {
  margin: 0 0 8px;
  font-size: 15px;
  font-weight: 600;
  color: var(--solo-text-primary);
}

.status-hint {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--solo-text-muted);
}

.back-link {
  display: inline-block;
  margin-top: 12px;
  font-size: 13px;
  font-weight: 500;
  color: var(--solo-primary);
  text-decoration: none;
}

.back-link:hover {
  color: var(--solo-primary-light);
}
</style>
