<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, watch } from 'vue'
import { NImage, NTabs, NTabPane } from 'naive-ui'
import type { AgentActionItem } from '@/api/agent'
import AgentActionCard from '@/components/business/AgentActionCard.vue'

interface ReceiptPreview {
  url: string
  name: string
  receipt?: Record<string, unknown>
}

const props = defineProps<{
  actions: AgentActionItem[]
  activeReceipt?: ReceiptPreview | null
  monthlyIncome: number
  monthlyExpense: number
  netProfit: number
}>()

const emit = defineEmits<{
  (e: 'resolved'): void
  (e: 'quickAsk', prompt: string): void
  (e: 'close'): void
}>()

// ponytail: smart tab selection - default to pending review when items exist, else financial snapshot
const activeTab = ref<'actions' | 'snapshot'>('actions')

watch(
  () => props.actions.length,
  (newLen) => {
    if (newLen > 0 && activeTab.value !== 'actions') {
      activeTab.value = 'actions'
    }
  },
)

const formatMoney = (val: number) =>
  Number(val || 0).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })

const SNAPSHOT_PROMPTS = [
  '分析这个月的收支结构和主要开支',
  '检查是否有未对账的银行流水？',
  '测算本季度小规模纳税人增值税与附加税',
]
</script>

<template>
  <div class="evidence-board">
    <div class="board-header">
      <NTabs v-model:value="activeTab" type="segment" size="small" class="board-tabs">
        <NTabPane name="actions">
          <template #tab>
            <div class="tab-label">
              <span>待确认草稿</span>
              <span v-if="actions.length > 0" class="tab-badge">{{ actions.length }}</span>
            </div>
          </template>
        </NTabPane>
        <NTabPane name="snapshot" tab="经营快照" />
      </NTabs>
      <button type="button" class="close-btn" title="收起工作板" @click="emit('close')">
        <i class="ri-close-line" />
      </button>
    </div>

    <div class="board-body">
      <!-- 视图 1：待确认动作与单据原件核对 -->
      <div v-show="activeTab === 'actions'" class="tab-content actions-view">
        <!-- 发票/单据原件核对区 -->
        <div v-if="activeReceipt?.url" class="receipt-preview-card">
          <div class="card-title-bar">
            <span class="preview-title">
              <i class="ri-file-text-line" /> 单据原件对照
            </span>
            <span class="preview-name" :title="activeReceipt.name">{{ activeReceipt.name }}</span>
          </div>
          <div class="preview-image-container">
            <NImage
              :src="activeReceipt.url"
              :alt="activeReceipt.name"
              class="receipt-image"
              object-fit="contain"
              show-toolbar-tooltip
            />
          </div>
          <div v-if="activeReceipt.receipt" class="receipt-ocr-summary">
            <div class="ocr-tag-row">
              <span v-if="activeReceipt.receipt.sellerName || activeReceipt.receipt.merchant" class="ocr-tag">
                商户: {{ activeReceipt.receipt.sellerName || activeReceipt.receipt.merchant }}
              </span>
              <span v-if="activeReceipt.receipt.amount != null" class="ocr-tag amount">
                金额: ¥ {{ formatMoney(Number(activeReceipt.receipt.amount)) }}
              </span>
              <span v-if="activeReceipt.receipt.invoiceType" class="ocr-tag">
                {{ activeReceipt.receipt.invoiceType }}
              </span>
            </div>
          </div>
        </div>

        <!-- 待确认动作清单 -->
        <div class="actions-container">
          <div v-if="actions.length > 0" class="actions-list">
            <AgentActionCard
              v-for="action in actions"
              :key="action.id"
              :action="action"
              @resolved="emit('resolved')"
            />
          </div>
          <div v-else class="empty-actions">
            <div class="empty-icon"><i class="ri-checkbox-circle-line" /></div>
            <p class="empty-title">当前没有待确认的草稿</p>
            <p class="empty-desc">
              你可以对 AI 说“记一笔昨天打车 58 元”，或拍一张发票，AI 生成草稿后将在此处供你核对确认。
            </p>
          </div>
        </div>
      </div>

      <!-- 视图 2：当前账期经营快照 -->
      <div v-show="activeTab === 'snapshot'" class="tab-content snapshot-view">
        <div class="financial-pulse-card">
          <div class="pulse-header">
            <span class="pulse-title"><i class="ri-funds-line" /> 本月财务脉搏</span>
            <span class="pulse-subtitle">实时账目汇总</span>
          </div>

          <div class="metrics-grid">
            <div class="metric-item income">
              <span class="metric-label">本月营业收入</span>
              <span class="metric-val">¥ {{ formatMoney(monthlyIncome) }}</span>
            </div>
            <div class="metric-item expense">
              <span class="metric-label">本月各项支出</span>
              <span class="metric-val">¥ {{ formatMoney(monthlyExpense) }}</span>
            </div>
            <div class="metric-item profit" :class="{ negative: netProfit < 0 }">
              <span class="metric-label">净经营收益</span>
              <span class="metric-val">
                {{ netProfit >= 0 ? '+' : '' }}¥ {{ formatMoney(netProfit) }}
              </span>
            </div>
          </div>
        </div>

        <div class="quick-prompts-card">
          <div class="card-title"><i class="ri-sparkling-fill" /> 财务智能探查</div>
          <p class="card-desc">针对当前账期，让 AI 会计深度分析：</p>
          <div class="prompt-chips">
            <button
              v-for="p in SNAPSHOT_PROMPTS"
              :key="p"
              type="button"
              class="board-prompt-chip"
              @click="emit('quickAsk', p)"
            >
              <span>{{ p }}</span>
              <i class="ri-arrow-right-line" />
            </button>
          </div>
        </div>

        <div class="compliance-tip-card">
          <div class="tip-header">
            <i class="ri-shield-check-line" />
            <span>合规与审计提示</span>
          </div>
          <p class="tip-text">
            根据《小企业会计准则》，所有入账草稿需核验发票代码及银行真实付款流水。申报期前请确保完成对账与凭证计提。
          </p>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.evidence-board {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: #ffffff;
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: var(--solo-radius-lg, 12px);
  overflow: hidden;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}

