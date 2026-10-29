<!--
  Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
  Licensed under the SoloBooks License v2.0.
-->
<script setup lang="ts">
/**
 * 反结账弹窗（短信二次验证）：解锁本期账册并自动红冲期末损益结转凭证。
 * 从 closing-wizard/index.vue 拆出：状态/请求/文案全部自包含，
 * 成功后 emit('reopened') 由父页做数据刷新（entries + close-checks）。
 */
import { ref } from 'vue'
import { NButton, NInput, NModal, useMessage } from 'naive-ui'
import { reopenPeriod } from '@/api/accounting'
import { requestStepUp, sendPhoneCode } from '@/api/auth'

const props = defineProps<{ month: string }>()
const emit = defineEmits<{ reopened: [] }>()
const show = defineModel<boolean>('show', { default: false })

const message = useMessage()
const reason = ref('')
const phone = ref('')
const code = ref('')
const bizId = ref('')
const sending = ref(false)
const submitting = ref(false)

async function sendCode() {
  if (!phone.value) {
    message.warning('请输入账号绑定的手机号')
    return
  }
  sending.value = true
  try {
    const result = await sendPhoneCode({ phone: phone.value })
    bizId.value = (result as any).biz_id || ''
    message.success('验证码已发送')
  } catch {
    message.error('验证码发送失败')
  } finally {
    sending.value = false
  }
}

async function submit() {
  if (!reason.value.trim()) {
    message.warning('请填写反结账原因（将留痕）')
    return
  }
  if (!phone.value || !code.value) {
    message.warning('请输入手机号和短信验证码完成安全验证')
    return
  }
  submitting.value = true
  try {
    const stepUp = await requestStepUp({
      action: 'reopen_period',
      phone: phone.value,
      code: code.value,
      bizId: bizId.value || undefined,
    })
    await reopenPeriod(props.month, reason.value.trim(), stepUp.stepUpToken)
    show.value = false
    message.success(`${props.month} 已反结账：本期期末损益结转凭证已自动红冲，账册解锁，可补记后重新结账。`)
    emit('reopened')
  } catch (err: any) {
    const detail = err?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : detail?.message || '反结账失败')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <NModal v-model:show="show" :mask-closable="true" style="width: 480px; max-width: 96vw;" :auto-focus="false">
    <div class="manual-dialog reopen-dialog">
      <div class="manual-dialog-header">
        <div class="manual-dialog-icon reopen-icon"><i class="ri-lock-unlock-line"></i></div>
        <div>
          <div class="manual-dialog-title">反结账 {{ month }}</div>
          <div class="manual-dialog-subtitle">解锁本期账册，补记或更正后需重新结账</div>
        </div>
      </div>

      <div class="reopen-warning">
        <i class="ri-error-warning-line"></i>
        <span>反结账将自动红冲本期的期末损益结转凭证并解锁账册；如后续月份已关账，需先反结账最新月份。反结账原因将留痕。</span>
      </div>

      <div class="reopen-field">
        <label>反结账原因（必填）</label>
        <NInput
          v-model:value="reason"
          type="textarea"
          :autosize="{ minRows: 2, maxRows: 4 }"
          placeholder="例如：补记遗漏的银行手续费"
        />
      </div>

      <div class="reopen-field">
        <label>安全验证（仅账套所有者可反结账）</label>
        <div class="reopen-verify">
          <NInput v-model:value="phone" placeholder="绑定手机号" />
          <NInput v-model:value="code" placeholder="短信验证码" />
          <NButton size="small" :loading="sending" @click="sendCode">发送验证码</NButton>
        </div>
      </div>

      <div class="reopen-actions">
        <NButton size="small" @click="show = false">取消</NButton>
        <NButton size="small" type="warning" :loading="submitting" @click="submit">
          确认反结账
        </NButton>
      </div>
    </div>
  </NModal>
</template>

<style scoped>
.manual-dialog {
  background: var(--solo-bg-card);
  border-radius: 12px;
  padding: 24px;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.18);
}
.manual-dialog-header {
  display: flex;
  align-items: center;
  gap: 14px;
  margin-bottom: 18px;
}
.manual-dialog-icon {
  width: 44px;
  height: 44px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  flex-shrink: 0;
}
.manual-dialog-title {
  font-size: 16px;
  font-weight: 700;
  color: var(--solo-text-primary);
}
.manual-dialog-subtitle {
  font-size: 12px;
  color: var(--solo-text-muted);
  margin-top: 2px;
}
.reopen-icon {
  background: rgba(240, 153, 123, 0.12);
  color: #d85a30;
}
.reopen-warning {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  background: rgba(230, 162, 60, 0.08);
  border: 1px solid rgba(230, 162, 60, 0.25);
  border-radius: 8px;
  padding: 10px 12px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--solo-text-secondary, #586474);
  margin-bottom: 16px;
}
.reopen-warning i {
  color: #ba7517;
  font-size: 15px;
  flex-shrink: 0;
  margin-top: 2px;
}
.reopen-field {
  margin-bottom: 14px;
}
.reopen-field > label {
  display: block;
  font-size: 12.5px;
  font-weight: 500;
  color: var(--solo-text-primary);
  margin-bottom: 6px;
}
.reopen-verify {
  display: grid;
  grid-template-columns: 1fr 1fr auto;
  gap: 8px;
  align-items: center;
}
.reopen-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 18px;
}
</style>
