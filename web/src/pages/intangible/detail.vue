<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NButton, NTag, NSpace, NSpin, useMessage, NModal, NTabs, NTabPane, NDescriptions,
  NDescriptionsItem, NDataTable, NForm, NFormItem, NInput, NInputNumber, NSelect, NDatePicker, } from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import {
  approveIntangibleAsset, approveIntangibleDisposal, applyIntangibleLifeChange, cancelIntangibleDisposal,
  createIntangibleDisposal, createIntangibleImpairmentTest, deleteIntangibleAsset, getIntangibleAsset,
  getIntangibleDisposals, getIntangibleImpairmentTests, getIntangibleSchedule, rejectIntangibleAsset,
  reviewIntangibleImpairmentTest, submitIntangibleAsset, submitIntangibleDisposal,
} from '@/api/intangible'
import { formatMoney, formatDate } from '@/utils/format'
import type { AmortizationScheduleRow, IntangibleAsset, IntangibleDisposal, IntangibleImpairmentTest } from '@/api/types'
import { SoloEmpty } from '@/components/ui'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const assetId = computed(() => String(route.params.id || ''))
const asset = ref<IntangibleAsset | null>(null)
const schedule = ref<AmortizationScheduleRow[]>([])
const disposals = ref<IntangibleDisposal[]>([])
const loading = ref(true)
const acting = ref(false)

const showReject = ref(false)
const rejectReason = ref('')
const showDisposal = ref(false)
const disposalSubmitting = ref(false)
const disposalForm = ref({
  disposalType: 'sale' as 'sale' | 'scrap' | 'expire' | 'donate' | 'transfer_out',
  // formatted-value 的「无值」哨兵是 null，空字符串会被当作待解析日期导致弹窗崩溃
  disposalDate: null as string | null,
  proceedsAmount: 0,
  outputVatAmount: 0,
  reason: '',
})

const impairmentTests = ref<IntangibleImpairmentTest[]>([])
const showImpairment = ref(false)
const impairmentSubmitting = ref(false)
const impairmentForm = ref({
  triggerType: 'tech_obsolete' as 'tech_obsolete' | 'legal_invalid' | 'market_decline' | 'idle_long' | 'other',
  testDate: null as string | null,
  indicatorDescription: '',
  recoverableAmount: 0,
  evidenceRef: '',
})
const triggerOptions = [
  { label: '技术过时', value: 'tech_obsolete' },
  { label: '法律失效', value: 'legal_invalid' },
  { label: '市场环境恶化', value: 'market_decline' },
  { label: '长期闲置', value: 'idle_long' },
  { label: '其他', value: 'other' },
]

const showChange = ref(false)
const changeSubmitting = ref(false)
const changeForm = ref({ usefulLifeMonths: 0, residualValue: 0, effectivePeriod: '' })

const statusConfig: Record<string, { label: string; type: 'success' | 'warning' | 'info' | 'error' | 'default' }> = {
  draft: { label: '草稿', type: 'default' },
  pending_review: { label: '待复核', type: 'warning' },
  active: { label: '摊销中', type: 'success' },
  idle: { label: '已停用', type: 'info' },
  fully_amortized: { label: '已摊销完毕', type: 'warning' },
  disposing: { label: '处置中', type: 'warning' },
  disposed: { label: '已处置', type: 'info' },
  scrapped: { label: '已核销', type: 'error' },
}

const disposalTypeLabels: Record<string, string> = {
  sale: '出售',
  scrap: '报废清理',
  expire: '到期核销',
  donate: '对外捐赠',
  transfer_out: '对外投资转出',
}

const disposalTypeOptions = Object.entries(disposalTypeLabels).map(([value, label]) => ({ value, label }))

