<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref, watch, onMounted, onUnmounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NInput,
  NButton,
  NForm,
  NFormItem,
  NAlert,
  NCheckbox,
  NTabs,
  NTabPane,
  NModal,
  NSpin,
  useMessage,
} from 'naive-ui'
import {
  sendPhoneCode,
  loginByPhoneCode,
  loginByPassword,
  resetPassword,
  getWechatWebConfig,
  getWechatWebAuthorizeUrl,
  exchangeWechatWebTicket,
  bindWechatWebPhone,
} from '@/api/auth'
import type { AuthResponse, WechatWebExchangeResult } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { safeRedirectTarget } from '@/router/guards'
import { apiDetail, createCountdown, isValidPhone } from '@/utils/phoneAuth'

// ICP 备案号，页脚自动展示并链接工信部网站
const ICP_LICENSE = '浙ICP备2025195764号-7'
// 公安备案号，链接公安部备案查询网站
const PSB_RECORD = '浙公网安备 33011002020894号'
const PSB_RECORD_URL = 'https://beian.mps.gov.cn/#/query/webSearch?code=33011002020894'

const message = useMessage()
const authStore = useAuthStore()
const route = useRoute()
const router = useRouter()

// 官网定价区 CTA（/landing → /login?plan=xxx）：登录成功后直接进入升级流程。
// trial 为免费体验版本身，不视为购买意向。
const planIntent =
  typeof route.query.plan === 'string' && route.query.plan && route.query.plan !== 'trial'
    ? route.query.plan
    : ''

const activeTab = ref<'code' | 'password' | 'wechat'>('code')
const errorMsg = ref('')

watch(activeTab, () => {
  errorMsg.value = ''
})

// ── 验证码登录 ──
const phone = ref('')
const code = ref('')
const loading = ref(false)

const codeCd = createCountdown()

onUnmounted(() => {
  codeCd.stop()
  fpCd.stop()
  bindCd.stop()
  teardownWechatViewport()
  window.removeEventListener('message', handleWechatMessage)
})

async function handleSendCode() {
  if (!isValidPhone(phone.value)) {
    message.warning('请输入正确的手机号')
    return
  }
  codeCd.sending.value = true
  try {
    await sendPhoneCode({ phone: phone.value })
    message.success('验证码已发送')
    codeCd.start()
  } catch (err: unknown) {
    message.error(apiDetail(err, '发送失败'))
    codeCd.sending.value = false
  }
}

async function finishLogin(resp: AuthResponse) {
  authStore.setSession(resp)
  await authStore.fetchProfile()
  message.success('登录成功')
  try {
    sessionStorage.removeItem('solo:setup-password-hint')
  } catch { /* ignore */ }
  // Reload the app from its authenticated entry point. This avoids a stale
  // login route or a rejected client-side navigation swallowing the jump.
  // 带套餐意向时直达独立订阅页 /mine/plan?plan=xxx；
  // 否则若有安全的站内 redirect（如 /console、/console/users）则优先回跳。
  const redirectTarget = safeRedirectTarget(route.query.redirect)
  window.location.replace(
    planIntent
      ? `/mine/plan?plan=${encodeURIComponent(planIntent)}`
      : redirectTarget || '/',
  )
}

async function handleCodeLogin() {
  if (!phone.value || !code.value) {
    message.warning('请填写手机号和验证码')
    return
  }
  loading.value = true
  errorMsg.value = ''
  try {
    await finishLogin(await loginByPhoneCode({ phone: phone.value, code: code.value }))
  } catch (err: unknown) {
    errorMsg.value = apiDetail(err, '登录失败，请重试')
  } finally {
    loading.value = false
  }
}

// ── 密码登录 ──
const pwPhone = ref('')
const pwPassword = ref('')
const pwLoading = ref(false)

async function handlePasswordLogin() {
  if (!isValidPhone(pwPhone.value)) {
    message.warning('请输入正确的手机号')
    return
  }
  if (!pwPassword.value) {
    message.warning('请输入密码')
    return
  }
  pwLoading.value = true
  errorMsg.value = ''
  try {
    await finishLogin(await loginByPassword({ phone: pwPhone.value, password: pwPassword.value }))
  } catch (err: unknown) {
    errorMsg.value = apiDetail(err, '登录失败，请重试')
  } finally {
    pwLoading.value = false
  }
}

