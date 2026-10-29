<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NBadge, NButton, NGrid, NGridItem, NTab, NTabs, useMessage,
} from 'naive-ui'
import dayjs from 'dayjs'
import { usePageRefresh } from '@/composables/usePageRefresh'
import {
  getRdEvidence, getRdEvidenceTypes, getRdExpenseTypes, getRdLedger,
  getRdProjects, getRdReconciliationSummary, getRdUnallocated,
} from '@/api/rd'
import type {
  RdEvidenceDocument, RdLedgerItem, RdReconciliationSummary, RdUnallocatedItem,
} from '@/api/rd'
import type { RdExpenseTypeOption, RdProject } from '@/api/types'
import { formatMoney } from '@/utils/format'
import { saveWorkbook } from '@/utils/export'

import RdLedgerTab from './components/RdLedgerTab.vue'
import RdProjectsTab from './components/RdProjectsTab.vue'
import RdUnallocatedTab from './components/RdUnallocatedTab.vue'
import RdReconciliationTab from './components/RdReconciliationTab.vue'
import RdProjectModal from './components/RdProjectModal.vue'
import RdCorrectionModal from './components/RdCorrectionModal.vue'
import RdEvidenceModal from './components/RdEvidenceModal.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()

type TabKey = 'ledger' | 'projects' | 'unallocated' | 'reconciliation'

const validTabs: TabKey[] = ['ledger', 'projects', 'unallocated', 'reconciliation']
function parseTab(raw: unknown): TabKey {
  return validTabs.includes(raw as TabKey) ? (raw as TabKey) : 'ledger'
}

const activeTab = ref<TabKey>(parseTab(route.query.tab))

watch(
  () => route.query.tab,
  (tab) => {
    const target = parseTab(tab)
    if (activeTab.value !== target) {
      activeTab.value = target
    }
  },
)

function onTabChange(val: string) {
  const target = parseTab(val)
  activeTab.value = target
  router.replace({ query: { ...route.query, tab: target } })
}

// 核心数据
const loading = ref(false)
const projects = ref<RdProject[]>([])
const ledgerItems = ref<RdLedgerItem[]>([])
const total = ref(0)
const unallocatedItems = ref<RdUnallocatedItem[]>([])
const reconciliation = ref<RdReconciliationSummary | null>(null)
const expenseTypes = ref<RdExpenseTypeOption[]>([])
const evidenceDocuments = ref<RdEvidenceDocument[]>([])
const evidenceTypes = ref<Array<{ value: string; label: string }>>([])

// 筛选条件
const selectedProject = ref<string | null>(null)
const applicationRegion = ref('')
const applicationBatch = ref('')
const period = ref<[number, number]>([
  dayjs().startOf('year').valueOf(),
  dayjs().endOf('year').valueOf(),
])

// 弹窗状态
const showProjectModal = ref(false)
const editingProject = ref<RdProject | null>(null)
const showCorrectionModal = ref(false)
const correctionTarget = ref<RdUnallocatedItem | null>(null)
const showEvidenceModal = ref(false)
const evidenceDefaultProject = ref<string>('')
const evidenceDefaultEntry = ref<string>('')

const activeCount = computed(() => projects.value.filter((p) => p.status === 'active').length)

