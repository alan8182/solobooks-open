<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { usePageRefresh } from '@/composables/usePageRefresh'
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NTag, NSpace, NSpin, useMessage, NModal, NGrid, NGridItem, NProgress, NRadioGroup, NRadioButton, NSelect } from 'naive-ui'
import {
  getFixedAssets, getFixedAssetCategories, deleteFixedAsset, runFixedAssetDepreciationCatchUp,
  getFixedAssetReconciliation, exportFixedAssetLedger,
} from '@/api/asset'
import { formatMoney, formatDate } from '@/utils/format'
import type { DepreciationCatchUpResult, FixedAsset, FixedAssetCategory, FixedAssetReconciliation } from '@/api/types'
import { SoloEmpty } from '@/components/ui'

const router = useRouter()
const message = useMessage()

const assets = ref<FixedAsset[]>([])
const categories = ref<FixedAssetCategory[]>([])
const loading = ref(true)
const deletingId = ref<string | null>(null)
const showDeleteConfirm = ref(false)
const deleteTarget = ref<FixedAsset | null>(null)
const filterStatus = ref('all')
const filterCategory = ref<string | null>(null)

// 折旧流程：先预览（dryRun），确认后执行
const previewing = ref(false)
const running = ref(false)
const previewResult = ref<DepreciationCatchUpResult | null>(null)
const showPreview = ref(false)
const runResult = ref<DepreciationCatchUpResult | null>(null)
const showRunResult = ref(false)

// 资产核对总账
const checking = ref(false)
const reconciliation = ref<FixedAssetReconciliation | null>(null)
const showRecon = ref(false)

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

const categoryOptions = computed(() =>
  categories.value.map((c) => ({ label: c.name, value: c.code }))
)

async function load() {
  loading.value = true
  try {
    const resp = await getFixedAssets({ pageSize: 200 })
    assets.value = resp.items ?? []
  } catch {
    message.error('加载固定资产列表失败')
  } finally {
    loading.value = false
  }
}

async function loadCategories() {
  try {
    const resp = await getFixedAssetCategories()
    categories.value = resp.items ?? []
  } catch {
    categories.value = []
  }
}

function confirmDelete(asset: FixedAsset) {
  deleteTarget.value = asset
  showDeleteConfirm.value = true
}