// ── 微信开放平台网站应用扫码登录（仅桌面）──
// 链路：默认勾选协议，切到微信 tab 即调 authorize-url 出码（iframe 内嵌微信扫码页）→
// 后端 302 到 /wechat-login-callback → 回调页 postMessage 给本页
// （ticket 在 HttpOnly cookie 里，消息不含 ticket）→ 本页调 exchange。
// 二维码按需现做（state 10 分钟过期），提供手动刷新；微信侧报错经回调页
// 以 error 消息回传，清码后可重新出码。
const TOS_VERSION = 'v1.0'
const PRIVACY_VERSION = 'v1.0'
const WECHAT_MESSAGE_TYPE = 'solo-wechat-login'

const WECHAT_ERROR_TEXT: Record<string, string> = {
  access_denied: '你取消了微信授权，可重新扫码登录',
  state_invalid: '登录二维码已过期，请刷新二维码重新扫码',
  state_expired: '登录二维码已过期，请刷新二维码重新扫码',
  config_missing: '微信登录暂未开放，请使用手机号登录',
  code_missing: '未收到微信授权结果，请重新扫码',
  ticket_expired: '登录已超时，请重新扫码',
  ticket_invalid: '登录已失效，请重新扫码',
  identity_conflict: '该微信已绑定其他账号，请使用手机号登录或联系客服',
  account_disabled: '该账号已被停用，请联系客服',
  network: '微信服务暂时不可用，请稍后重试',
  wechat_error: '微信授权失败，请重新扫码',
}
const WECHAT_ERROR_FALLBACK = '微信登录失败，请重试或使用手机号登录'

const isDesktop = ref(false)
const wechatEnabled = ref(false)
const wechatAgreed = ref(true)
const wechatLoading = ref(false)
const wechatQrUrl = ref('')
const wechatQrLoading = ref(false)
const wechatQrError = ref('')
const qrFrame = ref<HTMLIFrameElement | null>(null)
// 一次出码会话只允许换一次登录态，防重复 postMessage 触发重复 exchange
let wechatLoginDone = false
let desktopMq: MediaQueryList | null = null
let coarseMq: MediaQueryList | null = null

// 手机浏览器暂不支持微信登录（网站应用只能 PC 扫码），窄屏或触屏设备隐藏入口。
const wechatTabVisible = computed(() => isDesktop.value && wechatEnabled.value)

function syncWechatViewport() {
  const wide = desktopMq ? desktopMq.matches : false
  const coarse = coarseMq ? coarseMq.matches : false
  isDesktop.value = wide && !coarse
}

function setupWechatViewport() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return
  desktopMq = window.matchMedia('(min-width: 768px)')
  coarseMq = window.matchMedia('(pointer: coarse)')
  syncWechatViewport()
  desktopMq.addEventListener?.('change', syncWechatViewport)
  coarseMq.addEventListener?.('change', syncWechatViewport)
}

function teardownWechatViewport() {
  desktopMq?.removeEventListener?.('change', syncWechatViewport)
  coarseMq?.removeEventListener?.('change', syncWechatViewport)
  desktopMq = null
  coarseMq = null
}

function wechatErrorText(code: string): string {
  return WECHAT_ERROR_TEXT[code] || WECHAT_ERROR_FALLBACK
}

function isPhoneRequired(result: WechatWebExchangeResult): result is { phoneRequired: true } {
  return Boolean((result as { phoneRequired?: boolean })?.phoneRequired)
}

