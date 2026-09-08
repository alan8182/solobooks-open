<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NCard, NButton, NTag, NSpace, NInput, NInputNumber, NSelect, NDatePicker, useMessage, NModal, NForm, NFormItem, NDivider } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { amountSignForDirection, generateVoucherLines, normalizeVoucherLines } from '@/utils/accounting'
import { formatMoney, formatDate } from '@/utils/format'
import { getCategoriesByDirection } from '@/utils/constants'
import { getEntryNaturePresentation } from '@/utils/entryNature'
import type { Entry } from '@/api/types'
import { getRdExpenseTypes, getRdProjects } from '@/api/rd'
import type { RdExpenseTypeOption, RdProject } from '@/api/types'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()

const entry = ref<Entry | null>(null)
const loading = ref(true)
const editing = ref(false)
const showDeleteConfirm = ref(false)
const deleting = ref(false)
const saving = ref(false)
const rdProjects = ref<RdProject[]>([])
const rdExpenseTypes = ref<RdExpenseTypeOption[]>([])

// Edit form
const editForm = ref({
  title: '',
  amount: 0,
  category: '',
  direction: '',
  payer: '',
  billingStatus: '',
  paymentDate: null as string | null,
})

const directionOptions = [
  { label: '收入', value: 'income' },
  { label: '支出', value: 'expense' },
  { label: '资本与往来', value: 'owner_transfer' },
]

const payerOptions = [
  { label: '公司账户', value: 'company_account' },
  { label: '股东个人', value: 'boss_personal' },
  { label: '员工垫付', value: 'employee_personal' },
]

const billingOptions = [
  { label: '有发票', value: 'has_invoice' },
  { label: '有收据', value: 'has_receipt' },
  { label: '无票据', value: 'no_document' },
]

const categoryOptions = computed(() =>
  getCategoriesByDirection(editForm.value.direction || entry.value?.direction || 'expense')
    .map(c => ({ label: c, value: c }))
)

const voucherLines = computed(() => {
  if (!entry.value) return []
  const storedLines = normalizeVoucherLines((entry.value as any).voucherLines)
  if (storedLines.length > 0) return storedLines
  return generateVoucherLines({
    type: entry.value.direction as 'income' | 'expense' | 'owner_transfer',
    amount: entry.value.amount,
    category: entry.value.category || '',
    payer: entry.value.payer || 'company_account',
  })
})

const totalVoucherDebit = computed(() =>
  voucherLines.value.filter((l) => l.side === 'debit').reduce((s, l) => s + l.amount, 0),
)

const totalVoucherCredit = computed(() =>
  voucherLines.value.filter((l) => l.side === 'credit').reduce((s, l) => s + l.amount, 0),
)
const rdVoucherLines = computed(() => voucherLines.value.filter(line => line.rdDetail))

function rdProjectLabel(projectId: string) {
  const project = rdProjects.value.find(item => item.id === projectId)
  return project ? `${project.code} · ${project.name}` : projectId
}

function rdExpenseTypeLabel(expenseType: string) {
  return rdExpenseTypes.value.find(item => item.value === expenseType)?.label || expenseType
}

async function loadRdReferences() {
  try {
    const [projects, types] = await Promise.all([getRdProjects(true), getRdExpenseTypes()])
    rdProjects.value = projects
    rdExpenseTypes.value = types.items
  } catch {
    // 凭证仍可查看，项目名称暂以 ID 展示。
  }
}

function directionConfig(dir: string, category = '') {
  const presentation = getEntryNaturePresentation(dir, category)
  return { label: presentation.detailLabel, color: presentation.color, background: presentation.background }
}

