<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NCard, NButton, NTag, NSpace, NInput, NInputNumber, NSelect, NDatePicker, useMessage, NModal, NForm, NFormItem, NDivider } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { moneyPresentation, generateVoucherLines, normalizeVoucherLines } from '@/utils/accounting'
import { formatMoney, formatDate } from '@/utils/format'
import { getCategoriesByDirection } from '@/utils/constants'
import { getEntryNaturePresentation } from '@/utils/entryNature'
import { payerDisplayLabel } from '@/utils/fundsChannel'
import type { Entry } from '@/api/types'
import { getRdExpenseTypes, getRdProjects } from '@/api/rd'
import type { RdExpenseTypeOption, RdProject } from '@/api/types'
import { approveEntry, getPostingOptions, postEntry, reverseEntry, submitEntryForReview, type PostingOptions } from '@/api/accounting'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()

const entry = ref<Entry | null>(null)
const loading = ref(true)
// 待办"报销入账"直达：?edit=1&focus=payer 自动进入编辑态
const editing = ref(String(route.query.edit || '') === '1')
const showDeleteConfirm = ref(false)
const deleting = ref(false)
const showReverseConfirm = ref(false)
const reversing = ref(false)
const reverseForm = ref({ reason: '', reversalDate: null as number | null })
const showPostConfirm = ref(false)
const posting = ref(false)
const postingOptions = ref<PostingOptions | null>(null)
const isPendingReview = computed(() => voucherStatus.value === 'pending_review')
const needsSubmitReview = computed(() => !!postingOptions.value?.canSubmit && !postingOptions.value.canPost)
const postingLabel = computed(() => needsSubmitReview.value ? '提交复核' : isPendingReview.value ? '复核并过账' : '确认过账')

const returnTarget = computed(() => {
  const from = route.query.from
  if (typeof from === 'string' && from.startsWith('/')) return from
  return ''
})

const backLabel = computed(() => {
  if (returnTarget.value.includes('closing-wizard')) return '返回结账向导'
  if (returnTarget.value.includes('vouchers')) return '返回凭证管理'
  return '返回'
})

function handleBack() {
  if (returnTarget.value) {
    router.push(returnTarget.value)
    return
  }
  if (window.history.length > 1) {
    router.back()
    return
  }
  router.push('/ledger')
}
async function confirmPost() {
  if (!entry.value || posting.value) return
  posting.value = true
  try {
    const submitting = needsSubmitReview.value
    const result = await (submitting ? submitEntryForReview(entry.value.id) : isPendingReview.value ? approveEntry(entry.value.id) : postEntry(entry.value.id))
    entry.value = result.entry
    showPostConfirm.value = false
    message.success(submitting ? '已提交复核，请由有权限的复核人处理' : `已过账，凭证号：${result.entry.voucherRef || ''}`)
    try {
      postingOptions.value = await getPostingOptions(entry.value.id)
      await entryStore.fetchEntries()
    } catch {
      postingOptions.value = null
      message.warning('处理已完成，刷新列表失败，请刷新页面查看最新状态')
    }
  } catch (err) {
    message.error(errDetail(err, '过账失败，请核对权限、凭证及期间状态'))
  } finally {
    posting.value = false
  }
}
// ponytail: default collapsed to avoid accounting terminology cognitive overload for solo business owners
const showVoucherDetails = ref(false)

const voucherStatus = computed(() => String(entry.value?.voucherStatus || ''))
const settlementOriginalIds = computed<string[]>(() => (entry.value as any)?.originalEntryIds || [])
const settlementBankId = computed<string>(() => (entry.value as any)?.linkedStatementId || '')
const isPosted = computed(() => voucherStatus.value === 'posted')
const isReversed = computed(() => voucherStatus.value === 'reversed')