async function mintWechatQr() {
  // 协议是出码前置（后端 authorize-url 强制校验 privacy_accepted_at），勾选即出码
  if (!wechatAgreed.value || wechatQrLoading.value) return
  wechatQrLoading.value = true
  wechatQrError.value = ''
  try {
    const redirect = safeRedirectTarget(route.query.redirect)
    const { url } = await getWechatWebAuthorizeUrl({
      redirect: redirect || undefined,
      privacy_accepted_at: new Date().toISOString(),
      tos_version: TOS_VERSION,
      privacy_version: PRIVACY_VERSION,
    })
    if (!url) {
      wechatQrError.value = '微信登录地址获取失败，请稍后重试'
      return
    }
    wechatLoginDone = false
    wechatQrUrl.value = url
  } catch (err: unknown) {
    wechatQrError.value = apiDetail(err, '微信登录暂时不可用，请稍后重试')
  } finally {
    wechatQrLoading.value = false
  }
}

// 只接受同源、且来自本次出码 iframe 的消息；消息里没有 ticket，
// 拿到 ready 后凭 HttpOnly cookie 换登录态。
function handleWechatMessage(event: MessageEvent) {
  if (event.origin !== window.location.origin) return
  if (!qrFrame.value || event.source !== qrFrame.value.contentWindow) return
  const data = event.data as { type?: string; status?: string; code?: string } | null
  if (!data || data.type !== WECHAT_MESSAGE_TYPE) return
  if (data.status === 'ready') {
    if (wechatLoginDone) return
    wechatLoginDone = true
    void completeWechatLogin()
    return
  }
  if (data.status === 'error') {
    wechatQrError.value = wechatErrorText(data.code || 'wechat_error')
    // 清掉旧码露出重新出码入口；state 已随旧码作废，不能复用
    wechatQrUrl.value = ''
  }
}

// 切到微信 tab 即自动出码（默认已勾选协议，首次进 tab 才调接口避免浪费微信会话）
watch([activeTab, wechatAgreed, wechatTabVisible], ([tab, agreed, visible]) => {
  if (!agreed) {
    wechatQrUrl.value = ''
    wechatQrError.value = ''
    return
  }
  if (tab === 'wechat' && visible && !wechatQrUrl.value) void mintWechatQr()
})

async function completeWechatLogin() {
  wechatLoading.value = true
  errorMsg.value = ''
  try {
    const result = await exchangeWechatWebTicket()
    if (isPhoneRequired(result)) {
      // 首次登录：还没有 JWT，先绑定手机号，不写 token
      openBindModal()
      return
    }
    await finishLogin(result)
  } catch (err: unknown) {
    errorMsg.value = apiDetail(err, WECHAT_ERROR_FALLBACK)
  } finally {
    wechatLoading.value = false
  }
}

// ── 微信首次登录：绑定手机号 ──
const bindVisible = ref(false)
const bindPhone = ref('')
const bindCode = ref('')
const bindBizId = ref('')
const bindLoading = ref(false)
const bindCd = createCountdown()

function openBindModal() {
  bindVisible.value = true
}

watch(bindVisible, (visible) => {
  if (visible) return
  // 关闭即丢弃：ticket 留在 HttpOnly cookie 里自然过期，不会创建账号
  bindCd.stop()
  bindCode.value = ''
  bindBizId.value = ''
  bindLoading.value = false
})

async function handleBindSendCode() {
  if (!isValidPhone(bindPhone.value)) {
    message.warning('请输入正确的手机号')
    return
  }
  bindCd.sending.value = true
  try {
    const resp = await sendPhoneCode({ phone: bindPhone.value.trim() })
    bindBizId.value = resp?.biz_id || ''
    message.success('验证码已发送')
    bindCd.start()
  } catch (err: unknown) {
    message.error(apiDetail(err, '发送失败'))
    bindCd.sending.value = false
  }
}

async function handleBindSubmit() {
  if (!isValidPhone(bindPhone.value)) {
    message.warning('请输入正确的手机号')
    return
  }
  if (!bindCode.value) {
    message.warning('请输入验证码')
    return
  }
  bindLoading.value = true
  try {
    const resp = await bindWechatWebPhone({
      phone: bindPhone.value.trim(),
      code: bindCode.value,
      biz_id: bindBizId.value || undefined,
    })
    bindVisible.value = false
    await finishLogin(resp)
  } catch (err: unknown) {
    message.error(apiDetail(err, '绑定失败，请重试'))
  } finally {
    bindLoading.value = false
  }
}

