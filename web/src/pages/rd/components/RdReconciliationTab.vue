<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { useRouter } from 'vue-router'
import {
  NAlert, NButton, NCard, NGrid, NGridItem, NSpin, NTag,
} from 'naive-ui'
import type { RdEvidenceDocument, RdReconciliationSummary } from '@/api/rd'
import type { RdProject } from '@/api/types'
import { formatMoney } from '@/utils/format'
import { SoloEmpty } from '@/components/ui'

const props = defineProps<{
  reconciliation: RdReconciliationSummary | null
  evidenceDocuments: RdEvidenceDocument[]
  evidenceTypes: Array<{ value: string; label: string }>
  projects: RdProject[]
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'openEvidence', projectId?: string, entryId?: string): void
}>()

const router = useRouter()

function evidenceTypeLabel(type: string) {
  return props.evidenceTypes.find((item) => item.value === type)?.label || type
}

function projectLabel(projectId: string) {
  const p = props.projects.find((item) => item.id === projectId)
  return p ? `${p.code} · ${p.name}` : projectId || '全账套通用'
}

function navigateToVoucher(entryId: string) {
  if (entryId) {
    router.push(`/vouchers/${entryId}`)
  }
}
</script>

<template>
  <div class="rd-reconciliation-tab">
    <NSpin :show="loading">
      <!-- 勾稽核对卡 -->
      <NCard v-if="reconciliation" :bordered="false" class="recon-card mb-4">
        <div class="recon-header">
          <div class="header-title">
            <h3>总账勾稽与申报预审</h3>
            <p>基于复式记账总账与研发项目辅助账自动核算比对，为所得税加计扣除与科技项目申报提供可信核算底座。</p>
          </div>
          <NButton type="primary" secondary @click="emit('openEvidence')">
            <template #icon><i class="ri-file-add-line" /></template>
            登记研发证据
          </NButton>
        </div>

        <NAlert
          :type="reconciliation.isReconciled ? 'success' : 'error'"
          :bordered="false"
          class="recon-alert mb-4"
        >
          <div class="alert-row">
            <i :class="reconciliation.isReconciled ? 'ri-checkbox-circle-fill' : 'ri-close-circle-fill'" class="recon-icon" />
            <div class="alert-desc">
              <span>
                辅助账合计 <strong>{{ formatMoney(reconciliation.ledgerTotal) }}</strong>，
                4301 / 5301 系列总账 <strong>{{ formatMoney(reconciliation.generalLedgerTotal) }}</strong>，
                差异 <strong>{{ formatMoney(reconciliation.difference) }}</strong>。
              </span>
              <span>
                {{
                  reconciliation.isReconciled
                    ? '研发辅助账与财务总账完全勾稽一致，账账相符。'
                    : '检测到辅助账与总账存在差额，可能存在未归集凭证或手工凭证科目不匹配，暂不可直接用于最终申报。'
                }}
              </span>
            </div>
          </div>
        </NAlert>

        <!-- KPI 指标网格 -->
        <NGrid :cols="4" :x-gap="12" class="kpi-grid mb-4">
          <NGridItem>
            <div class="kpi-item">
              <span class="kpi-label">口径规则版本</span>
              <span class="kpi-value font-mono">{{ reconciliation.policyVersion }}</span>
              <span class="kpi-hint">{{ reconciliation.policyLabel }}</span>
            </div>
          </NGridItem>
          <NGridItem>
            <div class="kpi-item">
              <span class="kpi-label">材料预审结论</span>
              <span class="kpi-value" :class="reconciliation.policyStatus === 'suggested' ? 'text-green' : 'text-warning'">
                {{ reconciliation.policyStatus === 'suggested' ? '建议纳入复核' : '待补材料/人工复核' }}
              </span>
              <span class="kpi-hint">基于证据链完备度初筛</span>
            </div>
          </NGridItem>
          <NGridItem>
            <div class="kpi-item">
              <span class="kpi-label">证据链缺口</span>
              <span class="kpi-value font-mono" :class="reconciliation.evidenceIssues.length ? 'text-warning' : 'text-green'">
                {{ reconciliation.evidenceIssues.length }} <span class="unit">项</span>
              </span>
              <span class="kpi-hint">缺少发票/合同/工时依据</span>
            </div>
          </NGridItem>
          <NGridItem>
            <div class="kpi-item">
              <span class="kpi-label">待补充归集凭证</span>
              <span class="kpi-value font-mono" :class="reconciliation.unallocatedCount ? 'text-warning' : 'text-green'">
                {{ reconciliation.unallocatedCount }} <span class="unit">笔</span>
              </span>
              <span class="kpi-hint">需补充项目或费用分类</span>
            </div>
          </NGridItem>
        </NGrid>

        <p class="policy-disclaimer">
          <i class="ri-information-line" />
          {{ reconciliation.policyDisclaimer }}
        </p>
      </NCard>

      <!-- 证据缺口清单 -->
      <NCard
        v-if="reconciliation?.evidenceIssues.length"
        :bordered="false"
        title="证据缺口清单（需补充佐证资料）"
        class="table-card mb-4"
      >
        <div class="table-wrap">
          <table class="solo-table">
            <thead>
              <tr>
                <th style="width: 140px;">研发项目</th>
                <th style="width: 140px;">凭证号</th>
                <th style="width: 120px;">问题类型</th>
                <th>问题说明</th>
                <th style="width: 130px;" class="amount">涉及金额</th>
                <th style="width: 110px;" class="action-th">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="issue in reconciliation.evidenceIssues" :key="`${issue.entryId}-${issue.type}`">
                <td class="font-mono project-code">{{ issue.projectCode }}</td>
                <td>
                  <NButton
                    v-if="issue.voucherNo"
                    text
                    type="primary"
                    size="small"
                    class="voucher-link"
                    @click="navigateToVoucher(issue.entryId)"
                  >
                    <i class="ri-file-text-line" style="margin-right: 4px;" />
                    {{ issue.voucherNo }}
                  </NButton>
                  <span v-else class="text-muted">-</span>
                </td>
                <td>
                  <NTag size="small" type="warning" :bordered="false">{{ issue.type }}</NTag>
                </td>
                <td class="cell-desc">{{ issue.message }}</td>
                <td class="amount font-mono amount-cell">{{ formatMoney(issue.amount) }}</td>
                <td class="action-td">
                  <NButton
                    size="small"
                    type="primary"
                    secondary
                    @click="emit('openEvidence', issue.projectId, issue.entryId)"
                  >
                    登记资料
                  </NButton>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </NCard>

      <!-- 证据资料库 -->
      <NCard :bordered="false" class="table-card">
        <div class="evidence-header mb-4">
          <div class="header-left">
            <h4 class="evidence-title">研发证据资料库</h4>
            <span class="evidence-count">已归档 <strong>{{ evidenceDocuments.length }}</strong> 份证据材料</span>
          </div>
          <NButton size="small" secondary @click="emit('openEvidence')">
            <template #icon><i class="ri-add-line" /></template>
            登记新资料
          </NButton>
        </div>

        <div v-if="evidenceDocuments.length" class="table-wrap">
          <table class="solo-table">
            <thead>
              <tr>
                <th style="width: 220px;">关联研发项目</th>
                <th style="width: 140px;">资料类型</th>
                <th style="width: 200px;">资料编号 / 文件链接</th>
                <th style="width: 140px;">关联账目 ID</th>
                <th style="width: 110px;">状态</th>
                <th>备注</th>
                <th style="width: 160px;">登记时间</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="doc in evidenceDocuments" :key="doc.id">
                <td>
                  <span class="project-name">{{ projectLabel(doc.projectId) }}</span>
                </td>
                <td>
                  <NTag size="small" :bordered="false" type="info">
                    {{ evidenceTypeLabel(doc.documentType) }}
                  </NTag>
                </td>
                <td>
                  <a
                    v-if="doc.fileUrl"
                    :href="doc.fileUrl"
                    target="_blank"
                    rel="noopener"
                    class="file-link"
                  >
                    <i class="ri-link" style="margin-right: 4px;" />
                    {{ doc.referenceNo || '查看材料文件' }}
                  </a>
                  <span v-else class="reference-no">{{ doc.referenceNo || '-' }}</span>
                </td>
                <td class="font-mono text-muted">
                  {{ doc.entryId ? doc.entryId.slice(0, 16) : '项目级文件' }}
                </td>
                <td>
                  <NTag
                    size="small"
                    :type="doc.status === 'available' ? 'success' : doc.status === 'pending_review' ? 'warning' : 'error'"
                    :bordered="false"
                  >
                    {{ doc.status === 'available' ? '可用' : doc.status === 'pending_review' ? '待复核' : '缺失' }}
                  </NTag>
                </td>
                <td class="cell-notes" :title="doc.notes">{{ doc.notes || '-' }}</td>
                <td class="font-mono text-muted text-sm">{{ doc.createdAt }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <SoloEmpty
          v-else
          variant="hint"
          title="尚未登记研发证据资料"
          description="立项文件、工时单、采购合同等证明材料齐全，研发费用加计扣除和科技专项申报才经得起税审与专项审计。"
        />
      </NCard>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 {
  margin-bottom: 16px;
}
.recon-card,
.table-card {
  border-radius: var(--solo-radius-lg, 12px);
  background: var(--solo-bg-card);
}
.recon-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 16px;
}
.header-title h3 {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  color: var(--solo-text-primary);
}
.header-title p {
  margin: 4px 0 0 0;
  font-size: 13px;
  color: var(--solo-text-secondary);
}
.recon-alert {
  border-radius: 8px;
}
.alert-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.recon-icon {
  font-size: 20px;
}
.alert-desc {
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-size: 13px;
  line-height: 1.5;
}
.kpi-grid {
  margin-top: 14px;
}
.kpi-item {
  padding: 12px 14px;
  background: var(--solo-bg-page);
  border: 1px solid var(--solo-border);
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.kpi-label {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.kpi-value {
  font-size: 18px;
  font-weight: 700;
  color: var(--solo-text-primary);
}
.kpi-value .unit {
  font-size: 12px;
  color: var(--solo-text-muted);
  font-weight: 500;
}
.kpi-hint {
  font-size: 11px;
  color: var(--solo-text-secondary);
}
.text-green {
  color: var(--solo-income);
}
.text-warning {
  color: var(--solo-warning);
}
.policy-disclaimer {
  font-size: 12px;
  color: var(--solo-text-muted);
  line-height: 1.6;
  margin: 12px 0 0 0;
  display: flex;
  align-items: center;
  gap: 6px;
}
.evidence-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.evidence-title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--solo-text-primary);
}
.evidence-count {
  font-size: 12px;
  color: var(--solo-text-muted);
  margin-left: 10px;
}
.table-wrap {
  overflow-x: auto;
}
.solo-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.solo-table th,
.solo-table td {
  padding: 12px 14px;
  border-bottom: 1px solid var(--solo-border-light);
  text-align: left;
  white-space: nowrap;
}
.solo-table th {
  color: var(--solo-text-secondary);
  font-weight: 600;
  background: var(--solo-bg-soft);
}
.amount {
  text-align: right;
}
.amount-cell {
  font-weight: 600;
}
.action-th,
.action-td {
  text-align: right;
}
.font-mono {
  font-family: 'Outfit', 'Inter', monospace;
  font-variant-numeric: tabular-nums;
}
.project-code {
  color: var(--solo-primary);
  font-weight: 600;
}
.voucher-link {
  font-family: 'Outfit', 'Inter', monospace;
  font-size: 13px;
  font-weight: 500;
}
.file-link {
  color: var(--solo-primary);
  text-decoration: none;
}
.file-link:hover {
  text-decoration: underline;
}
.reference-no {
  color: var(--solo-text-primary);
}
.cell-desc {
  max-width: 280px;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--solo-text-primary);
}
.cell-notes {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--solo-text-secondary);
}
.text-muted {
  color: var(--solo-text-muted);
}
.text-sm {
  font-size: 12px;
}
@media (max-width: 760px) {
  .recon-header {
    flex-direction: column;
    gap: 12px;
  }
}
</style>
