<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NTag, NSpace, NSpin, useMessage, NEmpty, NModal, NGrid, NGridItem, NProgress, NRadioGroup, NRadioButton } from 'naive-ui'
import { getFixedAssets, deleteFixedAsset, runDepreciation } from '@/api/asset'
import { formatMoney, formatDate } from '@/utils/format'
import type { FixedAsset } from '@/api/types'

const router = useRouter()
const message = useMessage()

const assets = ref<FixedAsset[]>([])
const loading = ref(true)
const deletingId = ref<string | null>(null)
const showDeleteConfirm = ref(false)
const deleteTarget = ref<FixedAsset | null>(null)
const depRunning = ref(false)
const depResult = ref<any>(null)
const showDepResult = ref(false)
const filterStatus = ref('all')

const statusConfig: Record<string, { label: string; type: 'success' | 'warning' | 'info' | 'error' }> = {
  in_use: { label: '使用中', type: 'success' },
  idle: { label: '闲置', type: 'warning' },
  disposed: { label: '已处置', type: 'info' },
  scrapped: { label: '已报废', type: 'error' },
}

async function load() {
  loading.value = true
  try {
    const resp = await getFixedAssets()
    assets.value = resp.assets ?? []
  } catch {
    message.error('加载资产列表失败')
  } finally {
    loading.value = false
  }
}

function confirmDelete(asset: FixedAsset) {
  deleteTarget.value = asset
  showDeleteConfirm.value = true
}

async function handleRunDepreciation() {
  depRunning.value = true
  try {
    const now = new Date()
    const period = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
    depResult.value = await runDepreciation(period)
    showDepResult.value = true
    await load()
  } catch {
    message.error('计提折旧失败')
  } finally {
    depRunning.value = false
  }
}

async function doDelete() {
  if (!deleteTarget.value) return
  deletingId.value = deleteTarget.value.id
  try {
    await deleteFixedAsset(deleteTarget.value.id)
    message.success('已删除')
    showDeleteConfirm.value = false
    await load()
  } catch {
    message.error('删除失败')
  } finally {
    deletingId.value = null
    deleteTarget.value = null
  }
}

// Stats computations
const totalOriginalValue = computed(() => assets.value.reduce((sum, a) => sum + (a.originalValue || 0), 0))
const totalAccumulatedDepreciation = computed(() => assets.value.reduce((sum, a) => sum + (a.accumulatedDepreciation || 0), 0))
const totalNetValue = computed(() => totalOriginalValue.value - totalAccumulatedDepreciation.value)
const totalCount = computed(() => assets.value.length)

// Filtered list
const filteredAssets = computed(() => {
  if (filterStatus.value === 'all') return assets.value
  if (filterStatus.value === 'disposed') {
    return assets.value.filter((a) => a.status === 'disposed' || a.status === 'scrapped')
  }
  return assets.value.filter((a) => a.status === filterStatus.value)
})

function getDepreciationPercentage(asset: FixedAsset): number {
  if (!asset.originalValue) return 0
  const pct = (asset.accumulatedDepreciation / asset.originalValue) * 100
  return Math.min(100, Math.max(0, Math.round(pct)))
}

function isFullyDepreciated(asset: FixedAsset): boolean {
  const limit = asset.originalValue * (1 - asset.residualValueRate)
  return asset.accumulatedDepreciation >= limit - 0.01
}

onMounted(load)
</script>

