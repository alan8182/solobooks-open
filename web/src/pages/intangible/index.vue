<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { usePageRefresh } from '@/composables/usePageRefresh'
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NTag, NSpace, NSpin, useMessage, NModal, NGrid, NGridItem, NProgress, NRadioGroup, NRadioButton, NSelect } from 'naive-ui'
import { getIntangibleAssets, getIntangibleCategories, deleteIntangibleAsset, runIntangibleAmortizationCatchUp, exportIntangibleLedger } from '@/api/intangible'
import { formatMoney, formatDate } from '@/utils/format'
import type { AmortizationCatchUpResult, IntangibleAsset, IntangibleCategory } from '@/api/types'
import { SoloEmpty } from '@/components/ui'

const router = useRouter()
const message = useMessage()

const assets = ref<IntangibleAsset[]>([])
const categories = ref<IntangibleCategory[]>([])
const loading = ref(true)
const deletingId = ref<string | null>(null)
const showDeleteConfirm = ref(false)
const deleteTarget = ref<IntangibleAsset | null>(null)
const filterStatus = ref('all')
const filterCategory = ref<string | null>(null)

// 摊销流程：先预览（dryRun），确认后执行
const previewing = ref(false)
const running = ref(false)
const previewResult = ref<AmortizationCatchUpResult | null>(null)
const showPreview = ref(false)
const runResult = ref<AmortizationCatchUpResult | null>(null)
const showRunResult = ref(false)

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

const categoryOptions = computed(() =>
  categories.value.map((c) => ({ label: c.name, value: c.code }))
)

async function load() {
  loading.value = true
  try {
    const resp = await getIntangibleAssets({ pageSize: 200 })
    assets.value = resp.items ?? []
  } catch {
    message.error('加载无形资产列表失败')
  } finally {
    loading.value = false
  }
}

async function loadCategories() {
  try {
    const resp = await getIntangibleCategories()
    categories.value = resp.items ?? []
  } catch {
    categories.value = []
  }
}

function confirmDelete(asset: IntangibleAsset) {
  deleteTarget.value = asset
  showDeleteConfirm.value = true
}