const scheduleColumns: DataTableColumns<AmortizationScheduleRow> = [
  { title: '期间', key: 'periodStart', width: 90 },
  { title: '期数', key: 'sequenceNo', width: 70 },
  { title: '摊销额（元）', key: 'amount', align: 'right', render: (row) => formatMoney(row.amount) },
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

const disposalColumns: DataTableColumns<IntangibleDisposal> = [
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
    asset.value = await getIntangibleAsset(assetId.value)
    schedule.value = (await getIntangibleSchedule(assetId.value)).items ?? []
    disposals.value = (await getIntangibleDisposals({ assetId: assetId.value })).items ?? []
    impairmentTests.value = (await getIntangibleImpairmentTests({ assetId: assetId.value })).items ?? []
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
  withLoading(() => submitIntangibleAsset(assetId.value))
}

function handleApprove() {
  withLoading(() => approveIntangibleAsset(assetId.value), '已复核过账')
}

async function handleReject() {
  if (!rejectReason.value.trim()) {
    message.warning('请填写驳回原因')
    return
  }
  await withLoading(() => rejectIntangibleAsset(assetId.value, rejectReason.value), '已驳回')
  showReject.value = false
  rejectReason.value = ''
}

function handleDelete() {
  withLoading(
    () => deleteIntangibleAsset(assetId.value),
    '已删除'
  ).then(() => router.push('/intangibles'))
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
    await createIntangibleDisposal({
      assetId: assetId.value,
      ...disposalForm.value,
      disposalDate: disposalForm.value.disposalDate,
    })
    message.success('处置单已创建，提交复核后生效；处置当月不再计提摊销')
    showDisposal.value = false
    await load()
  } catch {
    message.error('创建处置单失败')
  } finally {
    disposalSubmitting.value = false
  }
}

function handleDisposalAction(action: 'submit' | 'approve' | 'cancel', row: IntangibleDisposal) {
  const fn = action === 'submit' ? submitIntangibleDisposal : action === 'approve' ? approveIntangibleDisposal : cancelIntangibleDisposal
  withLoading(() => fn(row.id), '操作成功')
}

function openImpairment() {
  const today = new Date()
  const period = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`
  impairmentForm.value.testDate = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`
  impairmentForm.value.triggerType = 'tech_obsolete'
  impairmentForm.value.indicatorDescription = ''
  impairmentForm.value.recoverableAmount = 0
  impairmentForm.value.evidenceRef = ''
  void period
  showImpairment.value = true
}

async function handleCreateImpairment() {
  if (!impairmentForm.value.testDate) {
    message.warning('请选择测试日期')
    return
  }
  if (!impairmentForm.value.indicatorDescription.trim()) {
    message.warning('请填写减值迹象描述')
    return
  }
  impairmentSubmitting.value = true
  try {
    const result = await createIntangibleImpairmentTest({
      assetId: assetId.value,
      testPeriod: impairmentForm.value.testDate.slice(0, 7),
      testDate: impairmentForm.value.testDate,
      triggerType: impairmentForm.value.triggerType,
      indicatorDescription: impairmentForm.value.indicatorDescription.trim(),
      recoverableAmount: impairmentForm.value.recoverableAmount || 0,
      evidenceRef: impairmentForm.value.evidenceRef.trim() || undefined,
    })
    if (result.suggestion) {
      message.warning(result.suggestion, { duration: 6000 })
    } else {
      message.success('减值评估已记录，未见减值迹象')
    }
    showImpairment.value = false
    await load()
  } catch {
    message.error('创建减值评估失败')
  } finally {
    impairmentSubmitting.value = false
  }
}

function handleReviewImpairment(row: IntangibleImpairmentTest) {
  withLoading(() => reviewIntangibleImpairmentTest(row.id), '已复核')
    .then((r) => {
      const result = (r as unknown as { suggestion?: string }) ?? null
      if (result?.suggestion) message.warning(result.suggestion, { duration: 6000 })
    })
}