onMounted(async () => {
  setupWechatViewport()
  window.addEventListener('message', handleWechatMessage)

  // 整页回退 / 后端回调失败后落回登录页的状态参数
  const upstreamError =
    (typeof route.query.wechat_error === 'string' && route.query.wechat_error) || ''
  const hasWechatState = Boolean(upstreamError || route.query.wechat_pending)
  if (route.query.wechat_pending) {
    activeTab.value = 'wechat'
    openBindModal()
  }
  if (upstreamError) activeTab.value = 'wechat'
  if (hasWechatState) {
    // 清掉一次性状态参数，刷新页面不再重复弹窗/报错；redirect 与 plan 意向保留
    const query = { ...route.query }
    delete query.wechat_error
    delete query.wechat_pending
    try {
      await router.replace({ path: '/login', query })
    } catch { /* 路由未就绪时忽略 */ }
  }
  // 必须放在切 tab 之后：activeTab 的 watcher 会清空 errorMsg
  if (upstreamError) errorMsg.value = wechatErrorText(upstreamError)

  try {
    const config = await getWechatWebConfig()
    wechatEnabled.value = Boolean(config?.enabled)
  } catch {
    // 配置接口失败时不显示入口，不影响手机号登录
    wechatEnabled.value = false
  }
})

// 入口可能因窗口变窄 / 配置关闭而消失，别把用户留在空白的 wechat tab
watch(wechatTabVisible, (visible) => {
  if (!visible && activeTab.value === 'wechat') activeTab.value = 'code'
})

// ── 忘记密码 ──
const forgotVisible = ref(false)
const fpPhone = ref('')
const fpCode = ref('')
const fpNewPassword = ref('')
const fpLoading = ref(false)
const fpCd = createCountdown()

function openForgot() {
  fpPhone.value = pwPhone.value
  fpCode.value = ''
  fpNewPassword.value = ''
  forgotVisible.value = true
}

async function handleForgotSendCode() {
  if (!isValidPhone(fpPhone.value)) {
    message.warning('请输入正确的手机号')
    return
  }
  fpCd.sending.value = true
  try {
    await sendPhoneCode({ phone: fpPhone.value })
    message.success('验证码已发送')
    fpCd.start()
  } catch (err: unknown) {
    message.error(apiDetail(err, '发送失败'))
    fpCd.sending.value = false
  }
}

async function handleForgotReset() {
  if (!isValidPhone(fpPhone.value)) {
    message.warning('请输入正确的手机号')
    return
  }
  if (!fpCode.value) {
    message.warning('请输入验证码')
    return
  }
  if (fpNewPassword.value.length < 8) {
    message.warning('密码长度至少 8 位')
    return
  }
  fpLoading.value = true
  try {
    await resetPassword({
      phone: fpPhone.value,
      code: fpCode.value,
      new_password: fpNewPassword.value,
    })
    message.success('密码已重置，请使用新密码登录')
    forgotVisible.value = false
    activeTab.value = 'password'
    pwPhone.value = fpPhone.value
    pwPassword.value = ''
  } catch (err: unknown) {
    message.error(apiDetail(err, '重置失败，请重试'))
  } finally {
    fpLoading.value = false
  }
}
</script>

