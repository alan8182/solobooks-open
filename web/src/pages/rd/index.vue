<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { usePageRefresh } from '@/composables/usePageRefresh'
import { computed, onMounted, ref } from 'vue'
import {
  NAlert, NButton, NCard, NDatePicker, NForm, NFormItem, NInput, NInputNumber, NModal,
  NSelect, NSpace, NSpin, NStatistic, NTag, useMessage,
} from 'naive-ui'
import dayjs from 'dayjs'
import { correctRdUnallocated, createRdEvidence, createRdProject, getRdEvidence, getRdEvidenceTypes, getRdExpenseTypes, getRdLedger, getRdProjects, getRdReconciliationSummary, getRdUnallocated, updateRdProject } from '@/api/rd'
import type { RdCorrectionAllocation, RdEvidenceDocument, RdEvidenceInput, RdLedgerItem, RdProjectInput, RdReconciliationSummary, RdUnallocatedItem } from '@/api/rd'
import type { RdExpenseTypeOption, RdProject } from '@/api/types'
import { formatMoney } from '@/utils/format'
import { SoloEmpty } from '@/components/ui'

const message = useMessage()
const loading = ref(false)
const projects = ref<RdProject[]>([])
const ledgerItems = ref<RdLedgerItem[]>([])
const total = ref(0)
const unallocatedItems = ref<RdUnallocatedItem[]>([])
const reconciliation = ref<RdReconciliationSummary | null>(null)
const expenseTypes = ref<RdExpenseTypeOption[]>([])
const evidenceDocuments = ref<RdEvidenceDocument[]>([])
const evidenceTypes = ref<Array<{ value: string; label: string }>>([])
const showCreate = ref(false)
const saving = ref(false)
const showCorrection = ref(false)
const correcting = ref(false)
const correctionTarget = ref<RdUnallocatedItem | null>(null)
const correctionDate = ref(dayjs().format('YYYY-MM-DD'))
const correctionReason = ref('补充研发项目、费用类别及分摊依据')
const correctionAllocations = ref<RdCorrectionAllocation[]>([])
const showEvidence = ref(false)
const savingEvidence = ref(false)
const evidenceForm = ref<RdEvidenceInput>({
  projectId: '', entryId: '', documentType: 'other', referenceNo: '', fileUrl: '', status: 'available', notes: '',
})
const selectedProject = ref<string | null>(null)
const applicationRegion = ref('')
const applicationBatch = ref('')
const period = ref<[number, number]>([
  dayjs().startOf('year').valueOf(),
  dayjs().endOf('year').valueOf(),
])

const form = ref<RdProjectInput>({
  code: '',
  name: '',
  manager: '',
  startDate: dayjs().format('YYYY-MM-DD'),
  endDate: dayjs().endOf('year').format('YYYY-MM-DD'),
  status: 'active',
  initiationDocumentStatus: 'missing',
  notes: '',
})

const projectOptions = computed(() => [
  { label: '全部研发项目', value: '' },
  ...projects.value.map((project) => ({ label: `${project.code} ${project.name}`, value: project.id })),
])

const activeCount = computed(() => projects.value.filter((project) => project.status === 'active').length)
const activeProjectOptions = computed(() => projects.value
  .filter(project => ['active', 'completed'].includes(project.status))
  .map(project => ({ label: `${project.code} · ${project.name}`, value: project.id })))
const expenseTypeOptions = computed(() => expenseTypes.value.map(item => ({ label: item.label, value: item.value })))
const correctionTotal = computed(() => correctionAllocations.value.reduce((sum, item) => sum + Number(item.amount || 0), 0))
function statusLabel(status: RdProject['status']) {
  return { draft: '草稿', active: '进行中', completed: '已完成', archived: '已归档' }[status]
}

function documentLabel(status: RdProject['initiationDocumentStatus']) {
  return { missing: '缺少立项资料', partial: '资料不完整', complete: '资料完整' }[status]
}

