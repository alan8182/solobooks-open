<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<!-- 微信 Native 扫码支付弹窗：渲染 code_url 二维码 + 轮询订单状态 + 有效期倒计时。

结算真相在后端：轮询 GET /api/billing/orders/{orderNo} 时，后端对 created 状态的
微信订单会主动向微信查单兜底（防回调丢失），「我已支付」按钮即立即触发一次该查询。
弹窗只在订单 activated 后展示成功态，paid/closed 等中间态由后端口径决定。 -->
<script setup lang="ts">
import { onBeforeUnmount, ref, watch } from 'vue'
import { NButton, NModal, NSpin, useMessage } from 'naive-ui'
import QRCode from 'qrcode'
import { fetchBillingOrder } from '@/api/billing'

const props = defineProps<{
  show: boolean
  orderNo: string
  codeUrl: string
  amount: string
  planName: string
  expiresAt: string
  /** 是否允许在弹窗内直接改用支付宝下单跳转 */
  canSwitchAlipay?: boolean
  switchingAlipay?: boolean
  /** 弹窗底部展示的购买/充值服务条款 */
  termsTitle?: string
  termsLines?: string[]
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'success'): void
  (e: 'switch-alipay'): void
}>()

const message = useMessage()

const POLL_INTERVAL_MS = 2500

const qrDataUrl = ref('')
const qrFailed = ref(false)
const phase = ref<'scanning' | 'success' | 'failed'>('scanning')
const failReason = ref('')
const countdownText = ref('--:--')
const checking = ref(false)

let pollTimer: number | null = null
let countdownTimer: number | null = null
let paidNotified = false

async function renderQr() {
  qrDataUrl.value = ''
  qrFailed.value = false
  if (!props.codeUrl) {
    qrFailed.value = true
    return
  }
  try {
    qrDataUrl.value = await QRCode.toDataURL(props.codeUrl, {
      width: 224,
      margin: 1,
      errorCorrectionLevel: 'M',
    })
  } catch {
    qrFailed.value = true
  }
}

function stopTimers() {
  if (pollTimer !== null) {
    window.clearTimeout(pollTimer)
    pollTimer = null
  }
  if (countdownTimer !== null) {
    window.clearInterval(countdownTimer)
    countdownTimer = null
  }
}

function scheduleNextPoll(delay = POLL_INTERVAL_MS) {
  if (pollTimer !== null) window.clearTimeout(pollTimer)
  pollTimer = window.setTimeout(pollTick, delay)
}

async function pollTick() {
  pollTimer = null
  if (phase.value !== 'scanning' || !props.show) return
  try {
    const order = await fetchBillingOrder(props.orderNo)
    if (phase.value !== 'scanning') return
    if (order.status === 'activated' || order.status === 'paid') {
      markPaid()
      return
    }
    if (order.status === 'closed' || order.status === 'failed') {
      markFailed('订单未完成支付；如已付款请联系客服核实')
      return
    }
  } catch {
    // 网络抖动继续轮询
  }
  scheduleNextPoll()
}

function markPaid() {
  stopTimers()
  phase.value = 'success'
  if (!paidNotified) {
    paidNotified = true
    message.success('支付成功，套餐已生效')
    emit('success')
  }
}

function markFailed(reason: string) {
  stopTimers()
  phase.value = 'failed'
  failReason.value = reason
}

function updateCountdown() {
  const expires = Date.parse(props.expiresAt)
  if (!Number.isFinite(expires)) {
    countdownText.value = ''
    return
  }
  const remainMs = expires - Date.now()
  if (remainMs <= 0) {
    markFailed('二维码已过期，请关闭后重新下单')
    return
  }
  const totalSeconds = Math.floor(remainMs / 1000)
  const mm = String(Math.floor(totalSeconds / 60)).padStart(2, '0')
  const ss = String(totalSeconds % 60).padStart(2, '0')
  countdownText.value = `${mm}:${ss}`
}

function start() {
  stopTimers()
  phase.value = 'scanning'
  failReason.value = ''
  paidNotified = false
  void renderQr()
  updateCountdown()
  countdownTimer = window.setInterval(updateCountdown, 1000)
  scheduleNextPoll(POLL_INTERVAL_MS)
}

async function manualCheck() {
  if (phase.value !== 'scanning') return
  if (pollTimer !== null) {
    window.clearTimeout(pollTimer)
    pollTimer = null
  }
  checking.value = true
  try {
    await pollTick()
  } finally {
    checking.value = false
  }
}

function handleClose() {
  emit('update:show', false)
}

function handleSwitchAlipay() {
  if (props.switchingAlipay || phase.value !== 'scanning') return
  stopTimers()
  emit('switch-alipay')
}

watch(
  () => props.show,
  (visible) => {
    if (visible) start()
    else stopTimers()
  },
  { immediate: true },
)

onBeforeUnmount(stopTimers)
</script>

