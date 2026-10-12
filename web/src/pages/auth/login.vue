<script setup lang="ts">
import { ref, watch, onUnmounted } from 'vue'
import {
  NInput,
  NButton,
  NForm,
  NFormItem,
  NAlert,
  NTabs,
  NTabPane,
  NModal,
  useMessage,
} from 'naive-ui'
import {
  sendPhoneCode,
  loginByPhoneCode,
  loginByPassword,
  resetPassword,
} from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

// ICP 备案通过后在此填入备案号（如"苏ICP备XXXXXXXX号"），页脚会自动展示并链接工信部网站
const ICP_LICENSE = ''

const message = useMessage()
const authStore = useAuthStore()

const activeTab = ref<'code' | 'password'>('code')
const errorMsg = ref('')

watch(activeTab, () => {
  errorMsg.value = ''
})

// ── 验证码登录 ──
const phone = ref('')
const code = ref('')
const loading = ref(false)

const codeCd = createCountdown()

function createCountdown() {
  const sending = ref(false)
  const countdown = ref(0)
  let timer: ReturnType<typeof setInterval> | null = null
  function start() {
    sending.value = true
    countdown.value = 60
    timer = setInterval(() => {
      countdown.value--
      if (countdown.value <= 0) {
        if (timer) clearInterval(timer)
        sending.value = false
      }
    }, 1000)
  }
  function stop() {
    if (timer) clearInterval(timer)
    timer = null
    sending.value = false
    countdown.value = 0
  }
  return { sending, countdown, start, stop }
}

onUnmounted(() => {
  codeCd.stop()
  fpCd.stop()
})

const isValidPhone = (value: string) => /^1\d{10}$/.test(value)

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
    const resp = err as { response?: { data?: { detail?: string } } }
    message.error(resp?.response?.data?.detail || '发送失败')
    codeCd.sending.value = false
  }
}

async function finishLogin(resp: Awaited<ReturnType<typeof loginByPhoneCode>>) {
  authStore.setSession(resp)
  await authStore.fetchProfile()
  message.success('登录成功')
  // 昵称为空或未设密码时，让登录后的首页弹一次"完善账号信息"引导。
  const needPassword = resp.has_password === false
  const needNickname = !String(authStore.user?.nickname || '').trim()
  if (needPassword || needNickname) {
    sessionStorage.setItem('solo:setup-password-hint', '1')
  } else {
    sessionStorage.removeItem('solo:setup-password-hint')
  }
  // Reload the app from its authenticated entry point. This avoids a stale
  // login route or a rejected client-side navigation swallowing the jump.
  window.location.replace('/')
}