async function load() {
  loading.value = true
  try {
    const [projectData, typeData, evidenceTypeData] = await Promise.all([getRdProjects(), getRdExpenseTypes(), getRdEvidenceTypes()])
    projects.value = projectData
    expenseTypes.value = typeData.items
    evidenceTypes.value = evidenceTypeData.items
    const params = {
      startDate: dayjs(period.value[0]).format('YYYY-MM-DD'),
      endDate: dayjs(period.value[1]).format('YYYY-MM-DD'),
      projectId: selectedProject.value || undefined,
    }
    const [data, unallocated, summary, evidence] = await Promise.all([
      getRdLedger(params),
      getRdUnallocated(),
      getRdReconciliationSummary({
        startDate: params.startDate, endDate: params.endDate,
        region: applicationRegion.value || undefined, programBatch: applicationBatch.value || undefined,
      }),
      getRdEvidence(),
    ])
    ledgerItems.value = data.items
    total.value = data.total
    unallocatedItems.value = unallocated.items
    reconciliation.value = summary
    evidenceDocuments.value = evidence.items
  } catch {
    message.error('研发费用数据加载失败')
  } finally {
    loading.value = false
  }
}

function openEvidence(projectId = '', entryId = '') {
  evidenceForm.value = {
    projectId, entryId, documentType: 'other', referenceNo: '', fileUrl: '', status: 'available', notes: '',
  }
  showEvidence.value = true
}

function evidenceTypeLabel(value: string) {
  return evidenceTypes.value.find(item => item.value === value)?.label || value
}

function projectLabel(projectId: string) {
  const project = projects.value.find(item => item.id === projectId)
  return project ? `${project.code} · ${project.name}` : projectId
}

async function saveEvidence() {
  if (!evidenceForm.value.projectId || (!evidenceForm.value.referenceNo?.trim() && !evidenceForm.value.fileUrl?.trim())) {
    message.warning('请选择研发项目，并填写证据编号或文件链接')
    return
  }
  savingEvidence.value = true
  try {
    await createRdEvidence({ ...evidenceForm.value, entryId: evidenceForm.value.entryId || undefined })
    message.success('研发证据已登记')
    showEvidence.value = false
    await load()
  } catch (error: any) {
    message.error(error?.response?.data?.message || error?.response?.data?.detail || '证据登记失败')
  } finally {
    savingEvidence.value = false
  }
}

function openCorrection(item: RdUnallocatedItem) {
  correctionTarget.value = item
  correctionDate.value = dayjs().format('YYYY-MM-DD')
  correctionReason.value = '补充研发项目、费用类别及分摊依据'
  correctionAllocations.value = [{
    projectId: '', expenseType: 'direct_input', amount: Math.abs(Number(item.amount)),
    allocationMethod: 'direct', allocationBasis: '', notes: '',
  }]
  showCorrection.value = true
}

function addAllocation() {
  correctionAllocations.value.push({
    projectId: '', expenseType: 'direct_input', amount: 0,
    allocationMethod: 'other', allocationBasis: '', notes: '',
  })
}

function removeAllocation(index: number) {
  if (correctionAllocations.value.length > 1) correctionAllocations.value.splice(index, 1)
}

async function submitCorrection() {
  const target = correctionTarget.value
  if (!target || !correctionReason.value.trim()) return
  const expected = Math.abs(Number(target.amount))
  if (correctionAllocations.value.some(item => !item.projectId || !item.expenseType || Number(item.amount) <= 0)) {
    message.warning('请完整填写每条项目、费用类别和金额')
    return
  }
  if (correctionAllocations.value.length > 1 && correctionAllocations.value.some(item => !item.allocationBasis?.trim())) {
    message.warning('多项目拆分必须填写每条分摊依据')
    return
  }
  if (Math.abs(correctionTotal.value - expected) > 0.001) {
    message.warning(`分配合计必须等于 ${formatMoney(expected)}`)
    return
  }
  correcting.value = true
  try {
    await correctRdUnallocated(target.entryId, {
      correctionDate: correctionDate.value,
      reason: correctionReason.value.trim(),
      allocations: correctionAllocations.value,
    })
    message.success(target.status === 'posted' ? '已生成冲销凭证和正确的研发归集凭证' : '研发归集已补充')
    showCorrection.value = false
    await load()
  } catch (error: any) {
    message.error(error?.response?.data?.message || error?.response?.data?.detail || '研发归集更正失败')
  } finally {
    correcting.value = false
  }
}