function openChange() {
  if (!asset.value) return
  changeForm.value.usefulLifeMonths = asset.value.usefulLifeMonths
  changeForm.value.residualValue = asset.value.residualValue
  changeForm.value.effectivePeriod = ''
  showChange.value = true
}

async function handleApplyChange() {
  if (!changeForm.value.usefulLifeMonths || changeForm.value.usefulLifeMonths < 1) {
    message.warning('请输入有效的新摊销年限')
    return
  }
  changeSubmitting.value = true
  try {
    const result = await applyIntangibleLifeChange(assetId.value, {
      usefulLifeMonths: changeForm.value.usefulLifeMonths,
      residualValue: changeForm.value.residualValue || undefined,
      effectivePeriod: changeForm.value.effectivePeriod.trim() || undefined,
    })
    message.success(
      `变更已生效（未来适用法）：生效期间 ${result.effectivePeriod}，新月摊销额 ¥${result.monthlyAmount}，计划版本 v${result.planVersion}`,
      { duration: 6000 }
    )
    showChange.value = false
    await load()
  } catch {
    message.error('变更失败：生效期间不得早于最后已摊期，且须与已摊期数一致')
  } finally {
    changeSubmitting.value = false
  }
}

const impairmentColumns: DataTableColumns<IntangibleImpairmentTest> = [
  { title: '期间', key: 'testPeriod', width: 90 },
  { title: '测试日期', key: 'testDate', width: 100, render: (row) => formatDate(row.testDate) },
  { title: '迹象类型', key: 'triggerLabel', width: 110 },
  { title: '迹象描述', key: 'indicatorDescription', ellipsis: { tooltip: true } },
  { title: '账面价值（元）', key: 'carryingAmount', align: 'right', render: (row) => formatMoney(row.carryingAmount) },
  { title: '可收回金额（元）', key: 'recoverableAmount', align: 'right', render: (row) => formatMoney(row.recoverableAmount) },
  {
    title: '结论',
    key: 'conclusion',
    width: 100,
    render: (row) =>
      h(
        NTag,
        { size: 'tiny', bordered: false, type: row.conclusion === 'impairment_noted' ? 'warning' : 'success' },
        { default: () => (row.conclusion === 'impairment_noted' ? '存在减值迹象' : '未见减值') }
      ),
  },
  {
    title: '复核',
    key: 'reviewedBy',
    width: 110,
    render: (row) => {
      if (row.reviewedBy) {
        return h('span', { style: 'font-size: 12px; color: var(--solo-text-muted);' }, `已复核 ${formatDate(row.reviewedAt || '')}`)
      }
      return h(
        NButton,
        { text: true, size: 'tiny', type: 'primary', onClick: () => handleReviewImpairment(row) },
        { default: () => '复核确认' }
      )
    },
  },
]

onMounted(load)
</script>