onMounted(async () => {
  await loadRdReferences()
  const id = route.params.id as string
  if (entryStore.activeEntries.length === 0) {
    await entryStore.fetchEntries()
  }
  let found = entryStore.activeEntries.find((e) => e.id === id)
  if (!found) {
    // 缓存可能过期，尝试重新从后端获取最新记录
    await entryStore.fetchEntries()
    found = entryStore.activeEntries.find((e) => e.id === id)
  }
  if (!found) {
    message.error('记录不存在')
    router.push('/ledger')
    return
  }
  entry.value = found
  editForm.value = {
    title: found.title || '',
    amount: found.amount,
    category: found.category || '',
    direction: found.direction,
    payer: found.payer || 'company_account',
    billingStatus: found.billingStatus || '',
    paymentDate: found.paymentDate || null,
  }
  loading.value = false
})

function startEdit() {
  if (!entry.value) return
  editForm.value = {
    title: entry.value.title || '',
    amount: entry.value.amount,
    category: entry.value.category || '',
    direction: entry.value.direction,
    payer: entry.value.payer || 'company_account',
    billingStatus: entry.value.billingStatus || '',
    paymentDate: entry.value.paymentDate || null,
  }
  editing.value = true
}

async function saveEdit() {
  if (!entry.value) return
  saving.value = true
  try {
    const updated = await entryStore.updateEntryField(entry.value.id, {
      title: editForm.value.title,
      amount: editForm.value.amount,
      direction: editForm.value.direction,
      category: editForm.value.category,
      billingStatus: editForm.value.billingStatus,
      payer: editForm.value.payer,
      paymentDate: editForm.value.paymentDate || null,
    })
    entry.value = updated || entryStore.activeEntries.find((e) => e.id === entry.value?.id) || {
      ...entry.value,
      title: editForm.value.title,
      amount: editForm.value.amount,
      category: editForm.value.category,
      direction: editForm.value.direction,
      payer: editForm.value.payer,
      billingStatus: editForm.value.billingStatus,
      paymentDate: editForm.value.paymentDate || undefined,
    }
    editing.value = false
    message.success('已保存')
  } catch {
    message.error('保存失败')
  } finally {
    saving.value = false
  }
}

