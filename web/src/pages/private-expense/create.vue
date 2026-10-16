<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NForm, NFormItem, NInput, NInputNumber, NSelect, NButton, NCard, useMessage, NSpace, NDatePicker, NRadioGroup, NRadioButton } from 'naive-ui'
import { getExpense, createExpense, updateExpense, getExpenseSummary } from '@/api/private-expense'
import apiClient from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const authStore = useAuthStore()

const isEdit = !!route.params.id
const loading = ref(false)
const saving = ref(false)

const PAYER_OPTIONS = [
  { value: 'cash', label: '现金' },
  { value: 'wechat', label: '微信' },
  { value: 'alipay', label: '支付宝' },
  { value: 'card', label: '银行卡' },
  { value: 'other', label: '其他' },
]

const DIRECTION_OPTIONS = [
  { value: 'expense', label: '支出' },
  { value: 'income', label: '收入' },
]

const PRIVATE_ACCOUNT_OPTIONS = [
  { value: '个人微信', label: '个人微信' },
  { value: '个人支付宝', label: '个人支付宝' },
  { value: '个人银行卡', label: '个人银行卡' },
  { value: '现金', label: '现金' },
  { value: '平台余额', label: '平台余额' },
  { value: '平台待结算', label: '平台待结算' },
  { value: '平台保证金', label: '平台保证金' },
  { value: '其他', label: '其他' },
]

const EXPENSE_CATEGORY_OPTIONS = [
  'AI/软件订阅', '打印复印', '办公用品', '快递物流',
  '交通差旅', '业务招待', '通讯网络', '资料/培训',
  '设计/技术服务', '小额采购', '平台服务费', '押金保证金', '其他经营支出',
].map(c => ({ value: c, label: c }))

const INCOME_CATEGORY_OPTIONS = [
  '私下收款', '客户回款', '平台提现', '现金收入',
  '微信/支付宝收款', '项目尾款', '代收款项', '其他经营收入',
].map(c => ({ value: c, label: c }))

const form = ref({
  title: '',
  amount: null as number | null,
  direction: 'expense' as 'income' | 'expense',
  category: '',
  occur_date: null as string | null,
  payer: '',
  owner_name: '',
  private_account: '',
  is_available: true,
  memo: '',
  invoice_status: 'no_invoice' as 'no_invoice' | 'has_invoice' | 'substitute',
  substitute_entry_id: '',
  substitute_memo: '',
})

const officialEntries = ref<any[]>([])
const ownerOptions = ref<Array<{ label: string; value: string }>>([])
const categoryOptions = computed(() => (
  form.value.direction === 'income' ? INCOME_CATEGORY_OPTIONS : EXPENSE_CATEGORY_OPTIONS
))

function normalizeAmount(amount: number | null) {
  if (amount === null || !Number.isFinite(amount)) return null
  return Math.round(amount * 100) / 100
}

const officialEntryOptions = computed(() => {
  return officialEntries.value
    .filter(e => e.direction === 'expense')
    .map(e => ({
      value: e.id,
      label: `[${e.occurDate?.slice(0, 10)}] ${e.title} - ¥${e.amount?.toFixed(2)}`
    }))
})

onMounted(async () => {
  try {
    const ownerSummary = await getExpenseSummary()
    const storageKey = `solobooks:last-private-expense-owner:${authStore.currentCompanyId || 'default'}`
    const lastOwner = localStorage.getItem(storageKey) || ''
    const names = new Set((ownerSummary.owners || []).map(item => item.owner_name))
    if (lastOwner) names.add(lastOwner)
    ownerOptions.value = [...names].map(name => ({ label: name, value: name }))
    if (!isEdit) {
      form.value.owner_name = lastOwner || ownerOptions.value[0]?.value || ''
    }
  } catch {
    // 新账户仍可直接输入归属人
  }
  // Fetch official entries
  try {
    const resp: any = await apiClient.get('/api/record/entries')
    officialEntries.value = resp.entries || []
  } catch {
    // silent
  }

  if (isEdit) {
    loading.value = true
    try {
      const data = await getExpense(route.params.id as string)
      form.value = {
        title: data.title,
        amount: normalizeAmount(data.amount),
        direction: data.direction || 'expense',
        category: data.category,
        occur_date: data.occur_date || null,
        payer: data.payer,
        owner_name: data.owner_name || '本人',
        private_account: data.private_account || '',
        is_available: data.is_available === undefined ? true : Boolean(data.is_available),
        memo: data.memo,
        invoice_status: data.invoice_status || 'no_invoice',
        substitute_entry_id: data.substitute_entry_id || '',
        substitute_memo: data.substitute_memo || '',
      }
    } catch {
      message.error('加载失败')
      router.push('/private-expenses')
    } finally {
      loading.value = false
    }
  }
})