<template>
  <NModal
    :show="show"
    :mask-closable="phase !== 'scanning'"
    @update:show="(v: boolean) => emit('update:show', v)"
    preset="card"
    title="微信扫码支付"
    class="wechat-pay-modal"
    style="width: 440px; max-width: calc(100vw - 32px)"
  >
    <div class="wxpay-body">
      <p class="wxpay-amount">
        <span class="wxpay-plan">{{ planName }}</span>
        <strong>¥{{ amount }}</strong>
      </p>

      <div v-if="phase === 'scanning'" class="wxpay-qr-area">
        <NSpin :show="!qrDataUrl && !qrFailed" size="small">
          <img v-if="qrDataUrl" :src="qrDataUrl" class="wxpay-qr" alt="微信支付二维码" />
          <div v-else-if="qrFailed" class="wxpay-qr-fallback">
            <i class="ri-error-warning-line" />
            <p>二维码生成失败，请关闭后重试</p>
          </div>
          <div v-else class="wxpay-qr-placeholder" />
        </NSpin>
        <p class="wxpay-tip">
          <i class="ri-wechat-pay-line" />
          请使用微信扫码支付
        </p>
        <p class="wxpay-countdown">二维码有效期剩余 {{ countdownText }}</p>
        <NButton size="small" secondary block :loading="checking" @click="manualCheck">
          我已支付，查询结果
        </NButton>
        <NButton
          v-if="canSwitchAlipay"
          size="small"
          quaternary
          block
          class="wxpay-switch-alipay"
          :loading="switchingAlipay"
          @click="handleSwitchAlipay"
        >
          <template #icon><i class="ri-alipay-line" /></template>
          改用支付宝支付 →
        </NButton>

        <div v-if="termsLines?.length" class="wxpay-terms">
          <p v-if="termsTitle" class="wxpay-terms-title">{{ termsTitle }}</p>
          <ul class="wxpay-terms-list">
            <li v-for="line in termsLines" :key="line">{{ line }}</li>
          </ul>
          <p class="wxpay-terms-links">
            扫码或切换支付即视为同意以上条款及
            <a href="/agreement" target="_blank" rel="noreferrer">《服务协议（含付费订阅与退款规则）》</a>
            与
            <a href="/privacy" target="_blank" rel="noreferrer">《隐私政策》</a>
          </p>
        </div>
      </div>

      <div v-else-if="phase === 'success'" class="wxpay-result success">
        <i class="ri-checkbox-circle-fill" />
        <p>支付成功，套餐已生效</p>
        <NButton type="primary" block @click="handleClose">完成</NButton>
      </div>

      <div v-else class="wxpay-result failed">
        <i class="ri-error-warning-line" />
        <p>{{ failReason }}</p>
        <NButton block @click="handleClose">关闭</NButton>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
.wxpay-body {
  display: flex;
  flex-direction: column;
  align-items: center;
  padding: 4px 0 8px;
}

.wxpay-amount {
  margin: 0 0 14px;
  display: flex;
  align-items: baseline;
  gap: 10px;
}

.wxpay-plan {
  font-size: 13.5px;
  color: var(--solo-text-secondary);
}

.wxpay-amount strong {
  font-size: 22px;
  font-weight: 700;
  color: var(--solo-text-primary);
  font-variant-numeric: tabular-nums;
}

.wxpay-qr-area {
  display: flex;
  flex-direction: column;
  align-items: center;
  width: 100%;
}

.wxpay-qr {
  width: 224px;
  height: 224px;
  display: block;
  border-radius: 8px;
  border: 1px solid var(--solo-border);
}

.wxpay-qr-placeholder {
  width: 224px;
  height: 224px;
  border-radius: 8px;
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-soft);
}

.wxpay-qr-fallback {
  width: 224px;
  height: 224px;
  border-radius: 8px;
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-soft);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  color: var(--solo-text-muted);
  font-size: 12.5px;
}

.wxpay-tip {
  margin: 12px 0 2px;
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13.5px;
  color: var(--solo-text-primary);
}

.wxpay-tip i {
  color: #07c160;
  font-size: 16px;
}

.wxpay-countdown {
  margin: 0 0 12px;
  font-size: 12px;
  color: var(--solo-text-muted);
  font-variant-numeric: tabular-nums;
}

.wxpay-switch-alipay {
  margin-top: 8px;
  color: var(--solo-primary);
}

.wxpay-terms {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--solo-border);
  width: 100%;
  text-align: left;
}

.wxpay-terms-title {
  margin: 0 0 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--solo-text-secondary);
}

.wxpay-terms-list {
  margin: 0;
  padding-left: 16px;
  list-style: disc;
  list-style-position: outside;
  display: grid;
  gap: 4px;
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--solo-text-muted);
}

.wxpay-terms-links {
  margin: 8px 0 0;
  font-size: 11.5px;
  line-height: 1.55;
  color: var(--solo-text-muted);
}

.wxpay-terms-links a {
  color: var(--solo-primary);
  text-decoration: none;
}

.wxpay-terms-links a:hover {
  text-decoration: underline;
}

.wxpay-result {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  padding: 18px 0 8px;
  width: 100%;
}

.wxpay-result i {
  font-size: 44px;
}

.wxpay-result.success i {
  color: #07c160;
}

.wxpay-result.failed i {
  color: var(--solo-warning, #d97706);
}

.wxpay-result p {
  margin: 0;
  font-size: 14px;
  color: var(--solo-text-primary);
}
</style>