<template>
  <div class="login-page">
    <div class="login-glow-mesh" aria-hidden="true" />
    <RouterLink class="back-to-landing" to="/landing">← 返回官网首页</RouterLink>

    <div class="login-shell">
      <!-- 宽屏左侧：极简品牌大留白 (Linear 风格) -->
      <aside class="brand-pane">
        <h1 class="brand-title">不做糊涂账，<br />合规用梭罗。</h1>
        <p class="brand-desc">
          告别“甩手掌柜”与应付报税的黑盒代账。<br />
          在日常确认中建立老板财务直觉，从第一天起养出经得起授信与融资尽调的体面账。
        </p>
      </aside>

      <div class="login-card">
      <div class="login-header">
        <div class="logo-area">
          <div class="logo-icon">
            <img src="/solo-logo.png" alt="梭罗记账" />
          </div>
          <div class="logo-copy">
            <span class="logo-text">梭罗记账</span>
            <span class="logo-sub">SoloBooks</span>
          </div>
        </div>
        <p class="subtitle subtitle-desktop">小微企业的驻场 AI 财务官</p>
        <p class="subtitle subtitle-compact">不做糊涂账，合规用梭罗。</p>
      </div>

      <NAlert v-if="errorMsg" type="error" closable class="mb-4" @close="errorMsg = ''">
        {{ errorMsg }}
      </NAlert>

      <NTabs v-model:value="activeTab" type="line" size="large" class="login-tabs">
        <NTabPane name="code" tab="验证码登录">
          <NForm>
            <NFormItem label="手机号">
              <NInput
                v-model:value="phone"
                placeholder="请输入手机号"
                maxlength="11"
                size="large"
              >
                <template #prefix>
                  <i class="ri-phone-line input-icon" />
                </template>
              </NInput>
            </NFormItem>

            <NFormItem label="验证码">
              <div class="code-row">
                <NInput
                  v-model:value="code"
                  placeholder="请输入验证码"
                  maxlength="6"
                  size="large"
                  style="flex: 1"
                  @keyup.enter="handleCodeLogin"
                >
                  <template #prefix>
                    <i class="ri-shield-keyhole-line input-icon" />
                  </template>
                </NInput>
                <NButton
                  :disabled="codeCd.sending.value"
                  @click="handleSendCode"
                  size="large"
                  class="code-btn"
                  secondary
                  type="primary"
                >
                  {{ codeCd.sending.value ? `${codeCd.countdown.value}s` : '获取验证码' }}
                </NButton>
              </div>
            </NFormItem>

            <NButton
              type="primary"
              block
              size="large"
              :loading="loading"
              @click="handleCodeLogin"
              class="login-btn"
            >
              {{ loading ? '正在登录 / 注册，首次将生成演示账套…' : '登录 / 注册' }}
            </NButton>
          </NForm>
        </NTabPane>

        <NTabPane name="password" tab="密码登录">
          <NForm>
            <NFormItem label="手机号">
              <NInput
                v-model:value="pwPhone"
                placeholder="请输入手机号"
                maxlength="11"
                size="large"
              >
                <template #prefix>
                  <i class="ri-phone-line input-icon" />
                </template>
              </NInput>
            </NFormItem>

            <NFormItem>
              <template #label>
                <div class="label-row">
                  <span>密码</span>
                  <a class="forgot-link" @click="openForgot">忘记密码？</a>
                </div>
              </template>
              <NInput
                v-model:value="pwPassword"
                type="password"
                show-password-on="click"
                placeholder="请输入密码"
                size="large"
                @keyup.enter="handlePasswordLogin"
              >
                <template #prefix>
                  <i class="ri-lock-password-line input-icon" />
                </template>
              </NInput>
            </NFormItem>

            <NButton
              type="primary"
              block
              size="large"
              :loading="pwLoading"
              @click="handlePasswordLogin"
              class="login-btn"
            >
              登 录
            </NButton>
          </NForm>
        </NTabPane>

        <NTabPane v-if="wechatTabVisible" name="wechat" tab="微信扫码登录">
          <div class="wechat-pane">
            <div v-if="wechatQrError" class="wechat-qr-fallback">
              <p class="wechat-qr-error">{{ wechatQrError }}</p>
              <NButton size="small" secondary @click="mintWechatQr">重新出码</NButton>
            </div>
            <div v-else-if="wechatQrUrl" class="wechat-qr-wrap">
              <iframe
                ref="qrFrame"
                :src="wechatQrUrl"
                class="wechat-qr-frame"
                title="微信扫码登录二维码"
                scrolling="no"
              />
              <p v-if="wechatLoading" class="wechat-tip">正在确认登录…</p>
              <p v-else class="wechat-tip">
                二维码失效？<a
                  href="#"
                  class="wechat-refresh"
                  @click.prevent="mintWechatQr"
                >刷新二维码</a>
              </p>
            </div>
            <div v-else class="wechat-qr-fallback">
              <NSpin v-if="wechatQrLoading" size="small" />
              <p v-else class="wechat-tip">勾选下方协议后显示二维码</p>
            </div>

            <NCheckbox v-model:checked="wechatAgreed" class="wechat-consent">
              我已阅读并同意
              <a href="/agreement" target="_blank" rel="noreferrer" @click.stop>《服务协议》</a>
              和
              <a href="/privacy" target="_blank" rel="noreferrer" @click.stop>《隐私政策》</a>
            </NCheckbox>
          </div>
        </NTabPane>
      </NTabs>

      <p v-if="activeTab !== 'wechat'" class="login-footer">
        登录即表示同意
        <a href="/agreement" target="_blank">《服务协议》</a>和<a href="/privacy" target="_blank">《隐私政策》</a>
      </p>
    </div>
    </div>

    <footer class="login-footer-bar">
      <span>© {{ new Date().getFullYear() }} 梭罗记账 SoloBooks</span>
      <a v-if="ICP_LICENSE" href="https://beian.miit.gov.cn" target="_blank" rel="noreferrer">{{ ICP_LICENSE }}</a>
      <a v-if="PSB_RECORD" :href="PSB_RECORD_URL" target="_blank" rel="noreferrer">{{ PSB_RECORD }}</a>
    </footer>

    <NModal
      v-model:show="forgotVisible"
      preset="card"
      title="重置密码"
      style="max-width: 400px; border-radius: var(--solo-radius-lg);"
    >
      <NForm style="margin-top: 8px;">
        <NFormItem label="手机号">
          <NInput v-model:value="fpPhone" placeholder="请输入手机号" maxlength="11">
            <template #prefix>
              <i class="ri-phone-line input-icon" />
            </template>
          </NInput>
        </NFormItem>
        <NFormItem label="验证码">
          <div class="code-row">
            <NInput v-model:value="fpCode" placeholder="请输入验证码" maxlength="6" style="flex: 1">
              <template #prefix>
                <i class="ri-shield-keyhole-line input-icon" />
              </template>
            </NInput>
            <NButton
              :disabled="fpCd.sending.value"
              @click="handleForgotSendCode"
              class="code-btn"
              secondary
              type="primary"
            >
              {{ fpCd.sending.value ? `${fpCd.countdown.value}s` : '获取验证码' }}
            </NButton>
          </div>
        </NFormItem>
        <NFormItem label="新密码">
          <NInput
            v-model:value="fpNewPassword"
            type="password"
            show-password-on="click"
            placeholder="至少 8 位"
          >
            <template #prefix>
              <i class="ri-lock-password-line input-icon" />
            </template>
          </NInput>
        </NFormItem>
      </NForm>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px;">
          <NButton :disabled="fpLoading" @click="forgotVisible = false">取消</NButton>
          <NButton type="primary" :loading="fpLoading" @click="handleForgotReset">
            重置密码
          </NButton>
        </div>
      </template>
    </NModal>

    <!-- 微信首次登录：绑定手机号后才发放登录态（关闭即放弃，不创建账号） -->
    <NModal
      v-model:show="bindVisible"
      preset="card"
      title="绑定手机号"
      style="max-width: 420px; border-radius: var(--solo-radius-lg);"
      :mask-closable="false"
    >
      <div class="bind-intro">
        <p>微信登录首次使用需绑定手机号，用于账号找回与合规实名。</p>
        <p>若该号码已有老账号，将登录到该账号，账套与套餐保留。</p>
      </div>
      <NForm style="margin-top: 8px;">
        <NFormItem label="手机号">
          <NInput v-model:value="bindPhone" placeholder="请输入手机号" maxlength="11">
            <template #prefix>
              <i class="ri-phone-line input-icon" />
            </template>
          </NInput>
        </NFormItem>
        <NFormItem label="验证码">
          <div class="code-row">
            <NInput
              v-model:value="bindCode"
              placeholder="请输入验证码"
              maxlength="6"
              style="flex: 1"
              @keyup.enter="handleBindSubmit"
            >
              <template #prefix>
                <i class="ri-shield-keyhole-line input-icon" />
              </template>
            </NInput>
            <NButton
              :disabled="bindCd.sending.value"
              @click="handleBindSendCode"
              class="code-btn"
              secondary
              type="primary"
            >
              {{ bindCd.sending.value ? `${bindCd.countdown.value}s` : '获取验证码' }}
            </NButton>
          </div>
        </NFormItem>
      </NForm>
      <template #footer>
        <div style="display: flex; justify-content: flex-end; gap: 8px;">
          <NButton :disabled="bindLoading" @click="bindVisible = false">取消</NButton>
          <NButton type="primary" :loading="bindLoading" @click="handleBindSubmit">
            绑定并登录
          </NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background:
    radial-gradient(circle at 50% -15%, rgba(23, 71, 176, 0.09) 0%, rgba(30, 127, 251, 0.03) 45%, transparent 75%),
    linear-gradient(180deg, #f1f4f8 0%, #ffffff 100%);
  position: relative;
  overflow: hidden;
  padding: 20px;
  flex-direction: column;
}