function errDetail(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail === 'object') {
    const msg = (detail as { message?: unknown }).message
    if (typeof msg === 'string' && msg) return msg
  }
  return fallback
}
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
  { label: '已对账', value: 'reconciled' },
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
const voucherBalanced = computed(() =>
  voucherLines.value.length > 0
  && Math.abs(totalVoucherDebit.value - totalVoucherCredit.value) < 0.005,
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

// 金额展示统一走 moneyPresentation（冲退类负金额按资金流向显示 +¥x/-¥x）；
// 色调跟随展示口径：冲退时可能与账面方向徽标不同色（如手续费退还显示绿色 +¥0.07）
function moneyDisplay(direction: string, category: string, amount: number | string) {
  const presentation = moneyPresentation(direction, category, amount)
  const color = presentation.tone === 'neutral'
    ? directionConfig(direction, category).color
    : getEntryNaturePresentation(presentation.tone === 'income' ? 'income' : 'expense', '').color
  return { text: presentation.text, color }
}

async function loadEntry() {
  loading.value = true
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
    handleBack()
    return
  }
  entry.value = found
  try {
    postingOptions.value = await getPostingOptions(found.id)
  } catch {
    message.error('读取过账权限失败，请刷新重试')
  }
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
}
onMounted(loadEntry)
watch(() => route.params.id, loadEntry)

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
  } catch (err) {
    message.error(errDetail(err, '保存失败'))
  } finally {
    saving.value = false
  }
}

async function confirmReverse() {
  if (!entry.value) return
  if (!reverseForm.value.reason.trim()) {
    message.warning('请填写冲销原因')
    return
  }
  reversing.value = true
  try {
    const reversalDate = reverseForm.value.reversalDate
      ? new Date(reverseForm.value.reversalDate).toISOString().slice(0, 10)
      : undefined
    await reverseEntry(entry.value.id, reverseForm.value.reason.trim(), reversalDate)
    showReverseConfirm.value = false
    message.success('已生成冲销凭证，该笔不再计入余额')
    await entryStore.fetchEntries()
    entry.value = entryStore.activeEntries.find((e) => e.id === entry.value?.id) || entry.value
  } catch (err) {
    message.error(errDetail(err, '冲销失败'))
  } finally {
    reversing.value = false
  }
}

async function deleteEntry() {
  if (!entry.value) return
  deleting.value = true
  try {
    await entryStore.removeEntry(entry.value.id)
    message.success('已删除')
    handleBack()
  } catch (err) {
    message.error(errDetail(err, '删除失败'))
  } finally {
    deleting.value = false
  }
}
</script>