.board-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  background: var(--solo-bg-page, #f6f7f9);
  border-bottom: 1px solid var(--solo-border, #dde3ea);
  gap: 8px;
}

.board-tabs {
  flex: 1;
}

.tab-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.tab-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  font-size: 11px;
  font-weight: 600;
  line-height: 1;
  border-radius: 9px;
  background: var(--solo-primary, #165dff);
  color: #ffffff;
}

.close-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 4px;
  color: var(--solo-text-muted, #8a95a3);
  font-size: 18px;
  line-height: 1;
  border-radius: 4px;
  transition: all 0.2s;
}

.close-btn:hover {
  background: rgba(0, 0, 0, 0.06);
  color: var(--solo-text-primary, #182230);
}

.board-body {
  flex: 1;
  overflow-y: auto;
  padding: 14px;
  display: flex;
  flex-direction: column;
}

.tab-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 发票单据预览 */
.receipt-preview-card {
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: var(--solo-radius, 8px);
  background: #ffffff;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.card-title-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
}

.preview-title {
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.preview-name {
  color: var(--solo-text-muted, #8a95a3);
  max-width: 160px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.preview-image-container {
  max-height: 180px;
  background: var(--solo-bg-page, #f6f7f9);
  border-radius: 6px;
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}

.receipt-image :deep(img) {
  max-height: 180px;
  width: auto;
  object-fit: contain;
}

.receipt-ocr-summary {
  font-size: 11.5px;
}

.ocr-tag-row {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.ocr-tag {
  background: var(--solo-bg-blue-soft, #edf3ff);
  color: var(--solo-primary, #165dff);
  padding: 2px 6px;
  border-radius: 4px;
}

.ocr-tag.amount {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

/* 待办列表 */
.actions-container {
  display: flex;
  flex-direction: column;
}

.actions-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.empty-actions {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  padding: 36px 16px;
  color: var(--solo-text-muted, #8a95a3);
}

.empty-icon {
  font-size: 36px;
  color: var(--solo-income, #12805c);
  margin-bottom: 8px;
}

.empty-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
  margin: 0 0 6px 0;
}

.empty-desc {
  font-size: 12px;
  line-height: 1.6;
  max-width: 280px;
  margin: 0;
}

/* 快照视图 */
.financial-pulse-card {
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: var(--solo-radius, 8px);
  padding: 12px;
  background: #ffffff;
}

.pulse-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}

.pulse-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
  display: inline-flex;
  align-items: center;
  gap: 5px;
}

.pulse-subtitle {
  font-size: 11px;
  color: var(--solo-text-muted, #8a95a3);
}

.metrics-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: 8px;
}

.metric-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 10px;
  background: var(--solo-bg-page, #f6f7f9);
  border-radius: 6px;
}

.metric-label {
  font-size: 12px;
  color: var(--solo-text-secondary, #586474);
}

.metric-val {
  font-size: 14px;
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-primary, #182230);
}

.metric-item.income .metric-val {
  color: var(--solo-income, #12805c);
}

.metric-item.expense .metric-val {
  color: var(--solo-expense, #c74444);
}

.metric-item.profit .metric-val {
  color: var(--solo-income, #12805c);
}

.metric-item.profit.negative .metric-val {
  color: var(--solo-expense, #c74444);
}

.quick-prompts-card {
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: var(--solo-radius, 8px);
  padding: 12px;
  background: #ffffff;
}

.quick-prompts-card .card-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-primary, #165dff);
  display: flex;
  align-items: center;
  gap: 5px;
}

.quick-prompts-card .card-desc {
  font-size: 11.5px;
  color: var(--solo-text-muted, #8a95a3);
  margin: 4px 0 8px 0;
}

.prompt-chips {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.board-prompt-chip {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 10px;
  border-radius: 6px;
  border: 1px solid var(--solo-border, #dde3ea);
  background: var(--solo-bg-page, #f6f7f9);
  color: var(--solo-text-primary, #182230);
  font-size: 12px;
  text-align: left;
  cursor: pointer;
  transition: all 0.2s;
}

.board-prompt-chip:hover {
  border-color: var(--solo-primary, #165dff);
  color: var(--solo-primary, #165dff);
  background: #ffffff;
}

.compliance-tip-card {
  background: var(--solo-bg-soft, #f1f4f8);
  border-radius: var(--solo-radius, 8px);
  padding: 10px 12px;
  font-size: 11.5px;
}

.tip-header {
  display: flex;
  align-items: center;
  gap: 5px;
  font-weight: 600;
  color: var(--solo-text-secondary, #586474);
  margin-bottom: 4px;
}

.tip-text {
  margin: 0;
  color: var(--solo-text-muted, #8a95a3);
  line-height: 1.5;
}
</style>