.login-glow-mesh {
  position: absolute;
  top: -160px;
  left: 50%;
  transform: translateX(-50%);
  width: 1100px;
  height: 600px;
  background: radial-gradient(circle, rgba(23, 71, 176, 0.08) 0%, rgba(30, 127, 251, 0.02) 50%, transparent 70%);
  pointer-events: none;
  z-index: 1;
}

.login-shell {
  flex: 1;
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 72px;
  padding-top: 32px;
  position: relative;
  z-index: 10;
}

.brand-pane {
  max-width: 440px;
}

.brand-title {
  font-size: 38px;
  font-weight: 850;
  line-height: 1.28;
  color: var(--solo-text-primary, #0f172a);
  margin: 0 0 18px;
  letter-spacing: -0.8px;
}

.brand-desc {
  font-size: 15px;
  line-height: 1.8;
  color: var(--solo-text-secondary, #586474);
  margin: 0;
  max-width: 380px;
}

/* 返回官网入口 */
.back-to-landing {
  position: absolute;
  top: 20px;
  left: 24px;
  z-index: 20;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--solo-text-muted);
  text-decoration: none;
  font-weight: 500;
  transition: color 0.15s ease;
}

.back-to-landing:hover {
  color: var(--solo-primary);
}

.login-footer-bar {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 8px 18px;
  margin-top: 24px;
  padding: 0 16px;
  font-size: 12px;
  color: var(--solo-text-muted);
  position: relative;
  z-index: 10;
}

.login-footer-bar a {
  color: var(--solo-text-muted);
  text-decoration: none;
}

.login-footer-bar a:hover {
  color: var(--solo-primary);
}

@media (max-width: 960px) {
  .brand-pane {
    display: none;
  }
}

.login-card {
  width: 100%;
  max-width: 400px;
  background: #ffffff;
  border: 1px solid rgba(226, 232, 240, 0.9);
  border-radius: 12px;
  padding: 44px 36px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 1),
    0 12px 32px -4px rgba(15, 23, 42, 0.06),
    0 4px 12px -2px rgba(15, 23, 42, 0.03);
  position: relative;
  z-index: 10;
  box-sizing: border-box;
}