<template>
  <div class="detail-page" v-if="!loading && entry">
    <div class="page-header">
      <div class="header-left">
        <NButton text class="back-btn" @click="handleBack">
          <template #icon><i class="ri-arrow-left-line" /></template>
          {{ backLabel }}
        </NButton>
        <div class="title-section">
          <h2>交易明细</h2>
          <p class="subtitle">查看这笔交易的明细流水、凭证生成状态及附件信息</p>
        </div>
      </div>
      <div class="header-right">
        <NSpace>
          <template v-if="!editing">
            <NButton v-if="returnTarget" size="small" secondary @click="handleBack">
              {{ backLabel }}
            </NButton>
            <NTag v-if="isReversed" size="small" :bordered="false" round>已冲销</NTag>
            <template v-else-if="isPosted">
              <NTag size="small" type="info" :bordered="false" round>已过账</NTag>
              <NButton size="small" type="warning" ghost @click="showReverseConfirm = true">冲销</NButton>
            </template>
            <template v-else>
              <NTag size="small" type="warning">{{ isPendingReview ? '待复核' : '待过账' }}</NTag>
              <NButton v-if="postingOptions?.canPost || postingOptions?.canApprove || postingOptions?.canSubmit" size="small" type="primary" @click="showPostConfirm = true">
                {{ postingLabel }}
              </NButton>
              <NButton v-if="!settlementBankId" size="small" @click="startEdit">编辑</NButton>
              <NButton v-if="!settlementBankId" size="small" type="error" ghost @click="showDeleteConfirm = true">删除</NButton>
            </template>
          </template>
        </NSpace>
      </div>
    </div>

    <!-- Main info -->
    <NCard :bordered="false" class="detail-card">
      <div class="amount-row">
        <span class="direction-badge" :style="{ background: directionConfig(entry.direction, entry.category).background, color: directionConfig(entry.direction, entry.category).color }">
          {{ directionConfig(entry.direction, entry.category).label }}
        </span>
        <span class="amount" :style="{ color: moneyDisplay(entry.direction, entry.category, entry.amount).color }">
          {{ moneyDisplay(entry.direction, entry.category, entry.amount).text }}
        </span>
      </div>

      <NDivider style="margin: 12px 0;" />

      <template v-if="!editing">
        <div class="info-grid">
          <div class="info-item span-2">
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
            <span class="value">{{ payerDisplayLabel(entry.payer, (entry as any).voucherLines) }}</span>
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
                {{ { has_invoice: '有发票', has_receipt: '有收据', no_document: '无票据', reconciled: '已对账' }[entry.billingStatus] || entry.billingStatus }}
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

      <div v-if="settlementBankId && !editing" class="info-grid" style="margin-top: 16px;">
        <div class="info-item span-2">
          <span class="label">结算来源</span>
          <span class="value">银行净额到账；原收款与手续费合并结算</span>
        </div>
        <div class="info-item">
          <span class="label">银行流水</span>
          <span class="value">{{ settlementBankId }}</span>
        </div>
        <div class="info-item" v-for="id in settlementOriginalIds" :key="id">
          <span class="label">原收款 / 手续费</span>
          <NButton text type="primary" @click="router.push(`/ledger/${id}`)">{{ entryStore.activeEntries.find(item => item.id === id)?.title || id }}</NButton>
        </div>
      </div>

      <!-- Edit mode -->
      <template v-else>
        <div class="edit-section">
          <NForm class="edit-grid">
            <NFormItem label="摘要" class="span-2">
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
              <NSelect
                v-model:value="editForm.payer"
                :options="payerOptions"
                :autofocus="String(route.query.focus || '') === 'payer'"
                :default-open="String(route.query.focus || '') === 'payer'"
              />
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

    <!-- Voucher Preview: 可折叠会计底账分录 -->
    <NCard :bordered="false" class="voucher-card mt-4" v-if="voucherLines.length > 0">
      <div class="voucher-toggle-header" @click="showVoucherDetails = !showVoucherDetails">
        <div class="toggle-title-group">
          <i class="ri-file-list-3-line" />
          <span class="toggle-heading">会计凭证分录（合规底账）</span>
          <span class="toggle-badge">{{ showVoucherDetails ? '点击收起' : '点击展开查看' }}</span>
        </div>
        <i :class="showVoucherDetails ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'" class="toggle-chevron" />
      </div>

      <div v-show="showVoucherDetails" class="voucher-table-wrapper">
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

    <!-- Post Confirm -->
    <NModal v-model:show="showPostConfirm" :title="postingLabel" preset="card" style="width: 560px;">
      <div v-if="entry" class="post-id-row">
        <span
          class="direction-badge"
          :style="{ background: directionConfig(entry.direction, entry.category).background, color: directionConfig(entry.direction, entry.category).color }"
        >
          {{ directionConfig(entry.direction, entry.category).label }}
        </span>
        <div class="post-id-main">
          <p class="post-id-title">{{ entry.title || '—' }}</p>
          <p class="post-id-meta">{{ formatDate(entry.occurDate || entry.created_at || '') }} · {{ entry.category }}</p>
        </div>
        <span
          class="post-id-amount"
          :style="{ color: moneyDisplay(entry.direction, entry.category, entry.amount).color }"
        >
          {{ moneyDisplay(entry.direction, entry.category, entry.amount).text }}
        </span>
      </div>

      <div class="post-lines">
        <div v-for="side in (['debit', 'credit'] as const)" :key="side" class="post-line-card" :class="side">
          <p class="post-line-side">{{ side === 'debit' ? '借方 DR' : '贷方 CR' }}</p>
          <template v-for="(line, index) in voucherLines.filter(l => l.side === side)" :key="index">
            <p class="post-line-name"><b>{{ line.code }}</b> {{ line.name }}</p>
            <p class="post-line-amount">{{ formatMoney(line.amount) }}</p>
          </template>
        </div>
      </div>
      <p class="post-balance" :class="voucherBalanced ? 'ok' : 'bad'">
        <i :class="voucherBalanced ? 'ri-checkbox-circle-fill' : 'ri-error-warning-fill'" />
        {{ voucherBalanced ? '借贷平衡' : `借贷不平衡：借 ${formatMoney(totalVoucherDebit)} / 贷 ${formatMoney(totalVoucherCredit)}` }}
      </p>

      <div class="post-notes">
        <p class="post-note">
          <i class="ri-information-line" />
          {{ needsSubmitReview ? '提交后等待复核人审批。' : '过账后将生成正式凭证号，修改需通过冲销处理。' }}
        </p>
      </div>

      <template #footer>
        <NSpace justify="end">
          <NButton :disabled="posting" @click="showPostConfirm = false">取消</NButton>
          <NButton type="primary" :loading="posting" @click="confirmPost">
            {{ postingLabel }}{{ entry ? ` ${formatMoney(entry.amount)}` : '' }}
          </NButton>
        </NSpace>
      </template>
    </NModal>

    <NModal v-model:show="showReverseConfirm" title="冲销凭证" preset="card" style="width: 440px;">
      <p style="margin: 0 0 12px;">
        已过账凭证不能直接删除。冲销会生成一笔方向相反的红字凭证抵消本笔影响，
        两张凭证均保留留档（符合会计规范）。
      </p>
      <NForm>
        <NFormItem label="冲销原因（必填）">
          <NInput
            v-model:value="reverseForm.reason"
            type="textarea"
            :rows="2"
            placeholder="例如：测试数据清理 / 记账错误，重新录入"
          />
        </NFormItem>
        <NFormItem label="冲销日期">
          <NDatePicker
            v-model:value="reverseForm.reversalDate"
            type="date"
            clearable
            style="width: 100%;"
          />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showReverseConfirm = false">取消</NButton>
          <NButton type="warning" :loading="reversing" @click="confirmReverse">确认冲销</NButton>
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
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 24px;
}
.info-item {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  min-width: 0;
}
.info-item.span-2 {
  grid-column: 1 / -1;
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
  min-width: 0;
  word-break: break-all;
}
.edit-section {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.edit-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0 20px;
}
.edit-grid .span-2 {
  grid-column: 1 / -1;
}
@media (max-width: 640px) {
  .info-grid,
  .edit-grid {
    grid-template-columns: 1fr;
  }
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
.voucher-toggle-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  cursor: pointer;
  user-select: none;
  padding: 4px 0;
  transition: opacity 0.2s;
}
.voucher-toggle-header:hover {
  opacity: 0.85;
}
.toggle-title-group {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--solo-text-primary);
  font-size: 15px;
  font-weight: 600;
}
.toggle-heading {
  font-size: 14px;
}
.toggle-badge {
  font-size: 11px;
  color: var(--solo-primary);
  background: var(--solo-bg-blue-soft);
  padding: 2px 8px;
  border-radius: 10px;
  font-weight: 500;
}
.toggle-chevron {
  font-size: 18px;
  color: var(--solo-text-muted);
}
.voucher-table-wrapper {
  margin-top: 14px;
  border-top: 1px solid var(--solo-border-light);
  padding-top: 12px;
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

/* 确认过账弹窗 */
.post-id-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 12px 14px;
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius, 10px);
  background: var(--solo-bg-soft, #f1f4f8);
}

