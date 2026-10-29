<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import {
  NForm, NFormItem, NInput, NInputNumber, NSelect, NButton,
  useMessage, NSpace, NDatePicker, NRadioGroup, NRadioButton,
} from 'naive-ui'
import dayjs from 'dayjs'
import { SoloDrawer } from '@/components/ui'
import {
  getExpense, createExpense, updateExpense, getExpenseSummary,
} from '@/api/private-expense'
import apiClient from '@/api/client'
import { getActivePinia } from 'pinia'
import { useAuthStore } from '@/stores/auth'

const props = withDefaults(
  defineProps<{
    show: boolean
    expenseId?: string | null
  }>(),
  {
    expenseId: null,
  },
)

const emit = defineEmits<{
  'update:show': [value: boolean]
  success: []
}>()

const message = useMessage()

const loading = ref(false)
const saving = ref(false)
const showAdvanced = ref(false)

const PAYER_OPTIONS = [
  { value: 'cash', label: '现金' },
  { value: 'wechat', label: '微信' },
  { value: 'alipay', label: '支付宝' },
  { value: 'card', label: '银行卡' },
  { value: 'other', label: '其他' },
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

const QUICK_EXPENSE_PRESETS = [
  { label: 'GPT 订阅', category: 'AI/软件订阅' },
  { label: '打车差旅', category: '交通差旅' },
  { label: '打印资料', category: '打印复印' },
  { label: '办公用品', category: '办公用品' },
  { label: '客户招待', category: '业务招待' },
  { label: '快递跑腿', category: '快递物流' },
]

const QUICK_INCOME_PRESETS = [
  { label: '客户私下收款', category: '私下收款' },
  { label: '客户回款', category: '客户回款' },
  { label: '平台提现', category: '平台提现' },
  { label: '现金收入', category: '现金收入' },
]

const form = ref({
  title: '',
  amount: null as number | null,
  direction: 'expense' as 'income' | 'expense',
  category: '',
  occur_date: dayjs().format('YYYY-MM-DD'),
  payer: '',
  owner_name: '本人',
  private_account: '个人微信',
  is_available: true,
  memo: '',
  invoice_status: 'no_invoice' as 'no_invoice' | 'has_invoice' | 'substitute',
  substitute_entry_id: '',
  substitute_memo: '',
})

const officialEntries = ref<any[]>([])
const ownerOptions = ref<Array<{ label: string; value: string }>>([])

const isEdit = computed(() => !!props.expenseId)
const categoryOptions = computed(() => (
  form.value.direction === 'income' ? INCOME_CATEGORY_OPTIONS : EXPENSE_CATEGORY_OPTIONS
))
const quickPresets = computed(() => (
  form.value.direction === 'income' ? QUICK_INCOME_PRESETS : QUICK_EXPENSE_PRESETS
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
      label: `[${e.occurDate?.slice(0, 10) || ''}] ${e.title} - ¥${Number(e.amount || 0).toFixed(2)}`,
    }))
})

function getStorageKeys() {
  let companyKey = 'default'
  if (getActivePinia()) {
    try {
      const authStore = useAuthStore()
      companyKey = authStore.currentCompanyId || 'default'
    } catch {
      // test environment without pinia
    }
  }
  return {
    owner: `solobooks:last-private-expense-owner:${companyKey}`,
    account: `solobooks:last-private-expense-account:${companyKey}`,
  }
}

async function initForm() {
  const keys = getStorageKeys()
  const lastOwner = localStorage.getItem(keys.owner) || ''
  const lastAccount = localStorage.getItem(keys.account) || ''

  try {
    const ownerSummary = await getExpenseSummary()
    const names = new Set((ownerSummary.owners || []).map(item => item.owner_name))
    if (lastOwner) names.add(lastOwner)
    if (!names.has('本人')) names.add('本人')
    ownerOptions.value = [...names].map(name => ({ label: name, value: name }))
  } catch {
    ownerOptions.value = [{ label: '本人', value: '本人' }]
  }

  try {
    const resp: any = await apiClient.get('/api/record/entries')
    officialEntries.value = resp.entries || []
  } catch {
    officialEntries.value = []
  }

  if (props.expenseId) {
    loading.value = true
    try {
      const data = await getExpense(props.expenseId)
      form.value = {
        title: data.title,
        amount: normalizeAmount(data.amount),
        direction: data.direction || 'expense',
        category: data.category,
        occur_date: data.occur_date || dayjs().format('YYYY-MM-DD'),
        payer: data.payer,
        owner_name: data.owner_name || '本人',
        private_account: data.private_account || '个人微信',
        is_available: data.is_available === undefined ? true : Boolean(data.is_available),
        memo: data.memo,
        invoice_status: data.invoice_status || 'no_invoice',
        substitute_entry_id: data.substitute_entry_id || '',
        substitute_memo: data.substitute_memo || '',
      }
      if (data.invoice_status === 'substitute' || data.memo || data.payer || !data.is_available) {
        showAdvanced.value = true
      }
    } catch {
      message.error('加载记录详情失败')
      emit('update:show', false)
    } finally {
      loading.value = false
    }
  } else {
    showAdvanced.value = false
    form.value = {
      title: '',
      amount: null,
      direction: 'expense',
      category: '',
      occur_date: dayjs().format('YYYY-MM-DD'),
      payer: '',
      owner_name: lastOwner || ownerOptions.value[0]?.value || '本人',
      private_account: lastAccount || '个人微信',
      is_available: true,
      memo: '',
      invoice_status: 'no_invoice',
      substitute_entry_id: '',
      substitute_memo: '',
    }
  }
}