.login-header {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-bottom: 24px;
}

.subtitle-compact {
  display: none;
}

.logo-area {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo-icon {
  width: 38px;
  height: 38px;
  display: flex;
  align-items: center;
  justify-content: center;
}

.logo-icon img {
  width: 100%;
  height: 100%;
  display: block;
}

.logo-copy {
  display: flex;
  flex-direction: column;
  line-height: 1.1;
  text-align: left;
}

.logo-text {
  font-size: 20px;
  font-weight: 750;
  color: var(--solo-text-primary);
  letter-spacing: 2px;
}

.logo-sub {
  margin-top: 3px;
  color: var(--solo-text-muted);
  font-size: 10px;
  letter-spacing: 0.8px;
  text-transform: uppercase;
  font-weight: 500;
}

.subtitle {
  margin: 12px 0 0;
  color: var(--solo-text-secondary);
  font-size: 13.5px;
  font-weight: 500;
}

.login-tabs {
  --n-tab-font-weight-active: 600;
  /* 三个 tab（验证码 / 密码 / 微信扫码）在 328px 内容宽里要放得下，压缩默认 20px 内边距 */
  --n-tab-padding: 0 10px;
}

.input-icon {
  font-size: 18px;
  color: var(--solo-text-muted);
}

.code-row {
  display: flex;
  gap: 12px;
  align-items: center;
  width: 100%;
}

.code-btn {
  white-space: nowrap;
  min-width: 110px;
  height: 40px;
  font-weight: 600;
  border-radius: var(--solo-radius);
}

.login-btn {
  margin-top: 12px;
  height: 44px;
  font-size: 15px;
  font-weight: 600;
  border-radius: var(--solo-radius);
  transition: all 0.2s ease;
}
.login-btn:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(22, 93, 255, 0.2);
}