function currentPeriod(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

function periodRangeText(result: AmortizationCatchUpResult | null): string {
  if (!result?.periods?.length) return '—'
  const first = result.periods[0].period
  const last = result.periods[result.periods.length - 1].period
  return first === last ? first : `${first} ~ ${last}`
}

async function handlePreviewAmortization() {
  previewing.value = true
  try {
    previewResult.value = await runIntangibleAmortizationCatchUp({
      throughPeriod: currentPeriod(),
      dryRun: true,
    })
    showPreview.value = true
  } catch (err: any) {
    message.error(err?.response?.data?.detail?.message || err?.response?.data?.detail || '预览摊销失败')
  } finally {
    previewing.value = false
  }
}

async function handleRunAmortization() {
  running.value = true
  try {
    runResult.value = await runIntangibleAmortizationCatchUp({ throughPeriod: currentPeriod() })
    showPreview.value = false
    showRunResult.value = true
    await load()
  } catch (err: any) {
    message.error(err?.response?.data?.detail?.message || err?.response?.data?.detail || '计提摊销失败')
  } finally {
    running.value = false
  }
}

async function doDelete() {
  if (!deleteTarget.value) return
  deletingId.value = deleteTarget.value.id
  try {
    await deleteIntangibleAsset(deleteTarget.value.id)
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

const activeAssets = computed(() => assets.value.filter((a) => ['active', 'idle', 'fully_amortized'].includes(a.status)))
const totalOriginalValue = computed(() => activeAssets.value.reduce((sum, a) => sum + (a.originalValue || 0), 0))
const totalAccumulatedAmortization = computed(() => activeAssets.value.reduce((sum, a) => sum + (a.accumulatedAmortization || 0), 0))
const totalNetValue = computed(() => totalOriginalValue.value - totalAccumulatedAmortization.value)
const expiringCount = computed(
  () =>
    activeAssets.value.filter((a) => {
      if (!a.expiryDate) return false
      const days = (new Date(a.expiryDate).getTime() - Date.now()) / 86400000
      return days <= 90
    }).length
)
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

function getAmortizationPercentage(asset: IntangibleAsset): number {
  if (!asset.originalValue) return 0
  const pct = ((asset.accumulatedAmortization || 0) / asset.originalValue) * 100
  return Math.min(100, Math.max(0, Math.round(pct)))
}

const exporting = ref(false)

async function handleExport() {
  exporting.value = true
  try {
    const blob = await exportIntangibleLedger()
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `无形资产台账-${new Date().toISOString().slice(0, 10)}.xlsx`
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
  <div class="intangible-page">
    <div class="page-header">
      <div class="title-section">
        <h2>无形资产</h2>
        <p class="subtitle">管理专利、商标、软件等无形资产，可供使用当月起按月摊销并生成凭证</p>
      </div>
      <NSpace>
        <NButton secondary :loading="exporting" @click="handleExport">
          <template #icon><i class="ri-download-2-line" /></template>
          导出台账
        </NButton>
        <NButton secondary type="primary" :loading="previewing" @click="handlePreviewAmortization">
          <template #icon><i class="ri-calculator-line" /></template>
          一键摊销
        </NButton>
        <NButton type="primary" @click="router.push('/intangibles/create')">
          <template #icon><i class="ri-add-line" /></template>
          录入无形资产
        </NButton>
      </NSpace>
    </div>

    <!-- 统计卡 -->
    <NGrid :cols="4" :x-gap="16" class="stats-grid">
      <NGridItem>
        <div class="stat-box">
          <div class="stat-icon-wrapper val-icon"><i class="ri-money-cny-box-line" /></div>
          <div class="stat-content">
            <span class="stat-label">无形资产原值</span>
            <span class="stat-value">{{ formatMoney(totalOriginalValue) }}</span>
          </div>
        </div>
      </NGridItem>
      <NGridItem>
        <div class="stat-box">
          <div class="stat-icon-wrapper dep-icon"><i class="ri-line-chart-line" /></div>
          <div class="stat-content">
            <span class="stat-label">累计摊销</span>
            <span class="stat-value text-muted-value">{{ formatMoney(totalAccumulatedAmortization) }}</span>
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
            <span class="stat-label">在册数量<span v-if="expiringCount" class="expiring-badge">{{ expiringCount }} 项 90 天内到期</span></span>
            <span class="stat-value">{{ totalCount }} <span class="unit">项</span></span>
          </div>
        </div>
      </NGridItem>
    </NGrid>

    <!-- 筛选 -->
    <div class="filter-section">
      <NRadioGroup v-model:value="filterStatus" size="medium">
        <NRadioButton value="all">全部 ({{ totalCount }})</NRadioButton>
        <NRadioButton value="pending_review">待复核 ({{ assets.filter(a => a.status === 'pending_review').length }})</NRadioButton>
        <NRadioButton value="in_use">摊销中 ({{ activeAssets.length }})</NRadioButton>
        <NRadioButton value="disposed">已处置/核销 ({{ assets.filter(a => a.status === 'disposed' || a.status === 'scrapped').length }})</NRadioButton>
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
          title="暂无符合条件的无形资产记录"
          description="录入软件著作权、商标等，系统自动按月摊销。"
          icon="ri-copyright-line"
        >
          <template #action>
            <div v-if="filterStatus === 'all'">
              <NButton size="small" type="primary" @click="router.push('/intangibles/create')">录入首笔无形资产</NButton>
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
                <span class="asset-category">{{ asset.code }} · {{ asset.categoryName }}</span>
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
                <span class="progress-label">摊销进度</span>
                <span class="progress-percent-text">剩余 {{ asset.remainingMonths }} 个月</span>
              </div>
              <NProgress type="line" :percentage="getAmortizationPercentage(asset)" :show-indicator="false" status="success" class="dep-progress" />
            </div>

            <div class="metadata-grid">
              <div class="meta-item">
                <span class="meta-label">起摊月份</span>
                <span class="meta-value">{{ asset.availableForUseDate.slice(0, 7) }}</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">摊销年限</span>
                <span class="meta-value">{{ Math.round(asset.usefulLifeMonths / 12 * 10) / 10 }} 年</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">权利到期</span>
                <span class="meta-value">{{ asset.expiryDate ? formatDate(asset.expiryDate) : '—' }}</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">账面净值</span>
                <span class="meta-value net-value-text">{{ formatMoney(asset.netValue) }}</span>
              </div>
            </div>

            <div class="card-actions">
              <span class="depreciation-accumulated">累计摊销: {{ formatMoney(asset.accumulatedAmortization) }}</span>
              <NSpace :size="4">
                <NButton text size="tiny" type="primary" @click="router.push(`/intangibles/${asset.id}`)">详情</NButton>
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
      <p>确定删除无形资产「<strong>{{ deleteTarget?.name }}</strong>」吗？仅草稿可删除，此操作不可撤销。</p>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showDeleteConfirm = false">取消</NButton>
          <NButton type="error" :loading="deletingId !== null" @click="doDelete">确认删除</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 摊销预览 -->
    <NModal v-model:show="showPreview" title="摊销补提预览" preset="card" style="width: 520px; border-radius: var(--solo-radius-lg);">
      <div v-if="previewResult" class="dep-result-content">
        <div class="result-detail-row">
          <span class="result-detail-label">补提期间</span>
          <span class="result-detail-val"><strong>{{ periodRangeText(previewResult) }}</strong></span>
        </div>
        <div class="result-detail-row">
          <span class="result-detail-label">待摊销资产</span>
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
        <p class="preview-tip">无形资产自可供使用当月起摊销；执行后将按公司过账策略生成凭证。</p>
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showPreview = false">取消</NButton>
          <NButton type="primary" :loading="running" :disabled="(previewResult?.processedCount ?? 0) === 0" @click="handleRunAmortization">
            确认计提
          </NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 摊销结果 -->
    <NModal v-model:show="showRunResult" title="摊销完成" preset="card" style="width: 480px; border-radius: var(--solo-radius-lg);">
      <div v-if="runResult" class="dep-result-content">
        <div class="result-success-header">
          <div class="success-icon-circle"><i class="ri-checkbox-circle-fill" /></div>
          <h3>计提任务已完成</h3>
          <p v-if="runResult.pendingReviewCount > 0">已生成 {{ runResult.pendingReviewCount }} 张待复核摊销凭证，请在凭证页复核过账</p>
          <p v-else>摊销已过账入账</p>
        </div>
        <div class="result-details-card">
          <div class="result-detail-row">
            <span class="result-detail-label">计提会计期间</span>
            <span class="result-detail-val"><strong>{{ periodRangeText(runResult) }}</strong></span>
          </div>
          <div class="result-detail-row">
            <span class="result-detail-label">本次摊销总额</span>
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
  </div>
</template>

<style scoped>
.intangible-page {
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
  display: flex;
  align-items: center;
  gap: 8px;
}
.expiring-badge {
  font-size: 10px;
  color: var(--solo-warning);
  background: rgba(245, 158, 11, 0.1);
  border-radius: 8px;
  padding: 1px 6px;
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
.preview-tip { margin: 0; font-size: 12px; color: var(--solo-text-muted); }
</style>