watch(
  () => props.show,
  (val) => {
    if (val) {
      initForm()
    }
  },
  { immediate: true },
)

function handleDirectionChange(val: 'income' | 'expense') {
  form.value.direction = val
  form.value.category = ''
  if (val === 'income') {
    form.value.invoice_status = 'no_invoice'
    form.value.substitute_entry_id = ''
    form.value.substitute_memo = ''
  }
}

function handleAccountChange(value: string) {
  form.value.is_available = !value.includes('保证金') && !value.includes('待结算')
}

function setDate(offsetDays: number) {
  form.value.occur_date = dayjs().subtract(offsetDays, 'day').format('YYYY-MM-DD')
}

function applyPreset(preset: { label: string; category: string }) {
  form.value.title = preset.label
  if (!form.value.category) {
    form.value.category = preset.category
  }
}

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
      occur_date: form.value.occur_date || dayjs().format('YYYY-MM-DD'),
      payer: form.value.payer,
      owner_name: form.value.owner_name.trim() || '本人',
      private_account: form.value.private_account,
      is_available: form.value.direction === 'income' ? form.value.is_available : true,
      memo: form.value.memo.trim(),
      invoice_status: form.value.invoice_status,
      substitute_entry_id: form.value.direction === 'expense' && form.value.invoice_status === 'substitute' ? form.value.substitute_entry_id : '',
      substitute_memo: form.value.direction === 'expense' && form.value.invoice_status === 'substitute' ? form.value.substitute_memo.trim() : '',
    }

    if (props.expenseId) {
      await updateExpense(props.expenseId, payload)
      message.success('已更新私账记录')
    } else {
      await createExpense(payload)
      message.success('已记一笔私账')
    }

    const keys = getStorageKeys()
    localStorage.setItem(keys.owner, payload.owner_name)
    localStorage.setItem(keys.account, payload.private_account)

    emit('update:show', false)
    emit('success')
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : '保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <SoloDrawer
    :show="show"
    :title="isEdit ? '编辑私账记录' : '新增私账记录'"
    :width="540"
    @update:show="emit('update:show', $event)"
  >
    <div class="drawer-inner">
      <div class="direction-segment">
        <button
          type="button"
          class="segment-item segment-expense"
          :class="{ active: form.direction === 'expense' }"
          @click="handleDirectionChange('expense')"
        >
          <i class="ri-arrow-right-up-line" />
          <span>支出（个人垫付）</span>
        </button>
        <button
          type="button"
          class="segment-item segment-income"
          :class="{ active: form.direction === 'income' }"
          @click="handleDirectionChange('income')"
        >
          <i class="ri-arrow-left-down-line" />
          <span>收入（私户代收）</span>
        </button>
      </div>

      <!-- 金额 Hero 输入区域 -->
      <div class="amount-hero" :class="form.direction">
        <div class="amount-label">
          <span>金额</span>
          <span class="amount-tip">{{ form.direction === 'expense' ? '老板或员工垫付的金额' : '私户代收的经营资金' }}</span>
        </div>
        <div class="amount-input-box">
          <span class="currency-symbol">¥</span>
          <NInputNumber
            v-model:value="form.amount"
            :min="0"
            :precision="2"
            :show-button="false"
            clearable
            placeholder="0.00"
            class="amount-field"
          />
        </div>
      </div>

      <NForm label-placement="top" :model="form" class="pe-form">
        <!-- 事由 -->
        <NFormItem label="事由" required>
          <NInput
            v-model:value="form.title"
            clearable
            :placeholder="form.direction === 'income' ? '如：客户私下转账、平台提现' : '如：GPT 订阅、打车、打印资料'"
            maxlength="120"
          />
        </NFormItem>

        <!-- 常用快捷胶囊 -->
        <div class="quick-presets">
          <button
            v-for="p in quickPresets"
            :key="p.label"
            type="button"
            class="preset-chip"
            @click="applyPreset(p)"
          >
            + {{ p.label }}
          </button>
        </div>

        <!-- 分类与日期 -->
        <div class="form-row">
          <NFormItem label="分类" class="col-item">
            <NSelect
              v-model:value="form.category"
              :options="categoryOptions"
              clearable
              filterable
              tag
              placeholder="选择分类"
            />
          </NFormItem>
          <NFormItem label="日期" class="col-item">
            <NDatePicker
              v-model:formatted-value="form.occur_date"
              value-format="yyyy-MM-dd"
              type="date"
              placeholder="发生日期"
              style="width: 100%;"
            />
            <div class="date-shortcuts">
              <span class="date-link" @click="setDate(0)">今天</span>
              <span class="date-link" @click="setDate(1)">昨天</span>
            </div>
          </NFormItem>
        </div>

        <!-- 归属人与私账账户 -->
        <div class="form-row">
          <NFormItem label="资金归属人" required class="col-item">
            <NSelect
              v-model:value="form.owner_name"
              :options="ownerOptions"
              tag
              filterable
              placeholder="如：本人、股东A"
            />
          </NFormItem>
          <NFormItem label="私账账户" required class="col-item">
            <NSelect
              v-model:value="form.private_account"
              :options="PRIVATE_ACCOUNT_OPTIONS"
              tag
              filterable
              placeholder="选择或输入账户"
              @update:value="handleAccountChange"
            />
          </NFormItem>
        </div>

        <!-- 高级折叠面板 -->
        <div class="advanced-divider">
          <button
            type="button"
            class="advanced-toggle-btn"
            @click="showAdvanced = !showAdvanced"
          >
            <span>高级选项（支付方式、发票凭证、备注）</span>
            <i :class="showAdvanced ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'" />
          </button>
        </div>

        <div v-show="showAdvanced" class="advanced-body">
          <NFormItem :label="form.direction === 'income' ? '收款方式' : '支付方式'">
            <NSelect
              v-model:value="form.payer"
              :options="PAYER_OPTIONS"
              clearable
              :placeholder="form.direction === 'income' ? '选择收款方式' : '选择支付方式'"
            />
          </NFormItem>

          <template v-if="form.direction === 'expense'">
            <NFormItem label="票据状态">
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

            <div v-if="form.invoice_status === 'substitute'" class="substitute-box">
              <NFormItem label="关联正式支出账目（选填）">
                <NSelect
                  v-model:value="form.substitute_entry_id"
                  :options="officialEntryOptions"
                  filterable
                  clearable
                  placeholder="选择后续用于补票/冲抵的正式支出"
                />
              </NFormItem>
              <NFormItem label="关联说明">
                <NInput
                  v-model:value="form.substitute_memo"
                  type="textarea"
                  :rows="2"
                  placeholder="如：GPT 订阅未取得国内发票，后续关联软件服务费凭证处理"
                />
              </NFormItem>
            </div>
          </template>

          <template v-else>
            <NFormItem label="资金状态">
              <NRadioGroup v-model:value="form.is_available" class="status-toggle">
                <NRadioButton :value="true">可用资金</NRadioButton>
                <NRadioButton :value="false">受限资金</NRadioButton>
              </NRadioGroup>
              <p class="field-tip">平台保证金、待结算款项选“受限资金”，暂不参与冲销结算。</p>
            </NFormItem>
          </template>

          <NFormItem label="备注">
            <NInput
              v-model:value="form.memo"
              type="textarea"
              :rows="2"
              placeholder="可选备忘信息"
              maxlength="200"
            />
          </NFormItem>
        </div>
      </NForm>
    </div>

    <template #footer>
      <NSpace justify="end" :size="12">
        <NButton @click="emit('update:show', false)">取消</NButton>
        <NButton type="primary" :loading="saving" @click="handleSave">
          保存记录
        </NButton>
      </NSpace>
    </template>
  </SoloDrawer>