.label-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.forgot-link {
  font-size: 13px;
  color: var(--solo-primary);
  cursor: pointer;
  font-weight: 500;
  transition: color 0.15s ease;
}
.forgot-link:hover {
  color: var(--solo-primary-light);
}

.login-footer {
  text-align: center;
  margin-top: 24px;
  font-size: 12px;
  color: var(--solo-text-muted);
  line-height: 1.6;
}
.login-footer a {
  color: var(--solo-primary);
  text-decoration: none;
  font-weight: 500;
  transition: color 0.15s ease;
}
.login-footer a:hover {
  color: var(--solo-primary-light);
}

/* ── 微信扫码登录 tab ── */
.wechat-pane {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 12px;
  padding: 10px 0 2px;
}

/* 内嵌微信扫码页（qrconnect）：已通过 href 隐藏「微信登录」标题并把二维码收敛到 200px */
.wechat-qr-wrap {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  width: 100%;
}

.wechat-qr-frame {
  width: 248px;
  max-width: 100%;
  height: 268px;
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-md, 8px);
  background: #fff;
}

.wechat-qr-fallback {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 10px;
  width: 248px;
  max-width: 100%;
  min-height: 240px;
  padding: 24px 16px;
  box-sizing: border-box;
  border: 1px dashed var(--solo-border-light);
  border-radius: var(--solo-radius-md, 8px);
  background: var(--solo-bg-soft);
}

.wechat-qr-error {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--solo-expense);
  text-align: center;
}

.wechat-tip {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--solo-text-muted);
  text-align: center;
}

.wechat-refresh {
  color: var(--solo-primary);
  text-decoration: none;
}

.wechat-refresh:hover {
  text-decoration: underline;
}

.wechat-consent {
  font-size: 12px;
  line-height: 1.6;
  color: var(--solo-text-muted);
  white-space: normal;
  align-items: center;
}

.wechat-consent a {
  color: var(--solo-primary);
  text-decoration: none;
}

.wechat-consent a:hover {
  color: var(--solo-primary-light);
}

.bind-intro {
  margin-bottom: 8px;
}

.bind-intro p {
  margin: 0 0 6px;
  font-size: 13px;
  line-height: 1.7;
  color: var(--solo-text-secondary);
}

.bind-intro p:last-child {
  margin-bottom: 0;
}

.mb-4 {
  margin-bottom: 16px;
}

/* Naive UI override consistency adjustments */
:deep(.n-form-item-label) {
  font-weight: 600 !important;
  color: var(--solo-text-secondary) !important;
  font-size: 13px !important;
  padding-bottom: 6px !important;
}

/* label 是 flex 容器，文字 span 默认按内容收缩（91px），
   .label-row 的 space-between 只能在 91px 内生效，导致「忘记密码？」紧跟「密码」。
   撑满 label 后右侧链接才对齐到输入框右边缘 */
:deep(.n-form-item-label__text) {
  flex: 1;
  min-width: 0;
}

/* ≤960px：收起左侧品牌面板，登录卡内保持简洁单行品牌副标题 */
@media (max-width: 960px) {
  .login-card {
    padding: 32px 26px;
  }

  .subtitle-desktop {
    display: none;
  }

  .subtitle-compact {
    display: block;
  }

  .back-to-landing {
    top: 14px;
    left: 16px;
  }
}
</style>