async function handleSave() {
  if (!form.value.title.trim()) {
    message.warning('请填写事由')
    return
  }
  const amount = normalizeAmount(form.value.amount)
  if (amount === null || amount <= 0) {
    message.warning('请填写有效金额')
    return
  }
  if (!form.value.owner_name.trim()) {
    message.warning('请填写资金归属人')
    return
  }
  if (!form.value.private_account) {
    message.warning('请选择或输入私账账户')
    return
  }

  saving.value = true
  try {
    const payload = {
      title: form.value.title.trim(),
      amount,
      direction: form.value.direction,
      category: form.value.category,
      occur_date: form.value.occur_date || '',
      payer: form.value.payer,
      owner_name: form.value.owner_name.trim() || '本人',
      private_account: form.value.private_account,
      is_available: form.value.direction === 'income' ? form.value.is_available : true,
      memo: form.value.memo.trim(),
      invoice_status: form.value.invoice_status,
      substitute_entry_id: form.value.direction === 'expense' && form.value.invoice_status === 'substitute' ? form.value.substitute_entry_id : '',
      substitute_memo: form.value.direction === 'expense' && form.value.invoice_status === 'substitute' ? form.value.substitute_memo.trim() : '',
    }

    if (isEdit) {
      await updateExpense(route.params.id as string, payload)
      message.success('更新成功')
    } else {
      await createExpense(payload)
      message.success('创建成功')
    }
    const storageKey = `solobooks:last-private-expense-owner:${authStore.currentCompanyId || 'default'}`
    localStorage.setItem(storageKey, payload.owner_name)
    router.push('/private-expenses')
  } catch {
    message.error('保存失败')
  } finally {
    saving.value = false
  }
}

function handleDirectionChange() {
  form.value.category = ''
  if (form.value.direction === 'income') {
    form.value.invoice_status = 'no_invoice'
    form.value.substitute_entry_id = ''
    form.value.substitute_memo = ''
  }
}

function handleAccountChange(value: string) {
  form.value.is_available = !value.includes('保证金') && !value.includes('待结算')
}
</script>