</template>

<style scoped>
.drawer-inner {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding-bottom: 24px;
}

/* 顶部收支切换 Segmented Control */
.direction-segment {
  display: flex;
  gap: 8px;
  background: var(--solo-bg-soft, #f1f4f8);
  padding: 4px;
  border-radius: var(--solo-radius, 8px);
}

.segment-item {
  flex: 1;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  height: 38px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: var(--solo-text-secondary, #586474);
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.2s ease;
}

.segment-item i {
  font-size: 16px;
}

.segment-expense.active {
  background: #ffffff;
  color: var(--solo-expense, #bd3d3d);
  border-color: rgba(189, 61, 61, 0.25);
  box-shadow: 0 1px 3px rgba(189, 61, 61, 0.08);
  font-weight: 600;
}

.segment-income.active {
  background: #ffffff;
  color: var(--solo-income, #0f7354);
  border-color: rgba(15, 115, 84, 0.25);
  box-shadow: 0 1px 3px rgba(15, 115, 84, 0.08);
  font-weight: 600;
}

/* 金额 Hero 输入区域 */
.amount-hero {
  border-radius: var(--solo-radius, 8px);
  padding: 12px 16px;
  background: var(--solo-bg-page, #f6f7f9);
  border: 1px solid var(--solo-border, #dde3ea);
  transition: border-color 0.2s ease;
}

.amount-hero.expense {
  border-left: 4px solid var(--solo-expense, #bd3d3d);
}

.amount-hero.income {
  border-left: 4px solid var(--solo-income, #0f7354);
}

.amount-label {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
  color: var(--solo-text-secondary, #586474);
  margin-bottom: 4px;
}

.amount-tip {
  font-size: 11px;
  color: var(--solo-text-muted, #636e7c);
}

.amount-input-box {
  display: flex;
  align-items: center;
  gap: 8px;
}

.currency-symbol {
  font-size: 26px;
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
  line-height: 1;
}

.amount-field {
  flex: 1;
}

.amount-field :deep(.n-input) {
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
}

.amount-field :deep(.n-input__input-el) {
  font-size: 28px !important;
  font-weight: 700 !important;
  font-variant-numeric: tabular-nums;
  height: 42px;
  line-height: 42px;
  padding: 0 !important;
  color: var(--solo-text-primary, #182230);
}

/* 表单主体 */
.pe-form :deep(.n-form-item) {
  margin-bottom: 14px;
}

.pe-form :deep(.n-form-item-label) {
  font-size: 13px;
  font-weight: 500;
  color: var(--solo-text-primary, #182230);
  padding-bottom: 4px;
}

/* 快捷预设胶囊 */
.quick-presets {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-top: -6px;
  margin-bottom: 14px;
}

.preset-chip {
  background: var(--solo-bg-soft, #f1f4f8);
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: 4px;
  padding: 2px 8px;
  font-size: 12px;
  color: var(--solo-text-secondary, #586474);
  cursor: pointer;
  transition: all 0.15s ease;
}

.preset-chip:hover {
  background: var(--solo-bg-blue-soft, #edf3ff);
  color: var(--solo-primary, #1747b0);
  border-color: var(--solo-primary-light, #2b66e2);
}

/* 表单两列排列 */
.form-row {
  display: flex;
  gap: 12px;
}

.col-item {
  flex: 1;
  min-width: 0;
}

.date-shortcuts {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}

.date-link {
  font-size: 11px;
  color: var(--solo-primary, #1747b0);
  cursor: pointer;
  user-select: none;
}

.date-link:hover {
  text-decoration: underline;
}

/* 高级选项折叠 */
.advanced-divider {
  margin: 8px 0 12px;
  border-top: 1px dashed var(--solo-border, #dde3ea);
  padding-top: 10px;
}

.advanced-toggle-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: none;
  border: none;
  padding: 0;
  font-size: 13px;
  color: var(--solo-text-secondary, #586474);
  cursor: pointer;
  font-weight: 500;
  transition: color 0.15s;
}

.advanced-toggle-btn:hover {
  color: var(--solo-primary, #1747b0);
}

.advanced-toggle-btn i {
  font-size: 16px;
}

.advanced-body {
  padding: 12px;
  background: var(--solo-bg-page, #f6f7f9);
  border-radius: var(--solo-radius, 8px);
  border: 1px solid var(--solo-border, #dde3ea);
  margin-bottom: 8px;
}

.substitute-box {
  background: rgba(245, 158, 11, 0.04);
  border: 1px dashed rgba(160, 92, 16, 0.3);
  border-radius: var(--solo-radius, 8px);
  padding: 10px 12px;
  margin-bottom: 12px;
}

.field-tip {
  margin: 4px 0 0;
  font-size: 11px;
  color: var(--solo-text-muted, #636e7c);
}
</style>
