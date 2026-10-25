<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton, NTag, NSpace, NSpin, useMessage, NModal, NTabs, NTabPane, NDescriptions,
  NDescriptionsItem, NDataTable, NForm, NFormItem, NInput, NInputNumber, NSelect, NDatePicker, } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import {
  approveFixedAsset, approveFixedAssetDisposal, cancelFixedAssetDisposal,
  createFixedAssetDisposal, deleteFixedAsset, getFixedAsset, getFixedAssetDisposals,
  getFixedAssetSchedule, rejectFixedAsset, submitFixedAsset, submitFixedAssetDisposal,
} from '@/api/asset'
import { formatMoney, formatDate } from '@/utils/format'
import type { AmortizationScheduleRow, FixedAsset, FixedAssetDisposal } from '@/api/types'
import { SoloEmpty } from '@/components/ui'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const assetId = computed(() => String(route.params.id || ''))
const asset = ref<FixedAsset | null>(null)
const schedule = ref<AmortizationScheduleRow[]>([])
const disposals = ref<FixedAssetDisposal[]>([])
const loading = ref(true)
const acting = ref(false)

const showReject = ref(false)
const rejectReason = ref('')
const showDisposal = ref(false)
const disposalSubmitting = ref(false)
const disposalForm = ref({
  disposalType: 'sale' as 'sale' | 'scrap' | 'donate' | 'transfer_out',
  // formatted-value 的「无值」哨兵是 null，空字符串会被当作待解析日期导致弹窗崩溃
  disposalDate: null as string | null,
  proceedsAmount: 0,
  outputVatAmount: 0,
  reason: '',
})

const statusConfig: Record<string, { label: string; type: 'success' | 'warning' | 'info' | 'error' | 'default' }> = {
  draft: { label: '草稿', type: 'default' },
  pending_review: { label: '待复核', type: 'warning' },
  active: { label: '使用中', type: 'success' },
  idle: { label: '已停用', type: 'info' },
  fully_depreciated: { label: '已折旧完毕', type: 'warning' },
  disposing: { label: '处置中', type: 'warning' },
  disposed: { label: '已处置', type: 'info' },
  scrapped: { label: '已报废', type: 'error' },
}

const disposalTypeLabels: Record<string, string> = {
  sale: '出售',
  scrap: '报废清理',
  donate: '对外捐赠',
  transfer_out: '对外投资转出',
}

const disposalTypeOptions = Object.entries(disposalTypeLabels).map(([value, label]) => ({ value, label }))

const acquisitionTypeLabels: Record<string, string> = {
  purchase: '外购取得',
  self_built: '自建',
  investor_contribution: '投资者投入',
  donation: '接受捐赠',
}

const methodLabels: Record<string, string> = {
  straight_line: '年限平均法',
  one_time: '一次性折旧',
}

const residualRateText = computed(() => {
  const original = Number(asset.value?.originalValue || 0)
  const residual = Number(asset.value?.residualValue || 0)
  if (original <= 0) return '0.00%'
  return `${(residual / original * 100).toFixed(2)}%`
})

const scheduleColumns: DataTableColumns<AmortizationScheduleRow> = [
  { title: '期间', key: 'periodStart', width: 90 },
  { title: '期数', key: 'sequenceNo', width: 70 },
  { title: '折旧额（元）', key: 'amount', align: 'right', render: (row) => formatMoney(row.amount) },
  { title: '计提前累计', key: 'accumulatedBefore', align: 'right', render: (row) => formatMoney(row.accumulatedBefore) },
  { title: '计提后累计', key: 'accumulatedAfter', align: 'right', render: (row) => formatMoney(row.accumulatedAfter) },
  {
    title: '状态',
    key: 'status',
    width: 90,
    render: (row) => {
      const map: Record<string, { label: string; type: 'success' | 'default' | 'warning' | 'info' }> = {
        posted: { label: '已过账', type: 'success' },
        planned: { label: '计划', type: 'default' },
        skipped: { label: '已跳过', type: 'warning' },
        reversed: { label: '已冲销', type: 'info' },
      }
      const cfg = map[row.status] ?? { label: row.status, type: 'default' as const }
      return h(NTag, { size: 'tiny', bordered: false, type: cfg.type }, { default: () => cfg.label })
    },
  },
  {
    title: '凭证',
    key: 'journalEntryId',
    render: (row) =>
      row.journalEntryId
        ? h(
            'a',
            {
              style: 'color: var(--solo-primary); cursor: pointer;',
              onClick: () => router.push(`/vouchers/${row.journalEntryId}`),
            },
            { default: () => '查看凭证' }
          )
        : '—',
  },
]