async function load() {
  loading.value = true
  try {
    const [projectData, typeData, evidenceTypeData] = await Promise.all([
      getRdProjects(true),
      getRdExpenseTypes(),
      getRdEvidenceTypes(),
    ])
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
        startDate: params.startDate,
        endDate: params.endDate,
        region: applicationRegion.value || undefined,
        programBatch: applicationBatch.value || undefined,
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

// 弹窗打开函数
function openCreateProject() {
  editingProject.value = null
  showProjectModal.value = true
}

function openEditProject(project: RdProject) {
  editingProject.value = project
  showProjectModal.value = true
}

function openCorrection(item: RdUnallocatedItem) {
  correctionTarget.value = item
  showCorrectionModal.value = true
}

function openEvidence(projectId = '', entryId = '') {
  evidenceDefaultProject.value = projectId
  evidenceDefaultEntry.value = entryId
  showEvidenceModal.value = true
}

function viewProjectLedger(projectId: string) {
  selectedProject.value = projectId
  onTabChange('ledger')
  void load()
}

// Excel 导出
async function exportLedger() {
  if (!ledgerItems.value.length && !unallocatedItems.value.length) {
    message.warning('当前没有可导出的研发费用数据')
    return
  }
  const XLSX = await import('xlsx')
  const workbook = XLSX.utils.book_new()

  XLSX.utils.book_append_sheet(
    workbook,
    XLSX.utils.json_to_sheet(
      ledgerItems.value.map((item) => ({
        日期: item.date,
        凭证号: item.voucherNo || '',
        项目编号: item.projectCode,
        项目名称: item.projectName,
        摘要: item.summary,
        费用类别: item.expenseTypeLabel,
        科目编码: item.accountCode,
        研发金额: item.amount,
        分摊比例: item.allocationRate,
        分摊方法: item.allocationMethod,
        分摊依据: item.allocationBasis || '',
      })),
    ),
    '研发费用辅助账',
  )

  XLSX.utils.book_append_sheet(
    workbook,
    XLSX.utils.json_to_sheet(
      projects.value.map((p) => ({
        项目编号: p.code,
        项目名称: p.name,
        负责人: p.manager || '',
        开始日期: p.startDate,
        结束日期: p.endDate || '',
        状态: p.status,
        立项资料: p.initiationDocumentStatus,
      })),
    ),
    '研发项目台账',
  )

  if (unallocatedItems.value.length) {
    XLSX.utils.book_append_sheet(
      workbook,
      XLSX.utils.json_to_sheet(
        unallocatedItems.value.map((item) => ({
          日期: item.date,
          凭证号: item.voucherNo || '',
          摘要: item.summary,
          科目编码: item.accountCode,
          科目名称: item.accountName,
          金额: item.amount,
          诊断原因: item.reason,
        })),
      ),
      '待补充归集',
    )
  }

  if (reconciliation.value) {
    XLSX.utils.book_append_sheet(
      workbook,
      XLSX.utils.json_to_sheet(
        reconciliation.value.projectSummary.map((item) => ({
          项目编号: item.projectCode,
          项目名称: item.projectName,
          研发费用: item.amount,
        })),
      ),
      '项目汇总',
    )

    XLSX.utils.book_append_sheet(
      workbook,
      XLSX.utils.json_to_sheet(
        reconciliation.value.expenseTypeSummary.map((item) => ({
          费用类别: item.label,
          科目编码: item.accountCode,
          研发费用: item.amount,
        })),
      ),
      '费用类别汇总',
    )

    XLSX.utils.book_append_sheet(
      workbook,
      XLSX.utils.json_to_sheet([
        {
          规则版本: reconciliation.value.policyVersion,
          规则名称: reconciliation.value.policyLabel,
          辅助账合计: reconciliation.value.ledgerTotal,
          总账合计: reconciliation.value.generalLedgerTotal,
          差异: reconciliation.value.difference,
          勾稽结果: reconciliation.value.isReconciled ? '一致' : '不一致',
          申报预审: reconciliation.value.policyStatus === 'suggested' ? '建议纳入复核' : '待复核',
          说明: reconciliation.value.policyDisclaimer,
          申报地区: reconciliation.value.region || '',
          申报批次: reconciliation.value.programBatch || '',
        },
      ]),
      '勾稽与政策口径',
    )

    if (reconciliation.value.evidenceIssues.length) {
      XLSX.utils.book_append_sheet(
        workbook,
        XLSX.utils.json_to_sheet(
          reconciliation.value.evidenceIssues.map((item) => ({
            项目编号: item.projectCode,
            凭证号: item.voucherNo || '',
            问题类型: item.type,
            问题说明: item.message,
            金额: item.amount,
            账目ID: item.entryId,
          })),
        ),
        '证据缺口清单',
      )
    }

    if (evidenceDocuments.value.length) {
      XLSX.utils.book_append_sheet(
        workbook,
        XLSX.utils.json_to_sheet(
          evidenceDocuments.value.map((item) => ({
            项目ID: item.projectId,
            账目ID: item.entryId || '',
            资料类型: item.documentType,
            资料编号: item.referenceNo || '',
            文件链接: item.fileUrl || '',
            状态: item.status,
            备注: item.notes || '',
            登记时间: item.createdAt,
          })),
        ),
        '证据资料清单',
      )
    }
  }

  saveWorkbook(
    workbook,
    `研发费用辅助账_${dayjs(period.value[0]).format('YYYYMMDD')}_${dayjs(period.value[1]).format('YYYYMMDD')}.xlsx`,
  )
}

onMounted(load)
usePageRefresh(load)
</script>

<template>
  <div class="rd-page">
    <!-- 页头 -->
    <div class="page-header">
      <div class="title-section">
        <h2>研发费用</h2>
        <p class="subtitle">
          按研发项目和费用类别精细归集，辅助账与已过账总账权威勾稽，赋能加计扣除与科技项目申报。
        </p>
      </div>
      <div class="header-actions">
        <NButton secondary @click="openEvidence()">
          <template #icon><i class="ri-file-add-line" /></template>
          登记研发证据
        </NButton>
        <NButton secondary @click="exportLedger">
          <template #icon><i class="ri-download-2-line" /></template>
          导出台账
        </NButton>
        <NButton type="primary" @click="openCreateProject">
          <template #icon><i class="ri-add-line" /></template>
          新建研发项目
        </NButton>
      </div>
    </div>

    <!-- 顶部统计概览 -->
    <NGrid :cols="4" :x-gap="16" class="stats-grid">
      <NGridItem>
        <div class="stat-box">
          <div class="stat-icon-wrapper val-icon">
            <i class="ri-money-cny-box-line" />
          </div>
          <div class="stat-content">
            <span class="stat-label">期间研发费用</span>
            <span class="stat-value font-mono">{{ formatMoney(total) }}</span>
          </div>
        </div>
      </NGridItem>
      <NGridItem>
        <div class="stat-box clickable" @click="onTabChange('projects')">
          <div class="stat-icon-wrapper prj-icon">
            <i class="ri-folder-open-line" />
          </div>
          <div class="stat-content">
            <span class="stat-label">研发项目总数</span>
            <span class="stat-value">{{ projects.length }} <span class="unit">个</span><span v-if="activeCount" class="stat-sub-text"> ({{ activeCount }} 进行中)</span></span>
          </div>
        </div>
      </NGridItem>
      <NGridItem>
        <div class="stat-box clickable" @click="onTabChange('unallocated')">
          <div class="stat-icon-wrapper" :class="unallocatedItems.length > 0 ? 'warn-icon' : 'ok-icon'">
            <i :class="unallocatedItems.length > 0 ? 'ri-alert-line' : 'ri-checkbox-circle-line'" />
          </div>
          <div class="stat-content">
            <span class="stat-label">待补充归集</span>
            <span class="stat-value" :class="{ 'text-warn': unallocatedItems.length > 0 }">
              {{ unallocatedItems.length }} <span class="unit">笔</span>
            </span>
          </div>
        </div>
      </NGridItem>
      <NGridItem>
        <div class="stat-box clickable" @click="onTabChange('reconciliation')">
          <div
            class="stat-icon-wrapper"
            :class="reconciliation?.isReconciled ? 'ok-icon' : 'err-icon'"
          >
            <i :class="reconciliation?.isReconciled ? 'ri-scales-3-line' : 'ri-error-warning-line'" />
          </div>
          <div class="stat-content">
            <span class="stat-label">总账勾稽状态</span>
            <span
              class="stat-value stat-value--text"
              :class="reconciliation?.isReconciled ? 'text-green' : 'text-warn'"
            >
              {{
                reconciliation
                  ? reconciliation.isReconciled
                    ? '勾稽一致'
                    : `差异 ${formatMoney(reconciliation.difference)}`
                  : '核算中'
              }}
            </span>
          </div>
        </div>
      </NGridItem>
    </NGrid>

    <!-- 分段标签导航栏 -->
    <div class="tabs-bar">
      <NTabs :value="activeTab" type="segment" class="rd-tabs" @update:value="onTabChange">
        <NTab name="ledger">
          研发辅助账 ({{ ledgerItems.length }})
        </NTab>
        <NTab name="projects">
          研发项目 ({{ projects.length }})
        </NTab>
        <NTab name="unallocated">
          待补充归集
          <NBadge
            v-if="unallocatedItems.length"
            :value="unallocatedItems.length"
            :max="99"
            style="margin-left: 6px;"
          />
        </NTab>
        <NTab name="reconciliation">
          总账勾稽与证据
          <span v-if="reconciliation && !reconciliation.isReconciled" class="diff-indicator" />
        </NTab>
      </NTabs>
    </div>

    <!-- 子标签页切换 -->
    <RdLedgerTab
      v-show="activeTab === 'ledger'"
      :items="ledgerItems"
      :total="total"
      :loading="loading"
      :projects="projects"
      :selected-project="selectedProject"
      :period="period"
      :application-region="applicationRegion"
      :application-batch="applicationBatch"
      @update:selected-project="selectedProject = $event"
      @update:period="period = $event"
      @update:application-region="applicationRegion = $event"
      @update:application-batch="applicationBatch = $event"
      @search="load"
      @export="exportLedger"
    />

    <RdProjectsTab
      v-show="activeTab === 'projects'"
      :projects="projects"
      :loading="loading"
      @create="openCreateProject"
      @edit="openEditProject"
      @add-evidence="openEvidence($event)"
      @view-ledger="viewProjectLedger"
      @refresh="load"
    />

    <RdUnallocatedTab
      v-show="activeTab === 'unallocated'"
      :items="unallocatedItems"
      :loading="loading"
      @correct="openCorrection"
    />

    <RdReconciliationTab
      v-show="activeTab === 'reconciliation'"
      :reconciliation="reconciliation"
      :evidence-documents="evidenceDocuments"
      :evidence-types="evidenceTypes"
      :projects="projects"
      :loading="loading"
      @open-evidence="openEvidence($event)"
    />

    <!-- 弹窗组件 -->
    <RdProjectModal
      v-model:show="showProjectModal"
      :project="editingProject"
      @success="load"
    />

    <RdCorrectionModal
      v-model:show="showCorrectionModal"
      :target="correctionTarget"
      :projects="projects"
      :expense-types="expenseTypes"
      @success="load"
    />

    <RdEvidenceModal
      v-model:show="showEvidenceModal"
      :default-project-id="evidenceDefaultProject"
      :default-entry-id="evidenceDefaultEntry"
      :projects="projects"
      :evidence-types="evidenceTypes"
      @success="load"
    />
  </div>
</template>

<style scoped>
.rd-page {
  max-width: 1240px;
  margin: 0 auto;
  padding: 16px 0 48px 0;
  width: 100%;
  box-sizing: border-box;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  margin-bottom: 24px;
}
.title-section {
  min-width: 0;
  flex: 1;
}
.title-section h2 {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
  color: var(--solo-text-primary);
  letter-spacing: -0.5px;
}
.subtitle {
  margin: 4px 0 0 0;
  font-size: 13.5px;
  color: var(--solo-text-secondary);
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
  white-space: nowrap;
}
.stats-grid {
  margin-bottom: 24px;
}
.stat-box {
  background: var(--solo-bg-card);
  backdrop-filter: blur(12px);
  border: 1px solid var(--solo-border-glass);
  border-radius: var(--solo-radius-lg, 12px);
  padding: 16px 20px;
  display: flex;
  align-items: center;
  gap: 16px;
  box-shadow: var(--solo-shadow-sm);
  transition: all 0.25s ease;
}
.stat-box.clickable {
  cursor: pointer;
}
.stat-box.clickable:hover {
  transform: translateY(-2px);
  box-shadow: var(--solo-shadow-md);
  border-color: var(--solo-primary-light);
}
.stat-icon-wrapper {
  width: 44px;
  height: 44px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  flex-shrink: 0;
}
.val-icon { background: rgba(99, 102, 241, 0.1); color: var(--solo-primary); }
.prj-icon { background: rgba(100, 116, 139, 0.1); color: var(--solo-text-secondary); }
.warn-icon { background: rgba(245, 158, 11, 0.1); color: var(--solo-warning); }
.ok-icon { background: rgba(16, 185, 129, 0.1); color: var(--solo-income); }
.err-icon { background: rgba(239, 68, 68, 0.1); color: var(--solo-expense); }

.stat-content {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.stat-label {
  font-size: 12px;
  color: var(--solo-text-muted);
  font-weight: 500;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.stat-value {
  font-size: 19px;
  font-weight: 850;
  color: var(--solo-text-primary);
  font-family: 'Outfit', 'Inter', sans-serif;
  line-height: 1.1;
  white-space: nowrap;
}
.stat-value--text {
  font-size: 16px;
  font-family: inherit;
  font-weight: 700;
}
.stat-value .unit {
  font-size: 12px;
  color: var(--solo-text-muted);
  font-weight: 500;
}
.text-warn {
  color: var(--solo-warning);
}
.text-green {
  color: var(--solo-income);
}
.font-mono {
  font-family: 'Outfit', 'Inter', monospace;
  font-variant-numeric: tabular-nums;
}
.tabs-bar {
  margin-bottom: 20px;
  display: flex;
  justify-content: flex-start;
}
.rd-tabs {
  max-width: 520px;
}
.diff-indicator {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--solo-expense);
  margin-left: 6px;
  vertical-align: middle;
}
@media (max-width: 900px) {
  .page-header {
    flex-direction: column;
    align-items: flex-start;
  }
  .stats-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr)) !important;
  }
}
</style>