async function saveProject() {
  if (!form.value.code.trim() || !form.value.name.trim()) {
    message.warning('请填写项目编号和名称')
    return
  }
  saving.value = true
  try {
    await createRdProject(form.value)
    showCreate.value = false
    message.success('研发项目已创建')
    form.value = {
      code: '', name: '', manager: '', startDate: dayjs().format('YYYY-MM-DD'),
      endDate: dayjs().endOf('year').format('YYYY-MM-DD'), status: 'active',
      initiationDocumentStatus: 'missing', notes: '',
    }
    await load()
  } catch (error: any) {
    message.error(error?.response?.data?.message || error?.response?.data?.detail || '创建失败')
  } finally {
    saving.value = false
  }
}

async function archiveProject(project: RdProject) {
  try {
    await updateRdProject(project.id, { status: 'archived' })
    message.success('项目已归档')
    await load()
  } catch {
    message.error('项目归档失败')
  }
}

async function exportLedger() {
  if (!ledgerItems.value.length && !unallocatedItems.value.length) {
    message.warning('当前没有可导出的研发费用数据')
    return
  }
  const XLSX = await import('xlsx')
  const workbook = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(ledgerItems.value.map((item) => ({
    日期: item.date, 凭证号: item.voucherNo || '', 项目编号: item.projectCode, 项目名称: item.projectName,
    摘要: item.summary, 费用类别: item.expenseTypeLabel, 科目编码: item.accountCode,
    研发金额: item.amount, 分摊比例: item.allocationRate, 分摊方法: item.allocationMethod,
    分摊依据: item.allocationBasis || '',
  }))), '研发费用辅助账')
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(projects.value.map((project) => ({
    项目编号: project.code, 项目名称: project.name, 负责人: project.manager || '', 开始日期: project.startDate,
    结束日期: project.endDate || '', 状态: statusLabel(project.status), 立项资料: documentLabel(project.initiationDocumentStatus),
  }))), '研发项目')
  XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(unallocatedItems.value.map((item) => ({
    日期: item.date, 凭证号: item.voucherNo || '', 摘要: item.summary, 科目编码: item.accountCode,
    科目名称: item.accountName, 金额: item.amount, 问题: item.reason,
  }))), '待补充归集')
  if (reconciliation.value) {
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(reconciliation.value.projectSummary.map(item => ({
      项目编号: item.projectCode, 项目名称: item.projectName, 研发费用: item.amount,
    }))), '项目汇总')
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(reconciliation.value.expenseTypeSummary.map(item => ({
      费用类别: item.label, 科目编码: item.accountCode, 研发费用: item.amount,
    }))), '费用类别汇总')
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet([{
      规则版本: reconciliation.value.policyVersion, 规则名称: reconciliation.value.policyLabel,
      辅助账合计: reconciliation.value.ledgerTotal, 总账合计: reconciliation.value.generalLedgerTotal,
      差异: reconciliation.value.difference, 勾稽结果: reconciliation.value.isReconciled ? '一致' : '不一致',
      申报预审: reconciliation.value.policyStatus === 'suggested' ? '建议纳入复核' : '待复核',
      说明: reconciliation.value.policyDisclaimer,
      申报地区: reconciliation.value.region || '', 申报批次: reconciliation.value.programBatch || '',
    }]), '勾稽与政策口径')
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(reconciliation.value.evidenceIssues.map(item => ({
      项目编号: item.projectCode, 凭证号: item.voucherNo || '', 问题类型: item.type,
      问题说明: item.message, 金额: item.amount, 账目ID: item.entryId,
    }))), '证据缺口清单')
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(evidenceDocuments.value.map(item => ({
      项目: projectLabel(item.projectId), 账目ID: item.entryId || '', 资料类型: evidenceTypeLabel(item.documentType),
      编号: item.referenceNo || '', 文件链接: item.fileUrl || '', 状态: item.status,
      备注: item.notes || '', 登记时间: item.createdAt,
    }))), '证据资料清单')
    XLSX.utils.book_append_sheet(workbook, XLSX.utils.json_to_sheet(reconciliation.value.policyReviewItems.map(item => ({
      项目编号: item.projectCode, 凭证号: item.voucherNo || '', 费用类别: item.expenseTypeLabel,
      金额: item.amount, 建议状态: item.status === 'suggested' ? '建议纳入复核' : item.status === 'not_included' ? '建议不纳入' : '待复核',
      判断说明: item.reason, 账目ID: item.entryId,
    }))), '政策复核建议')
  }
  XLSX.writeFile(workbook, `研发费用辅助账_${dayjs(period.value[0]).format('YYYYMMDD')}_${dayjs(period.value[1]).format('YYYYMMDD')}.xlsx`)
}