const disposalColumns: DataTableColumns<FixedAssetDisposal> = [
  { title: '类型', key: 'disposalType', width: 100, render: (row) => disposalTypeLabels[row.disposalType] ?? row.disposalType },
  { title: '日期', key: 'disposalDate', width: 100, render: (row) => formatDate(row.disposalDate) },
  { title: '价款（元）', key: 'proceedsAmount', align: 'right', render: (row) => formatMoney(row.proceedsAmount) },
  { title: '账面净值（元）', key: 'netBookValueSnapshot', align: 'right', render: (row) => formatMoney(row.netBookValueSnapshot) },
  {
    title: '处置损益（元）',
    key: 'gainLossAmount',
    align: 'right',
    render: (row) =>
      h(
        'span',
        { style: { color: row.gainLossAmount >= 0 ? 'var(--solo-income)' : 'var(--solo-risk)' } },
        { default: () => formatMoney(row.gainLossAmount) }
      ),
  },
  {
    title: '状态',
    key: 'status',
    width: 90,
    render: (row) => {
      const map: Record<string, { label: string; type: 'success' | 'default' | 'warning' | 'info' | 'error' }> = {
        draft: { label: '草稿', type: 'default' },
        pending_review: { label: '待复核', type: 'warning' },
        approved: { label: '已审批', type: 'info' },
        posted: { label: '已过账', type: 'success' },
        rejected: { label: '已驳回', type: 'error' },
        cancelled: { label: '已取消', type: 'default' },
      }
      const cfg = map[row.status] ?? { label: row.status, type: 'default' as const }
      return h(NTag, { size: 'tiny', bordered: false, type: cfg.type }, { default: () => cfg.label })
    },
  },
  {
    title: '操作',
    key: 'actions',
    render: (row) => {
      const actions: ReturnType<typeof h>[] = []
      if (row.status === 'draft') {
        actions.push(
          h(NButton, { text: true, size: 'tiny', type: 'primary', onClick: () => handleDisposalAction('submit', row) }, { default: () => '提交' })
        )
      }
      if (row.status === 'pending_review') {
        actions.push(
          h(NButton, { text: true, size: 'tiny', type: 'primary', onClick: () => handleDisposalAction('approve', row) }, { default: () => '复核' })
        )
      }
      if (['draft', 'pending_review'].includes(row.status)) {
        actions.push(
          h(NButton, { text: true, size: 'tiny', type: 'error', onClick: () => handleDisposalAction('cancel', row) }, { default: () => '取消' })
        )
      }
      return actions.length ? h(NSpace, { size: 4 }, { default: () => actions }) : '—'
    },
  },
]

async function load() {
  loading.value = true
  try {
    asset.value = await getFixedAsset(assetId.value)
    schedule.value = (await getFixedAssetSchedule(assetId.value)).items ?? []
    disposals.value = (await getFixedAssetDisposals({ assetId: assetId.value })).items ?? []
  } catch {
    message.error('加载资产详情失败')
  } finally {
    loading.value = false
  }
}

async function withLoading(fn: () => Promise<unknown>, success?: string) {
  acting.value = true
  try {
    await fn()
    if (success) message.success(success)
    await load()
  } catch {
    message.error('操作失败')
  } finally {
    acting.value = false
  }
}

function handleSubmit() {
  withLoading(() => submitFixedAsset(assetId.value))
}

function handleApprove() {
  withLoading(() => approveFixedAsset(assetId.value), '已复核过账')
}

async function handleReject() {
  if (!rejectReason.value.trim()) {
    message.warning('请填写驳回原因')
    return
  }
  await withLoading(() => rejectFixedAsset(assetId.value, rejectReason.value), '已驳回')
  showReject.value = false
  rejectReason.value = ''
}

function handleDelete() {
  withLoading(
    () => deleteFixedAsset(assetId.value),
    '已删除'
  ).then(() => router.push('/assets'))
}

function openDisposal() {
  const today = new Date()
  disposalForm.value.disposalDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
  disposalForm.value.proceedsAmount = 0
  disposalForm.value.outputVatAmount = 0
  disposalForm.value.reason = ''
  showDisposal.value = true
}

async function handleCreateDisposal() {
  if (!disposalForm.value.disposalDate) {
    message.warning('请选择处置日期')
    return
  }
  if (disposalForm.value.disposalType === 'sale' && (disposalForm.value.proceedsAmount || 0) <= 0) {
    message.warning('出售必须填写处置价款')
    return
  }
  disposalSubmitting.value = true
  try {
    await createFixedAssetDisposal({
      assetId: assetId.value,
      ...disposalForm.value,
      disposalDate: disposalForm.value.disposalDate,
    })
    message.success('处置单已创建，提交复核后生效；处置当月不再计提折旧')
    showDisposal.value = false
    await load()
  } catch {
    message.error('创建处置单失败')
  } finally {
    disposalSubmitting.value = false
  }
}