async function deleteEntry() {
  if (!entry.value) return
  deleting.value = true
  try {
    await entryStore.removeEntry(entry.value.id)
    message.success('已删除')
    router.push('/ledger')
  } catch {
    message.error('删除失败')
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <div class="detail-page" v-if="!loading && entry">
    <div class="page-header">
      <div class="header-left">
        <NButton text class="back-btn" @click="router.push('/ledger')">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回
        </NButton>
        <div class="title-section">
          <h2>交易明细</h2>
          <p class="subtitle">查看这笔交易的明细流水、凭证生成状态及附件信息</p>
        </div>
      </div>
      <div class="header-right">
        <NSpace>
          <NButton v-if="!editing" size="small" @click="startEdit">编辑</NButton>
          <NButton v-if="!editing" size="small" type="error" ghost @click="showDeleteConfirm = true">删除</NButton>
        </NSpace>
      </div>
    </div>

    <!-- Main info -->
    <NCard :bordered="false" class="detail-card">
      <div class="amount-row">
        <span class="direction-badge" :style="{ background: directionConfig(entry.direction, entry.category).background, color: directionConfig(entry.direction, entry.category).color }">
          {{ directionConfig(entry.direction, entry.category).label }}
        </span>
        <span class="amount" :style="{ color: directionConfig(entry.direction, entry.category).color }">
          {{ amountSignForDirection(entry.direction, entry.category, entry.amount) }}{{ formatMoney(entry.amount) }}
        </span>
      </div>

      <NDivider style="margin: 12px 0;" />

      <template v-if="!editing">
        <div class="info-grid">
          <div class="info-item">
            <span class="label">摘要</span>
            <span class="value">{{ entry.title || '—' }}</span>
          </div>
          <div class="info-item">
            <span class="label">分类</span>
            <span class="value">
              <NTag size="small" :bordered="false">{{ entry.category }}</NTag>
            </span>
          </div>
          <div class="info-item">
            <span class="label">付款方式</span>
            <span class="value">{{ (entry.payer || '').startsWith('employee_personal') ? '员工垫付' : entry.payer === 'boss_personal' ? '股东个人' : '公司账户' }}</span>
          </div>
          <div class="info-item">
            <span class="label">业务/票据日期</span>
            <span class="value">{{ formatDate(entry.occurDate || entry.created_at || '') }}</span>
          </div>
          <div class="info-item">
            <span class="label">付款日</span>
            <span class="value">{{ entry.paymentDate ? formatDate(entry.paymentDate) : '未记录' }}</span>
          </div>
          <div class="info-item" v-if="entry.merchant">
            <span class="label">商家</span>
            <span class="value">{{ entry.merchant }}</span>
          </div>
          <div class="info-item" v-if="entry.billingStatus">
            <span class="label">票据状态</span>
            <span class="value">
              <NTag size="small" :type="entry.billingStatus === 'no_document' ? 'warning' : 'success'" :bordered="false">
                {{ { has_invoice: '有发票', has_receipt: '有收据', no_document: '无票据' }[entry.billingStatus] || entry.billingStatus }}
              </NTag>
            </span>
          </div>
          <div class="info-item" v-if="entry.riskTags?.length">
            <span class="label">风险标签</span>
            <NSpace size="small">
              <NTag v-for="tag in entry.riskTags" :key="tag" size="tiny" type="warning" :bordered="false">{{ tag }}</NTag>
            </NSpace>
          </div>
        </div>
      </template>

      <!-- Edit mode -->
      <template v-else>
        <div class="edit-section">
          <NForm>
            <NFormItem label="摘要">
              <NInput v-model:value="editForm.title" />
            </NFormItem>
            <NFormItem label="金额">
              <NInputNumber
                v-model:value="editForm.amount"
                :step="0.01"
                :precision="2"
                :show-button="false"
                style="width: 100%;"
              />
            </NFormItem>
            <NFormItem label="方向">
              <NSelect v-model:value="editForm.direction" :options="directionOptions" />
            </NFormItem>
            <NFormItem label="分类">
              <NSelect v-model:value="editForm.category" :options="categoryOptions" filterable />
            </NFormItem>
            <NFormItem label="付款方式">
              <NSelect v-model:value="editForm.payer" :options="payerOptions" />
            </NFormItem>
            <NFormItem label="付款日">
              <NDatePicker
                v-model:formatted-value="editForm.paymentDate"
                value-format="yyyy-MM-dd"
                type="date"
                clearable
                style="width: 100%;"
              />
            </NFormItem>
            <NFormItem label="票据状态">
              <NSelect v-model:value="editForm.billingStatus" :options="billingOptions" clearable />
            </NFormItem>
          </NForm>
          <NSpace justify="end">
            <NButton @click="editing = false">取消</NButton>
            <NButton type="primary" :loading="saving" @click="saveEdit">保存</NButton>
          </NSpace>
        </div>
      </template>
    </NCard>

    <NCard v-if="rdVoucherLines.length" :bordered="false" class="detail-card mt-4">
      <div class="voucher-header"><h3>研发归集信息</h3></div>
      <div v-for="line in rdVoucherLines" :key="`${line.code}-${line.rdDetail?.projectId}`" class="rd-detail-row">
        <div class="info-item"><span class="label">研发项目</span><span class="value">{{ rdProjectLabel(line.rdDetail!.projectId) }}</span></div>
        <div class="info-item"><span class="label">费用类别</span><span class="value">{{ rdExpenseTypeLabel(line.rdDetail!.expenseType) }}（{{ line.code }}）</span></div>
        <div class="info-item"><span class="label">研发金额</span><span class="value">{{ formatMoney(line.rdDetail!.rdAmount ?? line.amount) }} / 原金额 {{ formatMoney(line.rdDetail!.originalAmount) }}</span></div>
        <div class="info-item"><span class="label">分摊方式</span><span class="value">{{ line.rdDetail!.allocationMethod }}<template v-if="line.rdDetail!.allocationRate"> · {{ Number(line.rdDetail!.allocationRate) * 100 }}%</template></span></div>
        <div class="info-item"><span class="label">分摊依据</span><span class="value">{{ line.rdDetail!.allocationBasis || '直接归集' }}</span></div>
        <div v-if="line.rdDetail!.notes" class="info-item"><span class="label">说明</span><span class="value">{{ line.rdDetail!.notes }}</span></div>
      </div>
    </NCard>

    <!-- Voucher Preview -->
    <NCard :bordered="false" class="voucher-card mt-4" v-if="voucherLines.length > 0">
      <div class="voucher-header">
        <h3>记账凭证（预览）</h3>
      </div>
      <table class="voucher-table">
        <thead>
          <tr>
            <th class="col-code">科目编码</th>
            <th class="col-name">科目名称</th>
            <th class="col-side">方向</th>
            <th class="col-amount">金额</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="line in voucherLines" :key="line.code + line.side">
            <td>{{ line.code }}</td>
            <td>{{ line.name }}</td>
            <td>
              <NTag size="tiny" :type="line.side === 'debit' ? 'error' : 'success'" :bordered="false">
                {{ line.side === 'debit' ? '借方' : '贷方' }}
              </NTag>
            </td>
            <td class="amount-cell">{{ formatMoney(line.amount) }}</td>
          </tr>
        </tbody>
        <tfoot>
          <tr>
            <td colspan="3" class="total-label">合计</td>
            <td class="amount-cell">{{ formatMoney(Math.max(totalVoucherDebit, totalVoucherCredit)) }}</td>
          </tr>
        </tfoot>
      </table>
      <div class="voucher-footer" v-if="entry.voucherRef">
        凭证号：{{ entry.voucherRef }}
      </div>
    </NCard>

    <!-- Delete Confirm -->
    <NModal v-model:show="showDeleteConfirm" title="确认删除" preset="card" style="width: 400px;">
      <p>确定要删除这条记账记录吗？此操作不可撤销。</p>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showDeleteConfirm = false">取消</NButton>
          <NButton type="error" :loading="deleting" @click="deleteEntry">确认删除</NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.detail-page {
  max-width: 720px;
  margin: 0 auto;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 16px;
}
.header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.header-left h2 {
  margin: 0;
  font-size: 18px;
  font-weight: 600;
}
.mt-4 {
  margin-top: 16px;
}
.detail-card {
  border-radius: var(--solo-radius-lg);
}
.amount-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.direction-badge {
  font-size: 12px;
  font-weight: 600;
  padding: 3px 10px;
  border-radius: 999px;
}
.amount {
  font-size: 28px;
  font-weight: 700;
}
.info-grid {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.info-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.info-item .label {
  font-size: 12px;
  color: var(--solo-text-muted);
  width: 80px;
  flex-shrink: 0;
}
.info-item .value {
  font-size: 14px;
  color: var(--solo-text-primary);
}
.edit-section {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.rd-detail-row + .rd-detail-row {
  border-top: 1px solid var(--solo-border-light);
  margin-top: 12px;
  padding-top: 12px;
}

/* Voucher */
.voucher-card {
  border-radius: var(--solo-radius-lg);
}
.voucher-header h3 {
  margin: 0 0 12px;
  font-size: 15px;
  font-weight: 600;
}
.voucher-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.voucher-table th,
.voucher-table td {
  padding: 8px 12px;
  text-align: left;
  border-bottom: 1px solid var(--solo-border-light);
}
.voucher-table th {
  background: var(--solo-bg-page);
  font-weight: 600;
  color: var(--solo-text-secondary);
  font-size: 12px;
}
.col-code { width: 20%; }
.col-name { width: 35%; }
.col-side { width: 15%; }
.col-amount { width: 30%; text-align: right; }
.amount-cell {
  text-align: right;
  font-weight: 600;
  font-family: 'Inter', monospace;
}
.total-label {
  text-align: right;
  font-weight: 600;
  color: var(--solo-text-secondary);
}
.voucher-footer {
  margin-top: 8px;
  font-size: 12px;
  color: var(--solo-text-muted);
}
</style>