onMounted(load)

// 从别的标签切回来时静默重拉，避免对着旧数字干活
usePageRefresh(load)
</script>

<template>
  <div class="rd-page">
    <div class="page-header">
      <div>
        <h2>研发费用</h2>
        <p>按研发项目和费用类别归集，辅助账与已过账总账保持一致。</p>
      </div>
      <NSpace><NButton @click="openEvidence()">登记研发证据</NButton><NButton type="primary" @click="showCreate = true">新建研发项目</NButton></NSpace>
    </div>

    <NAlert type="info" :bordered="false" class="mb-4">
      本页面记录会计归集事实，不代表自动符合某项补助政策；申报前仍需按当期政策复核。
    </NAlert>

    <div class="stats-grid">
      <NCard :bordered="false"><NStatistic label="研发项目" :value="projects.length" /></NCard>
      <NCard :bordered="false"><NStatistic label="进行中" :value="activeCount" /></NCard>
      <NCard :bordered="false"><NStatistic label="待补充归集" :value="unallocatedItems.length" /></NCard>
      <NCard :bordered="false"><NStatistic label="期间研发费用" :value="formatMoney(total)" /></NCard>
    </div>

    <NCard v-if="reconciliation" :bordered="false" title="总账勾稽与申报预审" class="mb-4">
      <NAlert :type="reconciliation.isReconciled ? 'success' : 'error'" :bordered="false" class="mb-4">
        辅助账 {{ formatMoney(reconciliation.ledgerTotal) }}，4301 系列总账 {{ formatMoney(reconciliation.generalLedgerTotal) }}，
        差异 {{ formatMoney(reconciliation.difference) }}。{{ reconciliation.isReconciled ? '账表一致。' : '存在差异，暂不能用于申报复核。' }}
      </NAlert>
      <div class="reconciliation-grid">
        <div><span>规则版本</span><strong>{{ reconciliation.policyVersion }}</strong></div>
        <div><span>资料预审</span><strong>{{ reconciliation.policyStatus === 'suggested' ? '建议纳入复核' : '待补资料/人工复核' }}</strong></div>
        <div><span>证据缺口</span><strong>{{ reconciliation.evidenceIssues.length }}</strong></div>
        <div><span>待归集</span><strong>{{ reconciliation.unallocatedCount }}</strong></div>
      </div>
      <p class="policy-note">{{ reconciliation.policyDisclaimer }}</p>
      <div v-if="reconciliation.evidenceIssues.length" class="table-wrap">
        <table>
          <thead><tr><th>项目</th><th>凭证号</th><th>问题</th><th class="amount">金额</th><th>操作</th></tr></thead>
          <tbody><tr v-for="issue in reconciliation.evidenceIssues" :key="`${issue.entryId}-${issue.type}`">
            <td>{{ issue.projectCode }}</td><td>{{ issue.voucherNo || '-' }}</td><td>{{ issue.message }}</td>
            <td class="amount">{{ formatMoney(issue.amount) }}</td>
            <td><NButton size="tiny" secondary @click="openEvidence(issue.projectId, issue.entryId)">登记资料</NButton></td>
          </tr></tbody>
        </table>
      </div>
    </NCard>

    <NCard :bordered="false" title="研发证据资料" class="mb-4">
      <div v-if="evidenceDocuments.length" class="table-wrap">
        <table>
          <thead><tr><th>项目</th><th>资料类型</th><th>编号/链接</th><th>关联账目</th><th>状态</th></tr></thead>
          <tbody><tr v-for="document in evidenceDocuments" :key="document.id">
            <td>{{ projectLabel(document.projectId) }}</td><td>{{ evidenceTypeLabel(document.documentType) }}</td>
            <td><a v-if="document.fileUrl" :href="document.fileUrl" target="_blank" rel="noopener">{{ document.referenceNo || '查看文件' }}</a><span v-else>{{ document.referenceNo }}</span></td>
            <td>{{ document.entryId || '项目级资料' }}</td>
            <td><NTag size="small" :type="document.status === 'available' ? 'success' : 'warning'">{{ document.status === 'available' ? '可用' : document.status === 'pending_review' ? '待复核' : '缺失' }}</NTag></td>
          </tr></tbody>
        </table>
      </div>
      <SoloEmpty
        v-else
        variant="hint"
        title="尚未登记研发证据资料"
        description="立项文件、研发台账等证明材料齐了，加计扣除才立得住。"
      />
    </NCard>

    <NCard :bordered="false" title="研发项目" class="mb-4">
      <div v-if="projects.length" class="project-list">
        <div v-for="project in projects" :key="project.id" class="project-row">
          <div>
            <strong>{{ project.code }} · {{ project.name }}</strong>
            <p>{{ project.startDate }} 至 {{ project.endDate || '未设结束日期' }} · 负责人：{{ project.manager || '未填写' }}</p>
          </div>
          <NSpace align="center">
            <NTag :type="project.status === 'active' ? 'success' : 'default'">{{ statusLabel(project.status) }}</NTag>
            <NTag :type="project.initiationDocumentStatus === 'complete' ? 'success' : 'warning'">
              {{ documentLabel(project.initiationDocumentStatus) }}
            </NTag>
            <NButton v-if="project.status !== 'archived'" text size="small" @click="archiveProject(project)">归档</NButton>
          </NSpace>
        </div>
      </div>
      <SoloEmpty
        v-else
        variant="hint"
        title="尚未建立研发项目"
        description="先建项目，费用归集和加计扣除才有归处。"
      />
    </NCard>

    <NCard v-if="unallocatedItems.length" :bordered="false" title="待补充归集" class="mb-4">
      <NAlert type="warning" :bordered="false" class="mb-4">
        以下历史研发分录缺少项目或费用类别。当前仅提示，不会自动猜测或改写已过账凭证。
      </NAlert>
      <div class="table-wrap">
        <table>
          <thead><tr><th>日期</th><th>凭证号</th><th>摘要</th><th>原科目</th><th>状态</th><th class="amount">金额</th><th>操作</th></tr></thead>
          <tbody><tr v-for="item in unallocatedItems" :key="`${item.entryId}-${item.accountCode}`">
            <td>{{ item.date }}</td><td>{{ item.voucherNo || '-' }}</td><td>{{ item.summary }}</td>
            <td>{{ item.accountCode }} {{ item.accountName }}</td>
            <td><NTag size="small" :type="item.status === 'posted' ? 'warning' : 'info'">{{ item.status === 'posted' ? '已过账' : '草稿' }}</NTag></td>
            <td class="amount">{{ formatMoney(item.amount) }}</td>
            <td><NButton size="small" type="primary" secondary @click="openCorrection(item)">补充归集</NButton></td>
          </tr></tbody>
        </table>
      </div>
    </NCard>

    <NCard :bordered="false" title="研发费用辅助账">
      <NSpace align="center" wrap class="filters">
        <NDatePicker v-model:value="period" type="daterange" :clearable="false" />
        <NSelect v-model:value="selectedProject" :options="projectOptions" style="width: 240px" clearable />
        <NInput v-model:value="applicationRegion" placeholder="申报地区（选填）" style="width: 180px" />
        <NInput v-model:value="applicationBatch" placeholder="专项/批次（选填）" style="width: 200px" />
        <NButton @click="load">查询</NButton>
        <NButton @click="exportLedger">导出 Excel</NButton>
      </NSpace>
      <NSpin :show="loading">
        <div class="table-wrap">
          <table>
            <thead><tr><th>日期</th><th>项目</th><th>摘要</th><th>费用类别</th><th>凭证号</th><th class="amount">研发金额</th></tr></thead>
            <tbody>
              <tr v-for="item in ledgerItems" :key="`${item.entryId}-${item.accountCode}`">
                <td>{{ item.date }}</td><td>{{ item.projectCode }} {{ item.projectName }}</td><td>{{ item.summary }}</td>
                <td>{{ item.expenseTypeLabel }}</td><td>{{ item.voucherNo || '-' }}</td><td class="amount">{{ formatMoney(item.amount) }}</td>
              </tr>
              <tr v-if="!ledgerItems.length"><td colspan="6" class="empty">所选期间暂无已过账研发费用。</td></tr>
            </tbody>
          </table>
        </div>
      </NSpin>
    </NCard>

    <NModal v-model:show="showCreate" preset="card" title="新建研发项目" style="width: min(620px, 92vw)">
      <NForm label-placement="top">
        <div class="form-grid">
          <NFormItem label="项目编号"><NInput v-model:value="form.code" placeholder="例如 RD-2026-01" /></NFormItem>
          <NFormItem label="项目名称"><NInput v-model:value="form.name" /></NFormItem>
          <NFormItem label="负责人"><NInput v-model:value="form.manager" /></NFormItem>
          <NFormItem label="项目状态"><NSelect v-model:value="form.status" :options="[{label:'草稿',value:'draft'},{label:'进行中',value:'active'},{label:'已完成',value:'completed'}]" /></NFormItem>
          <NFormItem label="开始日期"><NInput v-model:value="form.startDate" placeholder="YYYY-MM-DD" /></NFormItem>
          <NFormItem label="结束日期"><NInput v-model:value="form.endDate" placeholder="YYYY-MM-DD" /></NFormItem>
          <NFormItem label="立项资料"><NSelect v-model:value="form.initiationDocumentStatus" :options="[{label:'缺少',value:'missing'},{label:'部分完整',value:'partial'},{label:'完整',value:'complete'}]" /></NFormItem>
          <NFormItem label="备注"><NInput v-model:value="form.notes" /></NFormItem>
        </div>
        <NSpace justify="end"><NButton @click="showCreate = false">取消</NButton><NButton type="primary" :loading="saving" @click="saveProject">保存</NButton></NSpace>
      </NForm>
    </NModal>

    <NModal v-model:show="showCorrection" preset="card" title="补充研发归集" style="width: min(820px, 96vw)">
      <NAlert v-if="correctionTarget" :type="correctionTarget.status === 'posted' ? 'warning' : 'info'" :bordered="false" class="mb-4">
        {{ correctionTarget.status === 'posted'
          ? '该凭证已过账，提交后将在更正日期生成冲销凭证和新的正确凭证，不会改写原凭证。'
          : '该凭证尚未过账，将直接重建草稿分录并保留审计记录。' }}
      </NAlert>
      <NForm label-placement="top">
        <div class="form-grid">
          <NFormItem label="待归集金额"><NInput :value="formatMoney(Math.abs(Number(correctionTarget?.amount || 0)))" disabled /></NFormItem>
          <NFormItem label="更正日期"><NInput v-model:value="correctionDate" placeholder="YYYY-MM-DD" /></NFormItem>
        </div>
        <NFormItem label="更正原因"><NInput v-model:value="correctionReason" /></NFormItem>
        <div class="allocation-header"><strong>项目分配</strong><NButton size="small" @click="addAllocation">增加项目</NButton></div>
        <div v-for="(allocation, index) in correctionAllocations" :key="index" class="allocation-row">
          <NSelect v-model:value="allocation.projectId" :options="activeProjectOptions" placeholder="研发项目" filterable />
          <NSelect v-model:value="allocation.expenseType" :options="expenseTypeOptions" placeholder="费用类别" />
          <NInputNumber v-model:value="allocation.amount" :min="0.01" :precision="2" :show-button="false" placeholder="金额" />
          <NSelect v-model:value="allocation.allocationMethod" :options="[
            {label:'直接归集',value:'direct'},{label:'工时',value:'timesheet'},{label:'使用量',value:'usage'},
            {label:'面积',value:'area'},{label:'数量',value:'quantity'},{label:'其他',value:'other'},
          ]" />
          <NInput v-model:value="allocation.allocationBasis" placeholder="分摊依据/合同/领料单" />
          <NButton quaternary type="error" :disabled="correctionAllocations.length === 1" @click="removeAllocation(index)">删除</NButton>
        </div>
        <div class="allocation-total" :class="Math.abs(correctionTotal - Math.abs(Number(correctionTarget?.amount || 0))) > 0.001 ? 'mismatch' : ''">
          分配合计：{{ formatMoney(correctionTotal) }} / {{ formatMoney(Math.abs(Number(correctionTarget?.amount || 0))) }}
        </div>
        <NSpace justify="end"><NButton @click="showCorrection = false">取消</NButton><NButton type="primary" :loading="correcting" @click="submitCorrection">确认更正</NButton></NSpace>
      </NForm>
    </NModal>

    <NModal v-model:show="showEvidence" preset="card" title="登记研发证据" style="width: min(620px, 92vw)">
      <NForm label-placement="top">
        <div class="form-grid">
          <NFormItem label="研发项目"><NSelect v-model:value="evidenceForm.projectId" :options="activeProjectOptions" filterable /></NFormItem>
          <NFormItem label="资料类型"><NSelect v-model:value="evidenceForm.documentType" :options="evidenceTypes" /></NFormItem>
          <NFormItem label="关联账目 ID（选填）"><NInput v-model:value="evidenceForm.entryId" /></NFormItem>
          <NFormItem label="资料状态"><NSelect v-model:value="evidenceForm.status" :options="[{label:'可用',value:'available'},{label:'待复核',value:'pending_review'},{label:'缺失',value:'missing'}]" /></NFormItem>
          <NFormItem label="资料编号"><NInput v-model:value="evidenceForm.referenceNo" placeholder="合同号、发票号、工时表编号等" /></NFormItem>
          <NFormItem label="文件链接"><NInput v-model:value="evidenceForm.fileUrl" placeholder="https://... 或企业文件地址" /></NFormItem>
        </div>
        <NFormItem label="备注"><NInput v-model:value="evidenceForm.notes" type="textarea" :rows="2" /></NFormItem>
        <NAlert type="info" :bordered="false" class="mb-4">资料编号或文件链接至少填写一项。项目级立项资料可不关联具体账目。</NAlert>
        <NSpace justify="end"><NButton @click="showEvidence = false">取消</NButton><NButton type="primary" :loading="savingEvidence" @click="saveEvidence">保存资料</NButton></NSpace>
      </NForm>
    </NModal>
  </div>
