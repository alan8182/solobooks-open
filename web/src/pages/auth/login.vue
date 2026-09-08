<script setup lang="ts">
import { ref } from 'vue'
import { NInput, NButton, NForm, NFormItem, NAlert, useMessage } from 'naive-ui'
import { sendPhoneCode, loginByPhoneCode } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'

const message = useMessage()
const authStore = useAuthStore()

const phone = ref('')
const code = ref('')
const loading = ref(false)
const sendingCode = ref(false)
const countdown = ref(0)
const errorMsg = ref('')
let timer: ReturnType<typeof setInterval> | null = null

function startCountdown() {
  countdown.value = 60
  timer = setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) {
      if (timer) clearInterval(timer)
      sendingCode.value = false
    }
  }, 1000)
}

async function handleSendCode() {
  if (!/^1\d{10}$/.test(phone.value)) {
    message.warning('请输入正确的手机号')
    return
  }
  sendingCode.value = true
  try {
    await sendPhoneCode({ phone: phone.value })
    message.success('验证码已发送')
    startCountdown()
  } catch (err: unknown) {
    const resp = err as { response?: { data?: { detail?: string } } }
    message.error(resp?.response?.data?.detail || '发送失败')
    sendingCode.value = false
  }
}

async function handleLogin() {
  if (!phone.value || !code.value) {
    message.warning('请填写手机号和验证码')
    return
  }
  loading.value = true
  errorMsg.value = ''
  try {
    const resp = await loginByPhoneCode({ phone: phone.value, code: code.value })
    authStore.setSession(resp)
    await authStore.fetchProfile()
    message.success('登录成功')
    // Reload the app from its authenticated entry point. This avoids a stale
    // login route or a rejected client-side navigation swallowing the jump.
    window.location.replace('/')
  } catch (err: unknown) {
    const resp = err as { response?: { data?: { detail?: string } } }
    errorMsg.value = resp?.response?.data?.detail || '登录失败，请重试'
  } finally {
    loading.value = false
  }
}

async function handleQuickLogin() {
  phone.value = '13800000000'
  code.value = '123456'
  await handleLogin()
}
</script>

<template>
  <div class="login-page">
    <!-- Ambient background glows -->
    <div class="bg-glow bg-glow-1" />
    <div class="bg-glow bg-glow-2" />

    <div class="login-card">
      <div class="login-header">
        <div class="logo-area">
          <div class="logo-icon">
            <i class="ri-line-chart-line" />
          </div>
          <div class="logo-copy">
            <span class="logo-text">司理</span>
            <span class="logo-sub">SoloBooks</span>
          </div>
        </div>
        <p class="subtitle">小微企业智能记账平台</p>
      </div>

      <NAlert v-if="errorMsg" type="error" closable class="mb-4">
        {{ errorMsg }}
      </NAlert>

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
            >
              <template #prefix>
                <i class="ri-shield-keyhole-line input-icon" />
              </template>
            </NInput>
            <NButton
              :disabled="sendingCode"
              @click="handleSendCode"
              size="large"
              class="code-btn"
              secondary
              type="primary"
            >
              {{ sendingCode ? `${countdown}s` : '获取验证码' }}
            </NButton>
          </div>
        </NFormItem>

        <NButton
          type="primary"
          block
          size="large"
          :loading="loading"
          @click="handleLogin"
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
          测试账号一键登录
        </NButton>
      </NForm>

      <p class="login-footer">
        登录即表示同意 <a href="#">《服务协议》</a>和<a href="#">《隐私政策》</a>
      </p>
    </div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background-color: var(--solo-bg-page);
  position: relative;
  overflow: hidden;
  padding: 20px;
}

/* Background Ambient Glows */
.bg-glow {
  position: absolute;
  width: 600px;
  height: 600px;
  border-radius: 50%;
  pointer-events: none;
  filter: blur(100px);
  z-index: 1;
}
.bg-glow-1 {
  top: -150px;
  left: -150px;
  background: radial-gradient(circle, rgba(22, 93, 255, 0.14) 0%, rgba(22, 93, 255, 0) 70%);
}
.bg-glow-2 {
  bottom: -150px;
  right: -150px;
  background: radial-gradient(circle, rgba(99, 102, 241, 0.12) 0%, rgba(99, 102, 241, 0) 70%);
}

.login-card {
  width: 100%;
  max-width: 400px;
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(20px);
  -webkit-backdrop-filter: blur(20px);
  border: 1px solid var(--solo-border-glass);
  border-radius: 20px;
  padding: 44px 36px;
  box-shadow: 
    0 4px 24px rgba(24, 34, 48, 0.02),
    0 20px 48px rgba(24, 34, 48, 0.06);
  position: relative;
  z-index: 10;
  box-sizing: border-box;
}

.login-header {
  display: flex;
  flex-direction: column;
  align-items: center;
  margin-bottom: 36px;
}

.logo-area {
  display: flex;
  align-items: center;
  gap: 12px;
}

.logo-icon {
  width: 38px;
  height: 38px;
  border-radius: 12px;
  background: var(--solo-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 20px;
  box-shadow: 0 4px 12px rgba(22, 93, 255, 0.25);
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

.login-footer {
  text-align: center;
  margin-top: 28px;
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
</style>