function currentPeriod(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

function periodRangeText(result: DepreciationCatchUpResult | null): string {
  if (!result?.periods?.length) return '—'
  const first = result.periods[0].period
  const last = result.periods[result.periods.length - 1].period
  return first === last ? first : `${first} ~ ${last}`
}

async function handlePreviewDepreciation() {
  previewing.value = true
  try {
    previewResult.value = await runFixedAssetDepreciationCatchUp({
      throughPeriod: currentPeriod(),
      dryRun: true,
    })
    showPreview.value = true
  } catch (err: any) {
    message.error(err?.response?.data?.detail?.message || err?.response?.data?.detail || '预览折旧失败')
  } finally {
    previewing.value = false
  }
}

async function handleRunDepreciation() {
  running.value = true
  try {
    runResult.value = await runFixedAssetDepreciationCatchUp({ throughPeriod: currentPeriod() })
    showPreview.value = false
    showRunResult.value = true
    await load()
  } catch (err: any) {
    message.error(err?.response?.data?.detail?.message || err?.response?.data?.detail || '计提折旧失败')
  } finally {
    running.value = false
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
    message.error('删除失败（仅草稿可删除）')
  } finally {
    deletingId.value = null
    deleteTarget.value = null
  }
}

async function handleReconciliation() {
  checking.value = true
  try {
    reconciliation.value = await getFixedAssetReconciliation()
    showRecon.value = true
  } catch {
    message.error('资产核对失败')
  } finally {
    checking.value = false
  }
}

const activeAssets = computed(() => assets.value.filter((a) => ['active', 'idle', 'fully_depreciated'].includes(a.status)))
const totalOriginalValue = computed(() => activeAssets.value.reduce((sum, a) => sum + (a.originalValue || 0), 0))
const totalAccumulatedDepreciation = computed(() => activeAssets.value.reduce((sum, a) => sum + (a.accumulatedDepreciation || 0), 0))
const totalNetValue = computed(() => totalOriginalValue.value - totalAccumulatedDepreciation.value)
const totalCount = computed(() => assets.value.length)

const filteredAssets = computed(() => {
  let list = assets.value
  if (filterStatus.value === 'all') {
    // 保持原顺序
  } else if (filterStatus.value === 'disposed') {
    list = list.filter((a) => a.status === 'disposed' || a.status === 'scrapped')
  } else {
    list = list.filter((a) => a.status === filterStatus.value)
  }
  if (filterCategory.value) {
    list = list.filter((a) => a.category === filterCategory.value)
  }
  return list
})

function getDepreciationPercentage(asset: FixedAsset): number {
  if (!asset.originalValue) return 0
  const pct = ((asset.accumulatedDepreciation || 0) / asset.originalValue) * 100
  return Math.min(100, Math.max(0, Math.round(pct)))
}

const methodLabels: Record<string, string> = {
  straight_line: '年限平均法',
  one_time: '一次性折旧',
}

const exporting = ref(false)

async function handleExport() {
  exporting.value = true
  try {
    const blob = await exportFixedAssetLedger()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `固定资产台账-${new Date().toISOString().slice(0, 10)}.xlsx`
    link.click()
    URL.revokeObjectURL(url)
  } catch {
    message.error('导出台账失败')
  } finally {
    exporting.value = false
  }
}

onMounted(() => {
  load()
  loadCategories()
})

// 从别的标签切回来时静默重拉，避免对着旧数字干活
usePageRefresh(load)
</script>

<template>
  <div class="asset-page">
    <div class="page-header">
      <div class="title-section">
        <h2>固定资产</h2>
        <p class="subtitle">管理房屋、设备、车辆等固定资产，自可供使用次月起按月计提折旧并生成凭证</p>
      </div>
      <NSpace>
        <NButton secondary :loading="checking" @click="handleReconciliation">
          <template #icon><i class="ri-scales-3-line" /></template>
          资产核对
        </NButton>
        <NButton secondary :loading="exporting" @click="handleExport">
          <template #icon><i class="ri-download-2-line" /></template>
          导出台账
        </NButton>
        <NButton secondary type="primary" :loading="previewing" @click="handlePreviewDepreciation">
          <template #icon><i class="ri-calculator-line" /></template>
          一键计提折旧
        </NButton>
        <NButton type="primary" @click="router.push('/assets/create')">
          <template #icon><i class="ri-add-line" /></template>
          录入固定资产
        </NButton>
      </NSpace>
    </div>

    <!-- 统计卡 -->
    <NGrid :cols="4" :x-gap="16" class="stats-grid">
      <NGridItem>
        <div class="stat-box">
          <div class="stat-icon-wrapper val-icon"><i class="ri-money-cny-box-line" /></div>
          <div class="stat-content">
            <span class="stat-label">固定资产原值</span>
            <span class="stat-value">{{ formatMoney(totalOriginalValue) }}</span>
          </div>
        </div>
      </NGridItem>
      <NGridItem>
        <div class="stat-box">
          <div class="stat-icon-wrapper dep-icon"><i class="ri-line-chart-line" /></div>
          <div class="stat-content">
            <span class="stat-label">累计折旧</span>
            <span class="stat-value text-muted-value">{{ formatMoney(totalAccumulatedDepreciation) }}</span>
          </div>
        </div>
      </NGridItem>
      <NGridItem>
        <div class="stat-box">
          <div class="stat-icon-wrapper net-icon"><i class="ri-shield-check-line" /></div>
          <div class="stat-content">
            <span class="stat-label">账面净值</span>
            <span class="stat-value text-green">{{ formatMoney(totalNetValue) }}</span>
          </div>
        </div>
      </NGridItem>
      <NGridItem>
        <div class="stat-box">
          <div class="stat-icon-wrapper qty-icon"><i class="ri-database-line" /></div>
          <div class="stat-content">
            <span class="stat-label">在册资产</span>
            <span class="stat-value">{{ totalCount }} <span class="unit">件</span></span>
          </div>
        </div>
      </NGridItem>
    </NGrid>

    <!-- 筛选 -->
    <div class="filter-section">
      <NRadioGroup v-model:value="filterStatus" size="medium">
        <NRadioButton value="all">全部 ({{ totalCount }})</NRadioButton>
        <NRadioButton value="pending_review">待复核 ({{ assets.filter(a => a.status === 'pending_review').length }})</NRadioButton>
        <NRadioButton value="in_use">使用中 ({{ activeAssets.length }})</NRadioButton>
        <NRadioButton value="disposed">已处置/报废 ({{ assets.filter(a => a.status === 'disposed' || a.status === 'scrapped').length }})</NRadioButton>
      </NRadioGroup>
      <NSelect
        v-model:value="filterCategory"
        :options="categoryOptions"
        placeholder="全部类别"
        clearable
        size="small"
        style="width: 160px; margin-left: 12px;"
      />
    </div>

    <NSpin :show="loading">
      <div v-if="filteredAssets.length === 0 && !loading" class="empty-section">
        <SoloEmpty
          title="暂无符合条件的固定资产记录"
          description="录入电脑、设备等大件，系统自动按月计提折旧。"
          icon="ri-computer-line"
        >
          <template #action>
            <div v-if="filterStatus === 'all'">
              <NButton size="small" type="primary" @click="router.push('/assets/create')">录入首笔固定资产</NButton>
            </div>
          </template>
        </SoloEmpty>
      </div>

      <div v-else class="asset-grid">
        <div v-for="asset in filteredAssets" :key="asset.id" class="asset-card-wrapper">
          <div class="asset-card-body">
            <div class="asset-card-header">
              <div class="asset-title-section">
                <span class="asset-name">{{ asset.name }}</span>
                <span class="asset-category">{{ asset.code }} · {{ asset.categoryName }} · {{ methodLabels[asset.depreciationMethod] || asset.depreciationMethod }}</span>
              </div>
              <NTag size="tiny" :bordered="false" :type="statusConfig[asset.status]?.type || 'default'" round>
                {{ statusConfig[asset.status]?.label || asset.status }}
              </NTag>
            </div>

            <div class="asset-cost-row">
              <span class="cost-label">资产原值</span>
              <span class="cost-value">{{ formatMoney(asset.originalValue) }}</span>
            </div>

            <div class="progress-section">
              <div class="progress-header">
                <span class="progress-label">折旧进度</span>
                <span class="progress-percent-text">剩余 {{ asset.remainingMonths }} 个月</span>
              </div>
              <NProgress type="line" :percentage="getDepreciationPercentage(asset)" :show-indicator="false" status="success" class="dep-progress" />
            </div>

            <div class="metadata-grid">
              <div class="meta-item">
                <span class="meta-label">取得日期</span>
                <span class="meta-value">{{ formatDate(asset.acquisitionDate) }}</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">折旧年限</span>
                <span class="meta-value">{{ Math.round(asset.usefulLifeMonths / 12 * 10) / 10 }} 年</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">月折旧额</span>
                <span class="meta-value">{{ formatMoney(asset.monthlyDepreciation) }}</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">账面净值</span>
                <span class="meta-value net-value-text">{{ formatMoney(asset.netValue) }}</span>
              </div>
            </div>

            <div class="card-actions">
              <span class="depreciation-accumulated">累计折旧: {{ formatMoney(asset.accumulatedDepreciation) }}</span>
              <NSpace :size="4">
                <NButton text size="tiny" type="primary" @click="router.push(`/assets/${asset.id}`)">详情</NButton>
                <NButton v-if="asset.status === 'draft'" text size="tiny" type="error" class="delete-btn" @click="confirmDelete(asset)">
                  <template #icon><i class="ri-delete-bin-line" /></template>
                  删除
                </NButton>
              </NSpace>
            </div>
          </div>
        </div>
      </div>
    </NSpin>

    <!-- 删除确认 -->
    <NModal v-model:show="showDeleteConfirm" title="确认删除" preset="card" style="width: 400px; border-radius: var(--solo-radius-lg);">
      <p>确定删除固定资产「<strong>{{ deleteTarget?.name }}</strong>」吗？仅草稿可删除，此操作不可撤销。</p>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showDeleteConfirm = false">取消</NButton>
          <NButton type="error" :loading="deletingId !== null" @click="doDelete">确认删除</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 折旧预览 -->
    <NModal v-model:show="showPreview" title="折旧补提预览" preset="card" style="width: 520px; border-radius: var(--solo-radius-lg);">
      <div v-if="previewResult" class="dep-result-content">
        <div class="result-detail-row">
          <span class="result-detail-label">补提期间</span>
          <span class="result-detail-val"><strong>{{ periodRangeText(previewResult) }}</strong></span>
        </div>
        <div class="result-detail-row">
          <span class="result-detail-label">待计提资产</span>
          <span class="result-detail-val">{{ previewResult.processedCount }} 项</span>
        </div>
        <div class="result-detail-row">
          <span class="result-detail-label">补提总额</span>
          <span class="result-detail-val dep-amount-highlight">{{ formatMoney(previewResult.totalAmount) }}</span>
        </div>
        <div v-if="previewResult.periods.length" class="dep-entries-section">
          <h4>按期间汇总</h4>
          <div class="dep-entries-list">
            <div v-for="item in previewResult.periods" :key="item.period" class="dep-entry-row">
              <span class="dep-entry-name">{{ item.period }} · {{ item.processedCount }} 项</span>
              <span class="dep-entry-val">{{ formatMoney(item.totalAmount) }}</span>
            </div>
          </div>
        </div>
        <p class="preview-tip">固定资产自可供使用<b>次月</b>起计提折旧；执行后将按公司过账策略生成凭证（借管理费用-折旧费，贷累计折旧）。</p>
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showPreview = false">取消</NButton>
          <NButton type="primary" :loading="running" :disabled="(previewResult?.processedCount ?? 0) === 0" @click="handleRunDepreciation">
            确认计提
          </NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 折旧结果 -->
    <NModal v-model:show="showRunResult" title="折旧完成" preset="card" style="width: 480px; border-radius: var(--solo-radius-lg);">
      <div v-if="runResult" class="dep-result-content">
        <div class="result-success-header">
          <div class="success-icon-circle"><i class="ri-checkbox-circle-fill" /></div>
          <h3>计提任务已完成</h3>
          <p v-if="runResult.pendingReviewCount > 0">已生成 {{ runResult.pendingReviewCount }} 张待复核折旧凭证，请在凭证页复核过账</p>
          <p v-else>折旧已过账入账</p>
        </div>
        <div class="result-details-card">
          <div class="result-detail-row">
            <span class="result-detail-label">计提会计期间</span>
            <span class="result-detail-val"><strong>{{ periodRangeText(runResult) }}</strong></span>
          </div>
          <div class="result-detail-row">
            <span class="result-detail-label">本次折旧总额</span>
            <span class="result-detail-val dep-amount-highlight">{{ formatMoney(runResult.totalAmount) }}</span>
          </div>
          <div class="result-detail-row">
            <span class="result-detail-label">计提资产数量</span>
            <span class="result-detail-val">{{ runResult.processedCount }} 项</span>
          </div>
        </div>
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton type="primary" @click="showRunResult = false">我知道了</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 资产核对总账 -->
    <NModal v-model:show="showRecon" title="资产核对总账" preset="card" style="width: 560px; border-radius: var(--solo-radius-lg);">
      <div v-if="reconciliation" class="dep-result-content">
        <template v-if="reconciliation.applicable">
          <div class="recon-verdict" :class="reconciliation.balanced ? 'recon-ok' : 'recon-bad'">
            <i :class="reconciliation.balanced ? 'ri-checkbox-circle-fill' : 'ri-error-warning-fill'" />
            {{ reconciliation.balanced ? '三方勾稽一致' : '勾稽存在差异' }}
          </div>
          <div class="result-details-card">
            <div class="result-detail-row">
              <span class="result-detail-label">卡片原值合计</span>
              <span class="result-detail-val">{{ formatMoney(reconciliation.cardCostTotal) }}</span>
            </div>
            <div class="result-detail-row">
              <span class="result-detail-label">1601 固定资产余额</span>
              <span class="result-detail-val">{{ formatMoney(reconciliation.ledger1601) }}</span>
            </div>
            <div class="result-detail-row">
              <span class="result-detail-label">差异（原值）</span>
              <span class="result-detail-val" :style="{ color: Number(reconciliation.costDiff) === 0 ? 'var(--solo-income)' : 'var(--solo-risk)' }">
                {{ formatMoney(reconciliation.costDiff) }}
              </span>
            </div>
            <div class="result-detail-row">
              <span class="result-detail-label">卡片累计折旧合计</span>
              <span class="result-detail-val">{{ formatMoney(reconciliation.cardDepreciationTotal) }}</span>
            </div>
            <div class="result-detail-row">
              <span class="result-detail-label">1602 累计折旧余额</span>
              <span class="result-detail-val">{{ formatMoney(reconciliation.ledger1602) }}</span>
            </div>
            <div class="result-detail-row">
              <span class="result-detail-label">差异（累计折旧）</span>
              <span class="result-detail-val" :style="{ color: Number(reconciliation.depreciationDiff) === 0 ? 'var(--solo-income)' : 'var(--solo-risk)' }">
                {{ formatMoney(reconciliation.depreciationDiff) }}
              </span>
            </div>
            <div class="result-detail-row">
              <span class="result-detail-label">折旧计划累计过账</span>
              <span class="result-detail-val">{{ formatMoney(reconciliation.schedulePostedTotal) }}</span>
            </div>
          </div>
          <p class="preview-tip">口径：卡片合计 vs 1601/1602 科目余额 vs 折旧计划累计（posted）；差异明细可导出台账逐张核对。</p>
        </template>
        <SoloEmpty
          v-else
          variant="hint"
          title="暂无已入账固定资产"
          description="没有入账的资产就没有折旧可核对，这一项不适用。"
        />
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton type="primary" @click="showRecon = false">我知道了</NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.asset-page {
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
  flex-shrink: 0;
}
.val-icon { background: rgba(99, 102, 241, 0.1); color: var(--solo-primary); }
.dep-icon { background: rgba(100, 116, 139, 0.1); color: var(--solo-text-secondary); }
.net-icon { background: rgba(16, 185, 129, 0.1); color: var(--solo-income); }
.qty-icon { background: rgba(245, 158, 11, 0.1); color: var(--solo-warning); }
.stat-content { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
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
.stat-value .unit { font-size: 12px; color: var(--solo-text-muted); font-weight: 500; }
.text-muted-value { color: var(--solo-text-secondary); }
.text-green { color: var(--solo-income); }
.filter-section {
  margin-bottom: 24px;
  display: flex;
  justify-content: flex-start;
  align-items: center;
  gap: 12px;
}
/* 分段控件的外观由全局主题统一（App.vue 的 Radio overrides + style.css 的凹槽），
   页面不再自己套白底描边容器。 */
.empty-section {
  padding: 64px 0;
  background: var(--solo-bg-card);
  border: 1px dashed var(--solo-border);
  border-radius: var(--solo-radius-lg);
  display: flex;
  justify-content: center;
}
.empty-section :deep(.n-empty__icon) { height: auto; }
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
.asset-card-body { padding: 20px; display: flex; flex-direction: column; gap: 14px; }
.asset-card-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.asset-title-section { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.asset-name {
  font-size: 16px;
  font-weight: 700;
  color: var(--solo-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.asset-category { font-size: 12px; color: var(--solo-text-muted); }
.asset-cost-row {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  padding: 8px 12px;
  background: rgba(99, 102, 241, 0.03);
  border-radius: var(--solo-radius);
  border: 1px dashed rgba(99, 102, 241, 0.08);
}
.cost-label { font-size: 12px; color: var(--solo-text-secondary); }
.cost-value {
  font-size: 18px;
  font-weight: 800;
  color: var(--solo-text-primary);
  font-family: 'Outfit', 'Inter', sans-serif;
}
.progress-section { display: flex; flex-direction: column; gap: 6px; }
.progress-header { display: flex; justify-content: space-between; align-items: center; }
.progress-label { font-size: 11.5px; color: var(--solo-text-secondary); font-weight: 500; }
.progress-percent-text {
  font-size: 11.5px;
  color: var(--solo-income);
  font-weight: 700;
  font-family: 'Inter', monospace;
}
.dep-progress { height: 6px; }
.metadata-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 8px;
  padding: 8px 0;
  border-top: 1px solid var(--solo-border-light);
  border-bottom: 1px solid var(--solo-border-light);
}
.meta-item { display: flex; flex-direction: column; gap: 2px; padding: 4px 8px; }
.meta-label { font-size: 11px; color: var(--solo-text-muted); }
.meta-value { font-size: 12.5px; color: var(--solo-text-primary); font-weight: 600; }
.net-value-text { color: var(--solo-income); font-family: 'Outfit', 'Inter', sans-serif; }
.card-actions { display: flex; justify-content: space-between; align-items: center; font-size: 12px; }
.depreciation-accumulated { color: var(--solo-text-muted); font-weight: 500; }
.delete-btn { font-size: 12px; font-weight: 600; }
.dep-result-content { display: flex; flex-direction: column; gap: 16px; }
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
.result-success-header h3 { margin: 4px 0 0 0; font-size: 18px; font-weight: 700; }
.result-success-header p { margin: 0; font-size: 13px; color: var(--solo-text-secondary); }
.result-details-card {
  background: rgba(255, 255, 255, 0.4);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius-lg);
  padding: 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.result-detail-row { display: flex; justify-content: space-between; align-items: center; font-size: 13.5px; }
.result-detail-label { color: var(--solo-text-secondary); }
.result-detail-val { color: var(--solo-text-primary); font-weight: 600; }
.dep-amount-highlight {
  font-size: 18px;
  font-weight: 800;
  color: var(--solo-primary);
  font-family: 'Outfit', 'Inter', sans-serif;
}
.dep-entries-section h4 { margin: 0 0 8px 0; font-size: 13.5px; color: var(--solo-text-secondary); font-weight: 750; }
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
.dep-entry-row:last-child { border-bottom: none; }
.dep-entry-name { font-weight: 600; color: var(--solo-text-primary); }
.dep-entry-val { font-weight: 700; color: var(--solo-primary); font-family: 'Outfit', 'Inter', sans-serif; }
.preview-tip { margin: 0; font-size: 12px; color: var(--solo-text-muted); line-height: 1.5; }
.preview-tip b { color: var(--solo-primary); font-weight: 600; }
.recon-verdict {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 15px;
  font-weight: 700;
  padding: 10px 14px;
  border-radius: var(--solo-radius);
}
.recon-ok { color: var(--solo-income); background: var(--solo-income-bg); }
.recon-bad { color: var(--solo-risk); background: rgba(239, 68, 68, 0.08); }
</style>