.post-id-main {
  flex: 1;
  min-width: 0;
}

.post-id-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.post-id-meta {
  margin: 3px 0 0;
  font-size: 12px;
  color: var(--solo-text-muted);
}

.post-id-amount {
  font-size: 20px;
  font-weight: 750;
  white-space: nowrap;
}

.post-lines {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 10px;
  margin-top: 14px;
}

.post-line-card {
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius, 10px);
  padding: 10px 12px;
  min-width: 0;
}

.post-line-card.debit {
  border-top: 3px solid var(--solo-expense, #c74444);
}

.post-line-card.credit {
  border-top: 3px solid var(--solo-income, #12805c);
}

.post-line-side {
  margin: 0 0 6px;
  font-size: 11px;
  letter-spacing: 0.5px;
  color: var(--solo-text-muted);
}

.post-line-name {
  margin: 0 0 2px;
  font-size: 13px;
  color: var(--solo-text-primary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.post-line-name b {
  font-weight: 600;
  margin-right: 4px;
}

.post-line-amount {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--solo-text-primary);
}

.post-balance {
  display: flex;
  align-items: center;
  gap: 5px;
  justify-content: center;
  margin: 12px 0 0;
  font-size: 12px;
}

.post-balance.ok {
  color: var(--solo-income, #12805c);
}

.post-balance.bad {
  color: var(--solo-expense, #c74444);
  font-weight: 600;
}

.post-notes {
  margin-top: 14px;
}

.post-note {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  font-size: 12px;
  color: var(--solo-text-muted);
}

.post-note i {
  color: var(--solo-info, #165dff);
}

@media (max-width: 640px) {
  .post-lines {
    grid-template-columns: 1fr;
  }
}
</style>