<template>
  <div class="asset-page">
    <div class="page-header">
      <div class="title-section">
        <h2>固定资产</h2>
        <p class="subtitle">管理企业设备、器具等固定资产，月度自动计提折旧并生成凭证</p>
      </div>
      <NSpace>
        <NButton secondary type="primary" :loading="depRunning" @click="handleRunDepreciation">
          <template #icon><i class="ri-calculator-line" /></template>
          一键计提本月折旧
        </NButton>
        <NButton type="primary" @click="router.push('/assets/create')">
          <template #icon><i class="ri-add-line" /></template>
          录入固定资产
        </NButton>
      </NSpace>
    </div>

    <!-- Stats Dashboard Header -->
    <NGrid :cols="4" :x-gap="16" class="stats-grid">
      <NGridItem>
        <div class="stat-box">
          <div class="stat-icon-wrapper val-icon">
            <i class="ri-money-cny-box-line" />
          </div>
          <div class="stat-content">
            <span class="stat-label">固定资产总值</span>
            <span class="stat-value">{{ formatMoney(totalOriginalValue) }}</span>
          </div>
        </div>
      </NGridItem>
      <NGridItem>
        <div class="stat-box">
          <div class="stat-icon-wrapper dep-icon">
            <i class="ri-line-chart-line" />
          </div>
          <div class="stat-content">
            <span class="stat-label">累计折旧总额</span>
            <span class="stat-value text-muted-value">{{ formatMoney(totalAccumulatedDepreciation) }}</span>
          </div>
        </div>
      </NGridItem>
      <NGridItem>
        <div class="stat-box">
          <div class="stat-icon-wrapper net-icon">
            <i class="ri-shield-check-line" />
          </div>
          <div class="stat-content">
            <span class="stat-label">资产账面净值</span>
            <span class="stat-value text-green">{{ formatMoney(totalNetValue) }}</span>
          </div>
        </div>
      </NGridItem>
      <NGridItem>
        <div class="stat-box">
          <div class="stat-icon-wrapper qty-icon">
            <i class="ri-database-line" />
          </div>
          <div class="stat-content">
            <span class="stat-label">在册资产数量</span>
            <span class="stat-value">{{ totalCount }} <span class="unit">台</span></span>
          </div>
        </div>
      </NGridItem>
    </NGrid>

    <!-- Category Filters -->
    <div class="filter-section">
      <NRadioGroup v-model:value="filterStatus" size="medium" class="filter-group">
        <NRadioButton value="all">全部资产 ({{ totalCount }})</NRadioButton>
        <NRadioButton value="in_use">使用中 ({{ assets.filter(a => a.status === 'in_use').length }})</NRadioButton>
        <NRadioButton value="idle">闲置 ({{ assets.filter(a => a.status === 'idle').length }})</NRadioButton>
        <NRadioButton value="disposed">已处置/报废 ({{ assets.filter(a => a.status === 'disposed' || a.status === 'scrapped').length }})</NRadioButton>
      </NRadioGroup>
    </div>

    <!-- Spin wrapper -->
    <NSpin :show="loading">
      <div v-if="filteredAssets.length === 0 && !loading" class="empty-section">
        <NEmpty description="暂无符合条件的固定资产记录">
          <template #icon>
            <div class="empty-icon-circle">
              <i class="ri-computer-line" />
            </div>
          </template>
          <template #extra v-if="filterStatus === 'all'">
            <NButton size="small" type="primary" @click="router.push('/assets/create')">录入首笔资产</NButton>
          </template>
        </NEmpty>
      </div>

      <!-- Assets grid -->
      <div v-else class="asset-grid">
        <div
          v-for="asset in filteredAssets"
          :key="asset.id"
          class="asset-card-wrapper"
        >
          <div class="asset-card-body">
            <!-- Header: Title & Status -->
            <div class="asset-card-header">
              <div class="asset-title-section">
                <span class="asset-name">{{ asset.name }}</span>
                <span class="asset-category">{{ asset.category }}</span>
              </div>
              <NTag size="tiny" :bordered="false" :type="statusConfig[asset.status]?.type || 'default'" round>
                {{ statusConfig[asset.status]?.label || asset.status }}
              </NTag>
            </div>

            <!-- Money: Original Cost -->
            <div class="asset-cost-row">
              <span class="cost-label">资产原值</span>
              <span class="cost-value">{{ formatMoney(asset.originalValue) }}</span>
            </div>

            <!-- Depreciation Progress -->
            <div class="progress-section">
              <div class="progress-header">
                <span class="progress-label">折旧进度</span>
                <span class="progress-percent" v-if="isFullyDepreciated(asset)">
                  <NTag size="tiny" type="warning" :bordered="false" class="done-tag">折旧完毕</NTag>
                </span>
                <span class="progress-percent-text" v-else>已折旧 {{ getDepreciationPercentage(asset) }}%</span>
              </div>
              <NProgress
                type="line"
                :percentage="getDepreciationPercentage(asset)"
                :show-indicator="false"
                status="success"
                class="dep-progress"
              />
            </div>

            <!-- Metadata Grid -->
            <div class="metadata-grid">
              <div class="meta-item">
                <span class="meta-label">购置日期</span>
                <span class="meta-value">{{ formatDate(asset.purchaseDate) }}</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">折旧年限</span>
                <span class="meta-value">{{ asset.usefulLife }} 年</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">残值率</span>
                <span class="meta-value">{{ (asset.residualValueRate * 100).toFixed(0) }}%</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">账面净值</span>
                <span class="meta-value net-value-text">{{ formatMoney(asset.originalValue - asset.accumulatedDepreciation) }}</span>
              </div>
            </div>

            <!-- Card Actions -->
            <div class="card-actions">
              <span class="depreciation-accumulated">
                累计折旧: {{ formatMoney(asset.accumulatedDepreciation) }}
              </span>
              <NButton text size="tiny" type="error" class="delete-btn" @click="confirmDelete(asset)">
                <template #icon><i class="ri-delete-bin-line" /></template>
                删除资产
              </NButton>
            </div>
          </div>
        </div>
      </div>
    </NSpin>

    <!-- Delete Modal -->
    <NModal v-model:show="showDeleteConfirm" title="确认删除" preset="card" style="width: 400px; border-radius: var(--solo-radius-lg);">
      <p>确定从系统中删除固定资产「<strong>{{ deleteTarget?.name }}</strong>」吗？此操作将永久移除该资产记录且不可撤销。</p>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showDeleteConfirm = false">取消</NButton>
          <NButton type="error" :loading="deletingId !== null" @click="doDelete">确认删除</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- Depreciation Result Modal -->
    <NModal v-model:show="showDepResult" title="折旧自动计提成功" preset="card" style="width: 480px; border-radius: var(--solo-radius-lg);">
      <div v-if="depResult" class="dep-result-content">
        <div class="result-success-header">
          <div class="success-icon-circle">
            <i class="ri-checkbox-circle-fill" />
          </div>
          <h3>计提任务已完成</h3>
          <p>已自动生成相应的复式记账折旧凭证并扣减账面价值</p>
        </div>

        <div class="result-details-card">
          <div class="result-detail-row">
            <span class="result-detail-label">计提会计期间</span>
            <span class="result-detail-val"><strong>{{ depResult.period }}</strong></span>
          </div>
          <div class="result-detail-row">
            <span class="result-detail-label">本次折旧总额</span>
            <span class="result-detail-val dep-amount-highlight">{{ formatMoney(depResult.total_depreciation) }}</span>
          </div>
          <div class="result-detail-row">
            <span class="result-detail-label">计提资产数量</span>
            <span class="result-detail-val">{{ depResult.processed_count }} 项</span>
          </div>
          <div class="result-detail-row" v-if="depResult.skipped_count > 0">
            <span class="result-detail-label">跳过资产（已折旧毕）</span>
            <span class="result-detail-val">{{ depResult.skipped_count }} 项</span>
          </div>
        </div>

        <div v-if="depResult.entries.length > 0" class="dep-entries-section">
          <h4>资产折旧明细表</h4>
          <div class="dep-entries-list">
            <div v-for="entry in depResult.entries" :key="entry.asset_id" class="dep-entry-row">
              <span class="dep-entry-name">{{ entry.asset_name }}</span>
              <span class="dep-entry-val">+ {{ formatMoney(entry.amount) }}</span>
            </div>
          </div>
        </div>
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton type="primary" @click="showDepResult = false">我知道了</NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.asset-page {
  max-width: 1080px;
  margin: 0 auto;
  padding: 16px 0 48px 0;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
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

/* Stats Dashboard */
.stats-grid {
  margin-bottom: 28px;
}
.stat-box {
  background: var(--solo-bg-card);
  backdrop-filter: blur(12px);
  border: 1px solid var(--solo-border-glass);
  border-radius: var(--solo-radius-lg);
  padding: 16px 20px;
  display: flex;
  align-items: center;
  gap: 16px;
  box-shadow: var(--solo-shadow-sm);
  transition: all 0.3s ease;
}
.stat-box:hover {
  transform: translateY(-2px);
  box-shadow: var(--solo-shadow-lg);
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
}
.val-icon {
  background: rgba(99, 102, 241, 0.1);
  color: var(--solo-primary);
}
.dep-icon {
  background: rgba(100, 116, 139, 0.1);
  color: var(--solo-text-secondary);
}
.net-icon {
  background: rgba(16, 185, 129, 0.1);
  color: var(--solo-income);
}
.qty-icon {
  background: rgba(245, 158, 11, 0.1);
  color: var(--solo-warning);
}
.stat-content {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.stat-label {
  font-size: 12px;
  color: var(--solo-text-muted);
  font-weight: 500;
}
.stat-value {
  font-size: 19px;
  font-weight: 850;
  color: var(--solo-text-primary);
  font-family: 'Outfit', 'Inter', sans-serif;
  line-height: 1.1;
  white-space: nowrap;
}
.stat-value .unit {
  font-size: 12px;
  color: var(--solo-text-muted);
  font-weight: 500;
}
.text-muted-value {
  color: var(--solo-text-secondary);
}
.text-green {
  color: var(--solo-income);
}

/* Category Filters */
.filter-section {
  margin-bottom: 24px;
  display: flex;
  justify-content: flex-start;
}
.filter-group {
  background: var(--solo-bg-card);
  padding: 4px;
  border-radius: var(--solo-radius);
  border: 1px solid var(--solo-border-glass);
}

/* Empty State */
.empty-section {
  padding: 64px 0;
  background: var(--solo-bg-card);
  border: 1px dashed var(--solo-border);
  border-radius: var(--solo-radius-lg);
  display: flex;
  justify-content: center;
}
.empty-icon-circle {
  width: 64px;
  height: 64px;
  border-radius: 50%;
  background: var(--solo-primary-light);
  color: var(--solo-primary);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 28px;
  margin: 0 auto 12px auto;
  opacity: 0.8;
}

/* Assets Grid */
.asset-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
  gap: 16px;
}
.asset-card-wrapper {
  background: var(--solo-bg-card);
  backdrop-filter: blur(12px);
  border: 1px solid var(--solo-border-glass);
  border-radius: var(--solo-radius-lg);
  box-shadow: var(--solo-shadow-sm);
  transition: all 0.25s ease;
  overflow: hidden;
}
.asset-card-wrapper:hover {
  transform: translateY(-2px);
  box-shadow: var(--solo-shadow-md);
  border-color: var(--solo-primary-light);
}
.asset-card-body {
  padding: 20px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

/* Card Header */
.asset-card-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.asset-title-section {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}
.asset-name {
  font-size: 16px;
  font-weight: 700;
  color: var(--solo-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.asset-category {
  font-size: 12px;
  color: var(--solo-text-muted);
}

/* Asset Cost Row */
.asset-cost-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 8px 12px;
  background: rgba(99, 102, 241, 0.03);
  border-radius: var(--solo-radius);
  border: 1px dashed rgba(99, 102, 241, 0.08);
}
.cost-label {
  font-size: 12px;
  color: var(--solo-text-secondary);
}
.cost-value {
  font-size: 18px;
  font-weight: 800;
  color: var(--solo-text-primary);
  font-family: 'Outfit', 'Inter', sans-serif;
}

/* Depreciation Progress */
.progress-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.progress-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.progress-label {
  font-size: 11.5px;
  color: var(--solo-text-secondary);
  font-weight: 500;
}
.progress-percent-text {
  font-size: 11.5px;
  color: var(--solo-income);
  font-weight: 700;
  font-family: 'Inter', monospace;
}
.done-tag {
  font-size: 10px;
  font-weight: 700;
}
.dep-progress {
  height: 6px;
}

/* Metadata Grid */
.metadata-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  padding: 8px 0;
  border-top: 1px solid var(--solo-border-light);
  border-bottom: 1px solid var(--solo-border-light);
}
.meta-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px 8px;
}
.meta-label {
  font-size: 11px;
  color: var(--solo-text-muted);
}
.meta-value {
  font-size: 12.5px;
  color: var(--solo-text-primary);
  font-weight: 600;
}
.net-value-text {
  color: var(--solo-income);
  font-family: 'Outfit', 'Inter', sans-serif;
}

/* Card Footer Actions */
.card-actions {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12px;
}
.depreciation-accumulated {
  color: var(--solo-text-muted);
  font-weight: 500;
}
.delete-btn {
  font-size: 12px;
  font-weight: 600;
}

/* Depreciation Modal Result styling */
.dep-result-content {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.result-success-header {
  text-align: center;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
}
.success-icon-circle {
  width: 56px;
  height: 56px;
  background: var(--solo-income-bg);
  color: var(--solo-income);
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 32px;
}
.result-success-header h3 {
  margin: 4px 0 0 0;
  font-size: 18px;
  font-weight: 700;
}
.result-success-header p {
  margin: 0;
  font-size: 13px;
  color: var(--solo-text-secondary);
}
.result-details-card {
  background: rgba(255, 255, 255, 0.4);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-lg);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.result-detail-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 13.5px;
}
.result-detail-label {
  color: var(--solo-text-secondary);
}
.result-detail-val {
  color: var(--solo-text-primary);
  font-weight: 600;
}
.dep-amount-highlight {
  font-size: 18px;
  font-weight: 800;
  color: var(--solo-primary);
  font-family: 'Outfit', 'Inter', sans-serif;
}
.dep-entries-section h4 {
  margin: 0 0 8px 0;
  font-size: 13.5px;
  color: var(--solo-text-secondary);
  font-weight: 750;
}
.dep-entries-list {
  max-height: 180px;
  overflow-y: auto;
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius);
  background: rgba(0, 0, 0, 0.01);
}
.dep-entry-row {
  display: flex;
  justify-content: space-between;
  padding: 10px 14px;
  border-bottom: 1px solid var(--solo-border-light);
  font-size: 13px;
}
.dep-entry-row:last-child {
  border-bottom: none;
}
.dep-entry-name {
  font-weight: 600;
  color: var(--solo-text-primary);
}
.dep-entry-val {
  font-weight: 700;
  color: var(--solo-primary);
  font-family: 'Outfit', 'Inter', sans-serif;
}
</style>