<template>
  <div class="pe-form-page">
    <div class="page-header">
      <div class="header-left">
        <NButton text class="back-btn" @click="router.push('/private-expenses')">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回
        </NButton>
        <div class="title-section">
          <h2>{{ isEdit ? '编辑记录' : '新增记录' }}</h2>
          <p class="subtitle">记录未走公司公账的经营收支，用于后续补票、报销、冲销与无票追踪</p>
        </div>
      </div>
    </div>

    <NCard :bordered="false" class="form-card">
      <NForm label-placement="top" :model="form">
        <NFormItem label="类型" required>
          <NRadioGroup
            v-model:value="form.direction"
            class="direction-toggle"
            @update:value="handleDirectionChange"
          >
            <NRadioButton
              v-for="option in DIRECTION_OPTIONS"
              :key="option.value"
              :value="option.value"
            >
              {{ option.label }}
            </NRadioButton>
          </NRadioGroup>
        </NFormItem>
        <NFormItem label="事由" required>
          <NInput
            v-model:value="form.title"
            :placeholder="form.direction === 'income' ? '如：客户私下转账、平台提现' : '如：GPT 订阅、打印资料'"
          />
        </NFormItem>
        <NFormItem label="金额" required>
          <NInputNumber
            v-model:value="form.amount"
            :min="0"
            :step="0.01"
            :precision="2"
            clearable
            placeholder="0.00"
            style="width: 100%;"
          />
        </NFormItem>
        <div class="form-row">
          <NFormItem label="分类">
            <NSelect v-model:value="form.category" :options="categoryOptions" clearable placeholder="选择分类" />
          </NFormItem>
          <NFormItem label="日期">
            <NDatePicker
              v-model:formatted-value="form.occur_date"
              value-format="yyyy-MM-dd"
              type="date"
              placeholder="选择日期"
              style="width: 100%;"
            />
          </NFormItem>
        </div>
        <div class="form-row">
          <NFormItem label="资金归属人" required>
            <NSelect
              v-model:value="form.owner_name"
              :options="ownerOptions"
              tag
              filterable
              placeholder="如：本人、股东A、股东B"
            />
          </NFormItem>
          <NFormItem label="私账账户" required>
            <NSelect
              v-model:value="form.private_account"
              :options="PRIVATE_ACCOUNT_OPTIONS"
              tag
              filterable
              placeholder="选择或输入资金所在账户"
              @update:value="handleAccountChange"
            />
          </NFormItem>
        </div>
        <NFormItem v-if="form.direction === 'income'" label="资金状态">
          <NRadioGroup v-model:value="form.is_available" class="direction-toggle">
            <NRadioButton :value="true">可用资金</NRadioButton>
            <NRadioButton :value="false">受限资金</NRadioButton>
          </NRadioGroup>
          <p class="field-help">平台保证金、平台待结算请选择“受限资金”，不会参与冲销。</p>
        </NFormItem>
        <NFormItem :label="form.direction === 'income' ? '收款方式' : '支付方式'">
          <NSelect
            v-model:value="form.payer"
            :options="PAYER_OPTIONS"
            clearable
            :placeholder="form.direction === 'income' ? '选择收款方式' : '选择支付方式'"
          />
        </NFormItem>
        <NFormItem v-if="form.direction === 'expense'" label="票据状态">
          <NSelect
            v-model:value="form.invoice_status"
            :options="[
              { value: 'no_invoice', label: '无发票 (经营支出但暂未取得发票，先做私账追踪)' },
              { value: 'has_invoice', label: '有发票 (有合规发票，可正常报销/入账)' },
              { value: 'substitute', label: '后续补票/冲抵 (先记录，后续关联正式支出凭证)' },
            ]"
            placeholder="选择票据状态"
          />
        </NFormItem>
        <NFormItem v-else label="收款状态">
          <div class="status-note">未走公司公账，先做私账收入追踪</div>
        </NFormItem>
        <div v-if="form.direction === 'expense' && form.invoice_status === 'substitute'" class="substitute-fields" style="background: rgba(245, 158, 11, 0.03); padding: 12px; border-radius: var(--solo-radius); border: 1px dashed var(--solo-border-light); margin-bottom: 14px;">
          <NFormItem label="关联正式支出账目" required>
            <NSelect
              v-model:value="form.substitute_entry_id"
              :options="officialEntryOptions"
              filterable
              placeholder="选择后续用于补票/冲抵的正式支出"
            />
          </NFormItem>
          <NFormItem label="关联说明">
            <NInput
              v-model:value="form.substitute_memo"
              type="textarea"
              :rows="2"
              placeholder="如：GPT 订阅未取得发票，后续关联软件服务费凭证处理。"
            />
          </NFormItem>
        </div>
        <NFormItem label="备注">
          <NInput v-model:value="form.memo" type="textarea" :rows="2" placeholder="可选备注" />
        </NFormItem>
      </NForm>

      <div class="form-actions">
        <NSpace>
          <NButton @click="router.push('/private-expenses')">取消</NButton>
          <NButton type="primary" :loading="saving" @click="handleSave">保存</NButton>
        </NSpace>
      </div>
    </NCard>
  </div>
</template>

<style scoped>
.pe-form-page {
  max-width: 640px;
  margin: 0 auto;
}
.page-header {
  margin-bottom: 20px;
}
.page-header h2 {
  margin: 8px 0 0;
  font-size: 20px;
  font-weight: 600;
}
.form-card {
  border-radius: var(--solo-radius-lg);
}
.form-row {
  display: flex;
  gap: 16px;
}
.form-row .n-form-item {
  flex: 1;
}
.form-actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 24px;
}
.direction-toggle {
  width: 100%;
  display: flex;
}
.direction-toggle :deep(.n-radio-button) {
  flex: 1;
  text-align: center;
}
.status-note {
  width: 100%;
  box-sizing: border-box;
  padding: 9px 12px;
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius);
  background: var(--solo-bg-page);
  color: var(--solo-text-secondary);
}
.field-help {
  margin: 6px 0 0;
  color: var(--solo-text-secondary);
  font-size: 12px;
}
</style>