</template>

<style scoped>
.rd-page { max-width: 1180px; margin: 0 auto; }
.page-header { display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; margin-bottom: 20px; }
.page-header h2 { margin: 0; font-size: 22px; }
.page-header p, .project-row p { margin: 5px 0 0; color: var(--solo-text-secondary); font-size: 13px; }
.mb-4 { margin-bottom: 16px; }
.stats-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 16px; }
.project-list { display: grid; gap: 0; }
.project-row { display: flex; justify-content: space-between; gap: 20px; padding: 14px 0; border-bottom: 1px solid var(--solo-border-light); }
.project-row:last-child { border-bottom: 0; }
.filters { margin-bottom: 16px; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 12px; border-bottom: 1px solid var(--solo-border-light); text-align: left; white-space: nowrap; }
th { color: var(--solo-text-secondary); font-weight: 600; }
.amount { text-align: right; font-variant-numeric: tabular-nums; }
.empty { text-align: center; color: var(--solo-text-muted); padding: 28px; }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 0 16px; }
.reconciliation-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.reconciliation-grid > div { padding: 12px; background: var(--solo-bg-page); border-radius: 8px; display: grid; gap: 4px; }
.reconciliation-grid span, .policy-note { font-size: 12px; color: var(--solo-text-muted); }
.policy-note { margin: 12px 0 0; }
.allocation-header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 10px; }
.allocation-row { display: grid; grid-template-columns: 1.25fr 1fr .75fr .8fr 1.4fr auto; gap: 8px; margin-bottom: 10px; align-items: center; }
.allocation-total { text-align: right; font-weight: 600; margin: 12px 0; }
.allocation-total.mismatch { color: var(--solo-expense); }
@media (max-width: 760px) {
  .stats-grid { grid-template-columns: 1fr 1fr; }
  .page-header, .project-row { flex-direction: column; }
  .form-grid { grid-template-columns: 1fr; }
  .reconciliation-grid { grid-template-columns: 1fr 1fr; }
  .allocation-row { grid-template-columns: 1fr; padding: 12px; border: 1px solid var(--solo-border-light); border-radius: 8px; }
}
</style>
