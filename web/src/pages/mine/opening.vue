<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NButton, NTag, NSpace, useMessage, NInput, NSpin, NSelect, NModal, NFormItem } from 'naive-ui'
import apiClient from '@/api/client'
import { getAuthMe, requestStepUp, sendPhoneCode } from '@/api/auth'

interface BalanceItem {
  id?: string
  company_id?: string
  account_code: string
  account_name?: string
  debit_balance: number
  credit_balance: number
  fiscal_year: number
  voucherStatus?: string | null
  voucherRef?: string | null
}

const router = useRouter()
const message = useMessage()

const loading = ref(false)
const saving = ref(false)
const items = ref<BalanceItem[]>([])
const fiscalYear = ref(new Date().getFullYear())
const stepUpVisible = ref(false)
const phone = ref('')
const code = ref('')
const bizId = ref('')
const sendingCode = ref(false)
const isLocked = computed(() => items.value.some(i => ['posted', 'reversed'].includes(i.voucherStatus || '')))

const expenseAssetCodes = new Set([
  '5001', '5601', '5602', '5603', '5604', '5605', '5606', '5607', '5608', '5609',
  '5699', '6601', '6401', '6711', '6001', '6051', '6301',
])

function isBalanceAccount(code: string): boolean {
  // Balance sheet accounts (资产/负债/权益) need debit/credit balance
  // Income/expense accounts are closed at period end
  return !expenseAssetCodes.has(code)
}

const balanceItems = computed(() => items.value.filter(i => isBalanceAccount(i.account_code)))

const totalDebit = computed(() =>
  Math.round(balanceItems.value.reduce((s, i) => s + (i.debit_balance || 0), 0) * 100) / 100
)
const totalCredit = computed(() =>
  Math.round(balanceItems.value.reduce((s, i) => s + (i.credit_balance || 0), 0) * 100) / 100
)
const isBalanced = computed(() => Math.abs(totalDebit.value - totalCredit.value) <= 0.01)

async function load() {
  loading.value = true
  try {
    const data: any = await apiClient.get('/api/opening/balances', {
      params: { fiscal_year: fiscalYear.value },
    })
    items.value = data.items || []
  } catch {
    message.error('加载期初余额失败')
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!isBalanced.value) {
    message.warning('借方合计与贷方合计不相等，请检查')
    return
  }
  if (isLocked.value) {
    message.warning('期初凭证已过账；如需调整，请先冲销并创建调整凭证')
    return
  }
  stepUpVisible.value = true
}

async function sendCode() {
  if (!phone.value) {
    message.warning('请输入当前账号已绑定的手机号')
    return
  }
  sendingCode.value = true
  try {
    const result = await sendPhoneCode({ phone: phone.value })
    bizId.value = result.biz_id || ''
    message.success('验证码已发送')
  } catch {
    message.error('验证码发送失败')
  } finally {
    sendingCode.value = false
  }
}