function apiDetail(err: unknown, fallback: string): string {
  const resp = err as { response?: { data?: { detail?: string } } }
  return resp?.response?.data?.detail || fallback
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

async function handleQuickLogin() {
  activeTab.value = 'code'
  phone.value = '13800000000'
  code.value = '123456'
  await handleCodeLogin()
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
        <h1 class="brand-title">把账记简单，<br />把经营看清楚。</h1>
        <p class="brand-desc">
          面向一人公司与微型团队的财务底座。<br />
          流水自动对账，凭证合规留痕，告别繁复财务焦虑。
        </p>
      </aside>

      <div class="login-card">
      <!-- 移动端品牌区：宽屏由左侧品牌面板承担，小屏在此透出品牌与定位 -->
      <div class="mobile-brand">
        <div class="logo-area">
          <div class="logo-icon">
            <img src="/solo-logo.png" alt="梭罗记账" />
          </div>
          <div class="logo-copy">
            <span class="logo-text">梭罗记账</span>
            <span class="logo-sub">SoloBooks</span>
          </div>
        </div>
        <p class="mobile-brand-positioning">一人公司及小微企业的 AI 会计 Agent</p>
        <p class="mobile-brand-slogan">把账记简单，把经营看清楚。</p>
        <div class="mobile-brand-pills">
          <span>零会计门槛</span>
          <span>流水自动对账</span>
          <span>合规月结向导</span>
        </div>
      </div>

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
        <p class="subtitle">一人公司及小微企业的 AI 会计 Agent</p>
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
              登录 / 注册
            </NButton>

            <NButton
              secondary
              block
              size="large"
              :disabled="loading"
              @click="handleQuickLogin"
              class="quick-login-btn"
            >
              <template #icon>
                <i class="ri-flashlight-line" />
              </template>
              免注册 · 测试账号一键试用
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

            <NFormItem label="密码">
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

            <div class="forgot-row">
              <a class="forgot-link" @click="openForgot">忘记密码？</a>
            </div>
          </NForm>
        </NTabPane>
      </NTabs>

      <p class="login-footer">
        登录即表示同意
        <a href="/agreement" target="_blank">《服务协议》</a>和<a href="/privacy" target="_blank">《隐私政策》</a>
      </p>
    </div>
    </div>

    <footer class="login-footer-bar">
      <span>© {{ new Date().getFullYear() }} 梭罗记账 SoloBooks · 把账记简单，把经营看清楚。</span>
      <a href="/agreement" target="_blank">服务协议</a>
      <a href="/privacy" target="_blank">隐私政策</a>
      <a v-if="ICP_LICENSE" href="https://beian.miit.gov.cn" target="_blank" rel="noreferrer">{{ ICP_LICENSE }}</a>
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
    linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
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
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 72px;
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
  color: var(--solo-text-secondary, #64748b);
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
  position: absolute;
  bottom: 14px;
  left: 0;
  right: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 8px 18px;
  padding: 0 16px;
  font-size: 12px;
  color: var(--solo-text-muted);
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
  border-radius: 20px;
  padding: 44px 36px;
  box-shadow:
    inset 0 1px 0 rgba(255, 255, 255, 1),
    0 12px 32px -4px rgba(15, 23, 42, 0.06),
    0 4px 12px -2px rgba(15, 23, 42, 0.03);
  position: relative;
  z-index: 10;
  box-sizing: border-box;
}

/* 移动端品牌区（≤960px 展示，宽屏由左侧品牌面板承担） */
.mobile-brand {
  display: none;
  text-align: center;
  margin-bottom: 24px;
  padding-bottom: 22px;
  border-bottom: 1px solid var(--solo-border-light);
}

.mobile-brand .logo-area {
  justify-content: center;
  margin-bottom: 12px;
}

.mobile-brand-positioning {
  margin: 0 0 6px;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--solo-primary);
}

.mobile-brand-slogan {
  margin: 0 0 14px;
  font-size: 13px;
  color: var(--solo-text-secondary);
}

.mobile-brand-pills {
  display: flex;
  justify-content: center;
  gap: 8px;
  flex-wrap: wrap;
}

.mobile-brand-pills span {
  font-size: 11.5px;
  font-weight: 500;
  color: var(--solo-text-secondary);
  background: var(--solo-bg-soft);
  border: 1px solid var(--solo-border-light);
  border-radius: 999px;
  padding: 3px 10px;
}

.login-header {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-bottom: 24px;
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

.quick-login-btn {
  margin-top: 12px;
  height: 44px;
  font-size: 15px;
  font-weight: 600;
  border-radius: var(--solo-radius);
  transition: all 0.2s ease;
}
.quick-login-btn:hover {
  transform: translateY(-1px);
}

.forgot-row {
  margin-top: 14px;
  text-align: right;
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

/* ≤960px：收起左侧品牌面板，登录卡内展示品牌区 */
@media (max-width: 960px) {
  .login-card {
    padding: 32px 26px;
  }

  .mobile-brand {
    display: block;
  }

  .login-header {
    display: none;
  }

  .back-to-landing {
    top: 14px;
    left: 16px;
  }
}
</style>