<template>
  <div class="detail-page">
    <div class="page-header">
      <div class="header-left">
        <NButton text class="back-btn" @click="router.push('/intangibles')">
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
        <template v-else-if="['active', 'idle', 'fully_amortized'].includes(asset.status)">
          <NButton secondary :disabled="!['active', 'idle'].includes(asset.status)" @click="openChange">变更年限/残值</NButton>
          <NButton type="warning" @click="openDisposal">处置 / 核销</NButton>
        </template>
      </NSpace>
    </div>

    <NSpin :show="loading">
      <NTabs type="line" animated>
        <NTabPane name="overview" tab="概览">
          <div class="report-card" v-if="asset">
            <NDescriptions :column="3" label-placement="top" bordered>
              <NDescriptionsItem label="资产原值">{{ formatMoney(asset.originalValue) }}</NDescriptionsItem>
              <NDescriptionsItem label="累计摊销">{{ formatMoney(asset.accumulatedAmortization) }}</NDescriptionsItem>
              <NDescriptionsItem label="账面净值">
                <span style="color: var(--solo-income); font-weight: 700;">{{ formatMoney(asset.netValue) }}</span>
              </NDescriptionsItem>
              <NDescriptionsItem label="取得方式">{{ { purchase: '外购', self_developed: '自行研发资本化', investor_contribution: '投资者投入', donation: '接受捐赠' }[asset.acquisitionType] ?? asset.acquisitionType }}</NDescriptionsItem>
              <NDescriptionsItem label="取得日期">{{ formatDate(asset.acquisitionDate) }}</NDescriptionsItem>
              <NDescriptionsItem label="可供使用日期（摊销起点）">{{ formatDate(asset.availableForUseDate) }}</NDescriptionsItem>
              <NDescriptionsItem label="摊销年限">{{ asset.usefulLifeMonths }} 个月</NDescriptionsItem>
              <NDescriptionsItem label="摊销方法">年限平均法</NDescriptionsItem>
              <NDescriptionsItem label="摊销起止">{{ asset.availableForUseDate.slice(0, 7) }} ~ {{ asset.amortizationEndPeriod ?? '—' }}</NDescriptionsItem>
              <NDescriptionsItem label="剩余月数">{{ asset.remainingMonths }}</NDescriptionsItem>
              <NDescriptionsItem label="预计净残值">{{ formatMoney(asset.residualValue) }}</NDescriptionsItem>
              <NDescriptionsItem label="权证号">{{ asset.legalCertNo || '—' }}</NDescriptionsItem>
              <NDescriptionsItem label="权利到期日">{{ asset.expiryDate ? formatDate(asset.expiryDate) : '—' }}</NDescriptionsItem>
              <NDescriptionsItem label="使用部门 / 保管人">{{ asset.custodian || '—' }}</NDescriptionsItem>
              <NDescriptionsItem label="取得凭证">{{ asset.sourceEntryId ? '已生成' : '未生成' }}</NDescriptionsItem>
            </NDescriptions>
          </div>
        </NTabPane>

        <NTabPane name="schedule" tab="摊销计划">
          <div class="report-card">
            <NDataTable :columns="scheduleColumns" :data="schedule" :bordered="false" size="small" :pagination="false" />
          </div>
        </NTabPane>

        <NTabPane name="vouchers" tab="凭证">
          <div class="report-card">
            <p class="section-tip">该资产的取得凭证、月度摊销凭证与处置凭证均可在记账凭证页检索（来源类型：intangible_purchase / intangible_amortization / intangible_disposal 等）。</p>
            <NButton text type="primary" @click="router.push('/vouchers')">前往凭证页</NButton>
          </div>
        </NTabPane>

        <NTabPane name="disposal" tab="处置与减值">
          <div class="report-card">
            <NDataTable :columns="disposalColumns" :data="disposals" :bordered="false" size="small" :pagination="false" />
            <SoloEmpty
              v-if="disposals.length === 0"
              variant="hint"
              title="暂无处置记录"
              description="无形资产卖出或报废时会在这里留痕。"
              style="margin: 24px 0;"
            />
            <div class="impairment-section">
              <div class="impairment-header">
                <h3 class="report-section-title"><i class="ri-shield-star-line"></i> 减值迹象评估</h3>
                <NButton size="small" type="primary" secondary :disabled="!['active', 'idle', 'fully_amortized'].includes(asset?.status || '')" @click="openImpairment">
                  新建评估
                </NButton>
              </div>
              <p class="section-tip">小企业会计准则不计提减值准备（仅评估留痕）；存在减值迹象时建议发起年限变更（未来适用法），损失在处置/核销时通过营业外支出体现。</p>
              <NDataTable :columns="impairmentColumns" :data="impairmentTests" :bordered="false" size="small" :pagination="false" />
              <SoloEmpty
              v-if="impairmentTests.length === 0"
              variant="hint"
              title="暂无减值评估记录"
              description="只有出现减值迹象时才需要评估，多数资产一辈子用不上这一栏。"
              style="margin: 24px 0;"
            />
            </div>
          </div>
        </NTabPane>
      </NTabs>
    </NSpin>

    <!-- 驳回 -->
    <NModal v-model:show="showReject" title="驳回取得申请" preset="card" style="width: 440px; border-radius: var(--solo-radius-lg);">
      <NFormItem label="驳回原因（必填）">
        <NInput v-model:value="rejectReason" type="textarea" :rows="3" placeholder="例如：权证信息缺失，请补充专利号" />
      </NFormItem>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showReject = false">取消</NButton>
          <NButton type="primary" :loading="acting" @click="handleReject">确认驳回</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 处置 -->
    <NModal v-model:show="showDisposal" title="处置 / 核销无形资产" preset="card" style="width: 480px; border-radius: var(--solo-radius-lg);">
      <NForm label-placement="top">
        <NFormItem label="处置方式">
          <NSelect v-model:value="disposalForm.disposalType" :options="disposalTypeOptions" />
        </NFormItem>
        <NFormItem label="处置日期（该月不再计提摊销）">
          <NDatePicker v-model:formatted-value="disposalForm.disposalDate" value-format="yyyy-MM-dd" type="date" style="width: 100%;" />
        </NFormItem>
        <NFormItem label="处置价款（不含税，出售/转让必填）">
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
    <!-- 减值评估 -->
    <NModal v-model:show="showImpairment" title="减值迹象评估" preset="card" style="width: 480px; border-radius: var(--solo-radius-lg);">
      <NForm label-placement="top">
        <NFormItem label="迹象类型">
          <NSelect v-model:value="impairmentForm.triggerType" :options="triggerOptions" />
        </NFormItem>
        <NFormItem label="测试日期">
          <NDatePicker v-model:formatted-value="impairmentForm.testDate" value-format="yyyy-MM-dd" type="date" style="width: 100%;" />
        </NFormItem>
        <NFormItem label="迹象描述（必填）">
          <NInput v-model:value="impairmentForm.indicatorDescription" type="textarea" :rows="3" placeholder="例如：该专利技术已被新一代方案替代，继续使用经济性显著下降" />
        </NFormItem>
        <NFormItem label="可收回金额（元）">
          <NInputNumber v-model:value="impairmentForm.recoverableAmount" :min="0" :step="1000" style="width: 100%;" placeholder="评估值；低于账面价值即认定存在减值迹象" />
        </NFormItem>
        <NFormItem label="佐证材料编号（选填）">
          <NInput v-model:value="impairmentForm.evidenceRef" placeholder="评估报告编号等" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showImpairment = false">取消</NButton>
          <NButton type="primary" :loading="impairmentSubmitting" @click="handleCreateImpairment">保存评估</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 年限/残值变更 -->
    <NModal v-model:show="showChange" title="变更摊销年限 / 残值" preset="card" style="width: 460px; border-radius: var(--solo-radius-lg);">
      <NForm label-placement="top">
        <NFormItem label="新摊销年限（月）">
          <NInputNumber v-model:value="changeForm.usefulLifeMonths" :min="1" :max="840" style="width: 100%;" />
        </NFormItem>
        <NFormItem label="新预计净残值（元）">
          <NInputNumber v-model:value="changeForm.residualValue" :min="0" :step="100" style="width: 100%;" />
        </NFormItem>
        <NFormItem label="生效期间（YYYY-MM，选填）">
          <NInput v-model:value="changeForm.effectivePeriod" placeholder="缺省为最后已摊期的下一期" />
        </NFormItem>
      </NForm>
      <p class="section-tip">会计估计变更采用<b>未来适用法</b>：已过账期次不变，仅重算未执行期间；变更留痕于审计日志。</p>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showChange = false">取消</NButton>
          <NButton type="primary" :loading="changeSubmitting" @click="handleApplyChange">确认变更</NButton>
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
.impairment-section {
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px dashed var(--solo-border-light);
}
.impairment-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}
</style>