async function confirmSave() {
  if (!phone.value || !code.value) {
    message.warning('请输入手机号和验证码')
    return
  }
  saving.value = true
  try {
    const auth = await requestStepUp({
      action: 'adjust_opening_balance',
      phone: phone.value,
      code: code.value,
      bizId: bizId.value || undefined,
    })
    await apiClient.put('/api/opening/balances', {
      fiscal_year: fiscalYear.value,
      items: items.value.map(i => ({
        account_code: i.account_code,
        account_name: i.account_name,
        debit_balance: i.debit_balance || 0,
        credit_balance: i.credit_balance || 0,
      })),
    }, {
      headers: {
        'Step-Up-Token': auth.stepUpToken,
        'Idempotency-Key': `opening-${fiscalYear.value}-${Date.now()}`,
      },
    })
    stepUpVisible.value = false
    code.value = ''
    message.success('期初凭证已生成并提交')
    await load()
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : detail?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

onMounted(async () => {
  await load()
  try {
    const profile: any = await getAuthMe()
    phone.value = profile?.user?.phone || ''
  } catch {
    // 绑定手机号会在提交时由后端再次校验。
  }
})
</script>

<template>
  <div class="opening-page">
    <div class="page-header">
      <NButton text @click="router.push('/mine')">← 返回</NButton>
      <div class="header-row">
        <h2>期初余额</h2>
        <NSpace>
          <NSelect
            v-model:value="fiscalYear"
            :options="[
              { label: '2025', value: 2025 },
              { label: '2026', value: 2026 },
            ]"
            style="width: 100px;"
            @update:value="load"
          />
          <NButton size="small" type="primary" :loading="saving" :disabled="isLocked" @click="save">
            {{ isLocked ? '已过账锁定' : '生成期初凭证' }}
          </NButton>
        </NSpace>
      </div>
    </div>

    <div class="balance-summary" :class="{ balanced: isBalanced, unbalanced: !isBalanced }">
      <span>借方合计：{{ totalDebit.toFixed(2) }}</span>
      <span>贷方合计：{{ totalCredit.toFixed(2) }}</span>
      <NTag v-if="isBalanced" size="small" type="success" :bordered="false">✓ 平衡</NTag>
      <NTag v-else size="small" type="error" :bordered="false">✗ 不平衡</NTag>
    </div>

    <NSpin :show="loading">
      <NCard :bordered="false" class="table-card">
        <table class="balance-table">
          <thead>
            <tr>
              <th class="col-code">科目代码</th>
              <th class="col-name">科目名称</th>
              <th class="col-amount">借方余额</th>
              <th class="col-amount">贷方余额</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in balanceItems" :key="item.account_code">
              <td class="col-code">{{ item.account_code }}</td>
              <td class="col-name">{{ item.account_name || item.account_code }}</td>
              <td class="col-amount">
                <NInput
                  :disabled="isLocked"
                  :value="item.debit_balance ? String(item.debit_balance) : ''"
                  @update:value="(v: any) => { item.debit_balance = parseFloat(v) || 0 }"
                  placeholder="0.00"
                  size="small"
                  style="width: 120px;"
                />
              </td>
              <td class="col-amount">
                <NInput
                  :disabled="isLocked"
                  :value="item.credit_balance ? String(item.credit_balance) : ''"
                  @update:value="(v: any) => { item.credit_balance = parseFloat(v) || 0 }"
                  placeholder="0.00"
                  size="small"
                  style="width: 120px;"
                />
              </td>
            </tr>
          </tbody>
          <tfoot>
            <tr>
              <td colspan="2" class="col-total">合计</td>
              <td class="col-amount total">{{ totalDebit.toFixed(2) }}</td>
              <td class="col-amount total">{{ totalCredit.toFixed(2) }}</td>
            </tr>
          </tfoot>
        </table>
      </NCard>
    </NSpin>

    <NModal v-model:show="stepUpVisible" preset="card" title="短信确认期初凭证" style="max-width: 420px">
      <NFormItem label="已绑定手机号">
        <NInput v-model:value="phone" placeholder="请输入当前账号手机号" />
      </NFormItem>
      <NFormItem label="短信验证码">
        <NSpace :wrap="false" style="width: 100%">
          <NInput v-model:value="code" placeholder="验证码" />
          <NButton :loading="sendingCode" @click="sendCode">发送验证码</NButton>
        </NSpace>
      </NFormItem>
      <p class="step-up-note">提交后生成不可修改的期初凭证；如需更正，必须冲销后重做。</p>
      <NSpace justify="end">
        <NButton @click="stepUpVisible = false">取消</NButton>
        <NButton type="primary" :loading="saving" @click="confirmSave">确认生成</NButton>
      </NSpace>
    </NModal>
  </div>
</template>

<style scoped>
.opening-page {
  max-width: 720px;
  margin: 0 auto;
}
.page-header {
  margin-bottom: 16px;
}
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
}
.header-row h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.balance-summary {
  display: flex;
  align-items: center;
  gap: 16px;
  padding: 10px 14px;
  border-radius: 8px;
  margin-bottom: 16px;
  font-size: 14px;
  font-weight: 600;
  background: var(--solo-bg-secondary, #f5f5f5);
}
.balance-summary.balanced {
  color: var(--solo-income, #18a058);
}
.balance-summary.unbalanced {
  color: var(--solo-expense, #d03050);
}
.table-card {
  border-radius: var(--solo-radius-lg);
  overflow-x: auto;
}
.balance-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.balance-table th {
  text-align: left;
  padding: 8px 10px;
  font-weight: 600;
  color: var(--solo-text-secondary);
  border-bottom: 2px solid var(--solo-border-light);
}
.balance-table td {
  padding: 6px 10px;
  border-bottom: 1px solid var(--solo-border-light);
}
.balance-table tfoot td {
  border-top: 2px solid var(--solo-border-light);
  font-weight: 600;
}
.col-code { width: 100px; }
.col-name { width: 180px; }
.col-amount { width: 140px; text-align: right; }
.col-total { text-align: right; padding-right: 16px; }
.total {
  font-weight: 700;
  color: var(--solo-text-primary);
}
.step-up-note {
  color: var(--solo-text-secondary);
  font-size: 13px;
  line-height: 1.6;
}
</style>