function handleDisposalAction(action: 'submit' | 'approve' | 'cancel', row: FixedAssetDisposal) {
  const fn = action === 'submit' ? submitFixedAssetDisposal : action === 'approve' ? approveFixedAssetDisposal : cancelFixedAssetDisposal
  withLoading(() => fn(row.id), '操作成功')
}

onMounted(load)
</script>

<template>
  <div class="detail-page">
    <div class="page-header">
      <div class="header-left">
        <NButton text class="back-btn" @click="router.push('/assets')">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回台账
        </NButton>
        <div class="title-section" v-if="asset">
          <h2>{{ asset.name }}</h2>
          <p class="subtitle">{{ asset.code }} · {{ asset.categoryName }}
            <NTag size="tiny" :bordered="false" :type="statusConfig[asset.status]?.type || 'default'" round style="margin-left: 8px;">
              {{ statusConfig[asset.status]?.label || asset.status }}
            </NTag>
          </p>
        </div>
      </div>
      <NSpace v-if="asset">
        <template v-if="asset.status === 'draft'">
          <NButton type="error" secondary :loading="acting" @click="handleDelete">删除草稿</NButton>
          <NButton type="primary" :loading="acting" @click="handleSubmit">提交审核</NButton>
        </template>
        <template v-else-if="asset.status === 'pending_review'">
          <NButton secondary @click="showReject = true">驳回</NButton>
          <NButton type="primary" :loading="acting" @click="handleApprove">复核过账</NButton>
        </template>
        <template v-else-if="['active', 'idle', 'fully_depreciated'].includes(asset.status)">
          <NButton type="warning" @click="openDisposal">处置 / 报废</NButton>
        </template>
      </NSpace>
    </div>

    <NSpin :show="loading">
      <NTabs type="line" animated>
        <NTabPane name="overview" tab="概览">
          <div class="report-card" v-if="asset">
            <NDescriptions :column="3" label-placement="top" bordered>
              <NDescriptionsItem label="资产原值">{{ formatMoney(asset.originalValue) }}</NDescriptionsItem>
              <NDescriptionsItem label="累计折旧">{{ formatMoney(asset.accumulatedDepreciation) }}</NDescriptionsItem>
              <NDescriptionsItem label="账面净值">
                <span style="color: var(--solo-income); font-weight: 700;">{{ formatMoney(asset.netValue) }}</span>
              </NDescriptionsItem>
              <NDescriptionsItem label="取得方式">{{ acquisitionTypeLabels[asset.acquisitionType] ?? asset.acquisitionType }}</NDescriptionsItem>
              <NDescriptionsItem label="取得日期">{{ formatDate(asset.acquisitionDate) }}</NDescriptionsItem>
              <NDescriptionsItem label="可供使用日期（次月起提）">{{ formatDate(asset.availableForUseDate) }}</NDescriptionsItem>
              <NDescriptionsItem label="折旧年限">{{ asset.usefulLifeMonths }} 个月</NDescriptionsItem>
              <NDescriptionsItem label="折旧方法">{{ methodLabels[asset.depreciationMethod] ?? asset.depreciationMethod }}</NDescriptionsItem>
              <NDescriptionsItem label="月折旧额">{{ formatMoney(asset.monthlyDepreciation) }}</NDescriptionsItem>
              <NDescriptionsItem label="折旧起止">{{ asset.availableForUseDate.slice(0, 7) }} ~ {{ asset.depreciationEndPeriod ?? '—' }}</NDescriptionsItem>
              <NDescriptionsItem label="剩余月数">{{ asset.remainingMonths }}</NDescriptionsItem>
              <NDescriptionsItem label="预计净残值">{{ formatMoney(asset.residualValue) }}</NDescriptionsItem>
              <NDescriptionsItem label="预计净残值率">{{ residualRateText }}</NDescriptionsItem>
              <NDescriptionsItem label="存放地点">{{ asset.location || '—' }}</NDescriptionsItem>
              <NDescriptionsItem label="保管人">{{ asset.custodian || '—' }}</NDescriptionsItem>
              <NDescriptionsItem label="取得凭证">{{ asset.sourceEntryId ? '已生成' : '未生成' }}</NDescriptionsItem>
              <NDescriptionsItem v-if="asset.disposedOn" label="处置日期">{{ formatDate(asset.disposedOn) }}</NDescriptionsItem>
              <NDescriptionsItem v-if="asset.disposalType" label="处置方式">{{ disposalTypeLabels[asset.disposalType] ?? asset.disposalType }}</NDescriptionsItem>
            </NDescriptions>
          </div>
        </NTabPane>

        <NTabPane name="schedule" tab="折旧计划">
          <div class="report-card">
            <NDataTable :columns="scheduleColumns" :data="schedule" :bordered="false" size="small" :pagination="false" />
            <SoloEmpty
              v-if="schedule.length === 0"
              variant="hint"
              title="暂无折旧计划"
              description="提交审核后系统会自动生成全周期计划。"
              style="margin: 24px 0;"
            />
          </div>
        </NTabPane>

        <NTabPane name="vouchers" tab="凭证">
          <div class="report-card">
            <p class="section-tip">该资产的取得凭证、月度折旧凭证与处置凭证均可在记账凭证页检索（来源类型：fixed_asset_purchase / fixed_asset_depreciation / fixed_asset_disposal 等）。</p>
            <NButton text type="primary" @click="router.push('/vouchers')">前往凭证页</NButton>
          </div>
        </NTabPane>

        <NTabPane name="disposal" tab="处置">
          <div class="report-card">
            <div class="disposal-header">
              <h3 class="report-section-title"><i class="ri-delete-bin-line"></i> 处置记录</h3>
              <NButton size="small" type="primary" secondary :disabled="!['active', 'idle', 'fully_depreciated'].includes(asset?.status || '')" @click="openDisposal">
                发起处置
              </NButton>
            </div>
            <p class="section-tip">处置凭证经 <b>1606 固定资产清理</b>中转：借固定资产清理（净值）+ 累计折旧，贷固定资产；处置损益落营业外收支（5301/5711）。</p>
            <NDataTable :columns="disposalColumns" :data="disposals" :bordered="false" size="small" :pagination="false" />
            <SoloEmpty
              v-if="disposals.length === 0"
              variant="hint"
              title="暂无处置记录"
              description="资产卖出或报废时会在这里留痕。"
              style="margin: 24px 0;"
            />
          </div>
        </NTabPane>
      </NTabs>
    </NSpin>

    <!-- 驳回 -->
    <NModal v-model:show="showReject" title="驳回取得申请" preset="card" style="width: 440px; border-radius: var(--solo-radius-lg);">
      <NFormItem label="驳回原因（必填）">
        <NInput v-model:value="rejectReason" type="textarea" :rows="3" placeholder="例如：缺少购置发票，请补充" />
      </NFormItem>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showReject = false">取消</NButton>
          <NButton type="primary" :loading="acting" @click="handleReject">确认驳回</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 处置 -->
    <NModal v-model:show="showDisposal" title="处置 / 报废固定资产" preset="card" style="width: 480px; border-radius: var(--solo-radius-lg);">
      <NForm label-placement="top">
        <NFormItem label="处置方式">
          <NSelect v-model:value="disposalForm.disposalType" :options="disposalTypeOptions" />
        </NFormItem>
        <NFormItem label="处置日期（该月不再计提折旧）">
          <NDatePicker v-model:formatted-value="disposalForm.disposalDate" value-format="yyyy-MM-dd" type="date" style="width: 100%;" />
        </NFormItem>
        <NFormItem :label="disposalForm.disposalType === 'transfer_out' ? '投资作价（元，必填）' : '处置价款 / 残料收入（元，不含税）'">
          <NInputNumber v-model:value="disposalForm.proceedsAmount" :min="0" :step="1000" style="width: 100%;" />
        </NFormItem>
        <NFormItem label="销项税额（元，选填）">
          <NInputNumber v-model:value="disposalForm.outputVatAmount" :min="0" :step="100" style="width: 100%;" />
        </NFormItem>
        <NFormItem label="处置原因">
          <NInput v-model:value="disposalForm.reason" type="textarea" :rows="2" placeholder="选填" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showDisposal = false">取消</NButton>
          <NButton type="primary" :loading="disposalSubmitting" @click="handleCreateDisposal">创建处置单</NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.detail-page {
  max-width: 1240px;
  margin: 0 auto;
  padding: 16px 0 48px 0;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
}
.back-btn {
  font-weight: 500;
  color: var(--solo-text-secondary);
  margin-bottom: 8px;
}
.back-btn:hover { color: var(--solo-primary); }
.title-section h2 {
  margin: 4px 0 0 0;
  font-size: 24px;
  font-weight: 700;
  color: var(--solo-text-primary);
  letter-spacing: -0.5px;
}
.subtitle {
  margin: 6px 0 0 0;
  font-size: 13.5px;
  color: var(--solo-text-secondary);
  display: flex;
  align-items: center;
}
.report-card {
  background: var(--solo-bg-card);
  backdrop-filter: blur(12px);
  border: 1px solid var(--solo-border-glass);
  border-radius: var(--solo-radius-lg);
  box-shadow: var(--solo-shadow-sm);
  padding: 20px;
}
.section-tip {
  font-size: 12.5px;
  color: var(--solo-text-muted);
  line-height: 1.5;
  margin: 0 0 8px 0;
}
.section-tip b { color: var(--solo-primary); font-weight: 600; }
.disposal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
.report-section-title {
  margin: 0;
  font-size: 14.5px;
  font-weight: 700;
  color: var(--solo-text-primary);
}
</style>
