<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  useMessage,
} from 'naive-ui'
import GuideTrigger from '@/components/business/GuideTrigger.vue'
import {
  applyTaxSchedule,
  createTaxRegistration,
  deactivateTaxRegistration,
  diffTaxSchedule,
  importTaxRegistrations,
  listTaxRegistrations,
  previewTaxSchedule,
  updateTaxRegistration,
  type ScheduleDiff,
  type SchedulePreview,
  type TaxRegistrationInput,
  type TaxRegistrationRow,
} from '@/api/tax-registration'
import { accountingErrorText } from '@/utils/errors'
import { summarizeImport } from '@/utils/taxRegistration'

import TaxRegistrationHeader from './components/TaxRegistrationHeader.vue'
import TaxRegistrationTable from './components/TaxRegistrationTable.vue'
import TaxScheduleTimeline from './components/TaxScheduleTimeline.vue'
import TaxImportModal from './components/TaxImportModal.vue'

const router = useRouter()
const message = useMessage()

const loading = ref(false)
const saving = ref(false)
const importing = ref(false)
const importVisible = ref(false)

const year = ref(new Date().getFullYear())
const rows = ref<TaxRegistrationRow[]>([])
const stats = ref<{ total: number; active: number; groups: Record<string, number> }>({
  total: 0,
  active: 0,
  groups: {},
})
const preview = ref<SchedulePreview | null>(null)
const diff = ref<ScheduleDiff | null>(null)
const showInactive = ref(false)
const previewMode = ref<'dueMonth' | 'period'>('dueMonth')

const tableRef = ref<InstanceType<typeof TaxRegistrationTable> | null>(null)
const importModalRef = ref<InstanceType<typeof TaxImportModal> | null>(null)

async function load() {
  loading.value = true
  try {
    const [list, schedule, diffResult] = await Promise.all([
      listTaxRegistrations(showInactive.value),
      previewTaxSchedule(year.value),
      diffTaxSchedule(year.value),
    ])
    rows.value = list.items
    stats.value = list.stats
    preview.value = schedule
    diff.value = diffResult
  } catch (error) {
    message.error(accountingErrorText(error, '加载税费种认定失败'))
  } finally {
    loading.value = false
  }
}

watch(showInactive, () => {
  void load()
})
watch(year, () => {
  void load()
})

onMounted(() => {
  void load()
})

// ── 展示计算 ──────────────────────────────────────────────────────────────
const vatCycleText = computed(() => (preview.value?.cycles?.vat === 'month' ? '月报' : '季报'))

function goToDashboard() {
  router.push('/')
}

function goToClosingWizard() {
  router.push({ path: '/closing-wizard', query: { step: '1' } })
}

// ── 操作响应 ──────────────────────────────────────────────────────────────
function openImport() {
  importModalRef.value?.reset()
  importVisible.value = true
}

async function handleSaveRow(payload: { id?: string; data: TaxRegistrationInput }) {
  saving.value = true
  try {
    if (payload.id) {
      await updateTaxRegistration(payload.id, payload.data)
    } else {
      await createTaxRegistration(payload.data)
    }
    tableRef.value?.closeEditor()
    message.success('已保存，征期已按新认定重算')
    await load()
  } catch (error) {
    message.error(accountingErrorText(error, '保存失败'))
  } finally {
    saving.value = false
  }
}

async function handleDeactivateRow(row: TaxRegistrationRow) {
  try {
    await deactivateTaxRegistration(row.id as string)
    message.success('已停用')
    await load()
  } catch (error) {
    message.error(accountingErrorText(error, '停用失败'))
  }
}

async function handleConfirmImport(data: {
  rows: Array<Record<string, unknown>>
  mode: 'merge' | 'replace'
  source: 'paste' | 'ocr'
}) {
  importing.value = true
  try {
    const result = await importTaxRegistrations(data.rows, data.mode, data.source)
    importVisible.value = false
    importModalRef.value?.reset()
    try {
      await applyTaxSchedule(year.value)
    } catch {
      // 征期重算若存在非阻塞警告不打断主流程
    }
    message.success(
      `${summarizeImport(result)}；已按税局认定建立 ${year.value} 年征期`,
    )
    await load()
  } catch (error) {
    message.error(accountingErrorText(error, '导入失败'))
  } finally {
    importing.value = false
  }
}

async function applyNow() {
  try {
    const result = await applyTaxSchedule(year.value)
    message.success(`已按认定表重算征期：新增 ${result.created ?? 0} 条，豁免 ${result.autoWaived ?? 0} 条`)
    await load()
  } catch (error) {
    message.error(accountingErrorText(error, '重算失败'))
  }
}
</script>

<template>
  <div class="tax-registration-page">
    <!-- 头部与基础说明 -->
    <TaxRegistrationHeader
      :loading="loading"
      :stats="stats"
      :preview="preview"
      @refresh="load"
      @open-import="openImport"
    />

    <!-- ── 状态 A：尚未初始化（首推电子税局真实同步，辅以暂不同步先去记账） ── -->
    <div v-if="!stats.active" class="onboarding-guide-card">
      <div class="guide-header">
        <div class="guide-badge">
          <i class="ri-shield-check-line" />
          <span>建账必备 · 只需确认一次</span>
        </div>
        <h3 class="guide-title">还没有同步税局认定表</h3>
      </div>

      <div class="channels-grid">
        <!-- 通道 1：从电子税务局同步（唯一权威配置通道） -->
        <div class="channel-card channel-card--primary">
          <div class="channel-header">
            <div class="channel-ribbon">
              <i class="ri-fire-fill" /> 推荐 · 最精准
            </div>
            <div class="channel-icon-wrap channel-icon-wrap--primary">
              <i class="ri-download-cloud-2-line" />
            </div>
          </div>
          <div class="channel-content">
            <div class="channel-title">从电子税务局同步</div>
            <p class="channel-desc">
              登录电子税务局「税费种认定信息」页，复制表格整段粘贴，或直接上传截屏，系统秒级自动识别并建立征期。
            </p>
            <div class="channel-meta">
              <div class="meta-item"><i class="ri-check-line" /> 100% 契合税局最新核定税目与税率</div>
              <div class="meta-item"><i class="ri-check-line" /> 支持整表复制粘贴或图片识别</div>
            </div>
          </div>
          <div class="channel-action">
            <NButton
              type="primary"
              size="medium"
              class="channel-btn channel-btn--primary"
              @click="openImport"
            >
              <template #icon><i class="ri-download-cloud-2-line" /></template>
              去同步
            </NButton>
            <div class="channel-guide">
              <GuideTrigger guide="tax-registration" label="找不到认定表？" hint="含 5 步截图" />
            </div>
          </div>
        </div>

        <!-- 通道 2：暂不同步，先去记账对账（不制造伪数据，直接放行） -->
        <div class="channel-card channel-card--secondary">
          <div class="channel-header">
            <div class="channel-ribbon channel-ribbon--muted">
              不卡流程 · 随时补录
            </div>
            <div class="channel-icon-wrap channel-icon-wrap--secondary">
              <i class="ri-calendar-check-line" />
            </div>
          </div>
          <div class="channel-content">
            <div class="channel-title">手头暂无税局信息？先去记账</div>
            <p class="channel-desc">
              暂不同步也不影响日常记账与导入流水。系统会先按小微常规周期（增值税/企税按季、个税按月）临时推算日历，方便时再回来同步即可。
            </p>
            <div class="channel-meta">
              <div class="meta-item"><i class="ri-check-line" /> 不影响导入当月银行流水与发票</div>
              <div class="meta-item"><i class="ri-check-line" /> 避免猜错城建税率与品目，保持账套真实</div>
            </div>
          </div>
          <div class="channel-action">
            <NButton
              size="medium"
              class="channel-btn"
              @click="goToClosingWizard"
            >
              <template #icon><i class="ri-arrow-right-line" /></template>
              暂不同步，先去月度结账
            </NButton>
          </div>
        </div>
      </div>
    </div>

    <!-- ── 状态 B：已完成同步（大白话安心卡片 + 工作台直达 + 专业明细看板） ── -->
    <template v-else>
      <!-- 就绪状态卡 -->
      <div class="ready-summary-card">
        <div class="ready-head">
          <div class="ready-head-left">
            <div class="ready-badge">
              <i class="ri-checkbox-circle-fill ready-icon" />
              <span>税费申报设置已就绪</span>
            </div>
          </div>
          <div class="ready-actions">
            <NButton quaternary size="small" @click="goToDashboard">
              返回工作台
            </NButton>
            <NButton type="primary" size="small" class="go-dashboard-btn" @click="goToClosingWizard">
              前往月度结账 · 导入当月流水与发票
              <template #icon><i class="ri-arrow-right-line" /></template>
            </NButton>
          </div>
        </div>

        <div class="ready-metrics-grid">
          <div class="metric-item">
            <div class="metric-label">增值税申报周期</div>
            <div class="metric-value">{{ vatCycleText }}</div>
            <div class="metric-desc">小规模纳税人每季度首月（1/4/7/10月）申报</div>
          </div>
          <div class="metric-item">
            <div class="metric-label">企业所得税</div>
            <div class="metric-value">按季预缴</div>
            <div class="metric-desc">季度终了后 15 日内预缴，次年 5 月底汇算</div>
          </div>
          <div class="metric-item">
            <div class="metric-label">个人所得税</div>
            <div class="metric-value">按月申报</div>
            <div class="metric-desc">次月 15 日内全员全额代扣代缴申报</div>
          </div>
          <div class="metric-item">
            <div class="metric-label">附加税及六税两费</div>
            <div class="metric-value">同频随征</div>
            <div class="metric-desc">城建税、教育费附加随增值税自动减免</div>
          </div>
        </div>
      </div>

      <!-- 异常与主体类型不一致冲突提示 -->
      <NAlert
        v-if="preview?.conflicts?.length"
        type="warning"
        :bordered="false"
        class="block-alert"
        :show-icon="true"
      >
        <template #header>认定表与主体类型不一致</template>
        <div>
          <span
            v-for="item in preview.conflicts"
            :key="`${item.levyItem}-${item.levyCategory}`"
            class="conflict-item"
          >
            {{ item.levyItem }}{{ item.levyCategory ? ` · ${item.levyCategory}` : '' }}
          </span>
          与当前主体类型不匹配，已跳过、不会生成待办。请核对主体类型或税局认定。
        </div>
      </NAlert>

      <!-- 征期差异兜底提醒 -->
      <NAlert
        v-if="diff && (diff.willCreateCount || diff.willWaiveCount || diff.willRestoreCount)"
        type="info"
        :bordered="false"
        class="block-alert"
      >
        认定表与当前征期存在差异：将新增 <strong>{{ diff.willCreateCount }}</strong> 条、豁免
        <strong>{{ diff.willWaiveCount }}</strong> 条、恢复 <strong>{{ diff.willRestoreCount }}</strong> 条。
        <NButton size="tiny" type="primary" text style="margin-left: 8px" @click="applyNow">应用到征期</NButton>
      </NAlert>

      <!-- 认定明细宽表 -->
      <TaxRegistrationTable
        ref="tableRef"
        :rows="rows"
        :loading="loading"
        :saving="saving"
        :show-inactive="showInactive"
        @update:show-inactive="showInactive = $event"
        @save="handleSaveRow"
        @deactivate="handleDeactivateRow"
      />

      <!-- 申报节奏看板（按季度聚合与月份时间线） -->
      <TaxScheduleTimeline
        :preview="preview"
        :loading="loading"
        :year="year"
        :preview-mode="previewMode"
        @update:year="year = $event"
        @update:preview-mode="previewMode = $event"
      />
    </template>

    <!-- 同步弹窗（粘贴与 OCR 截图） -->
    <TaxImportModal
      ref="importModalRef"
      :show="importVisible"
      :importing="importing"
      @update:show="importVisible = $event"
      @import="handleConfirmImport"
    />
  </div>
</template>

<style scoped>
.tax-registration-page {
  padding: 20px 24px 40px;
  max-width: var(--page-width-wide);
  margin: 0 auto;
}
.block-alert {
  margin-bottom: 14px;
  border-radius: 8px;
  border: 1px solid color-mix(in srgb, var(--solo-primary) 15%, transparent);
}
.conflict-item {
  display: inline-block;
  margin-right: 8px;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--solo-bg-soft);
  font-size: 12px;
}

/* ── 未初始化向导卡片 ── */
.onboarding-guide-card {
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: 12px;
  padding: 28px 24px 32px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
  margin-bottom: 24px;
}
.guide-header {
  text-align: center;
  max-width: 600px;
  margin: 0 auto 28px;
}
.guide-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 3px 12px;
  border-radius: 999px;
  background: color-mix(in srgb, var(--solo-primary) 10%, transparent);
  color: var(--solo-primary);
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 12px;
}
.guide-title {
  margin: 0;
  font-size: 20px;
  font-weight: 700;
  color: var(--solo-text-primary);
  letter-spacing: -0.01em;
}
.channels-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 20px;
}
.channel-card {
  display: flex;
  flex-direction: column;
  border-radius: 10px;
  padding: 24px;
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-card);
  transition: all 0.2s ease;
  position: relative;
}
.channel-card--primary {
  border-color: color-mix(in srgb, var(--solo-primary) 40%, transparent);
  background: linear-gradient(
    180deg,
    color-mix(in srgb, var(--solo-primary) 3%, transparent) 0%,
    var(--solo-bg-card) 100%
  );
  box-shadow: 0 4px 12px color-mix(in srgb, var(--solo-primary) 8%, transparent);
}
.channel-card--primary:hover {
  border-color: var(--solo-primary);
  transform: translateY(-2px);
}
.channel-card--secondary {
  border-color: var(--solo-border);
  background: var(--solo-bg-card);
}
.channel-card--secondary:hover {
  border-color: color-mix(in srgb, var(--solo-border) 80%, black);
}
.channel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.channel-ribbon {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  font-weight: 600;
  padding: 2px 8px;
  border-radius: 4px;
  background: color-mix(in srgb, var(--solo-primary) 12%, transparent);
  color: var(--solo-primary);
}
.channel-ribbon--muted {
  background: var(--solo-bg-soft);
  color: var(--solo-text-secondary);
}
.channel-icon-wrap {
  width: 40px;
  height: 40px;
  border-radius: 8px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
}
.channel-icon-wrap--primary {
  background: color-mix(in srgb, var(--solo-primary) 12%, transparent);
  color: var(--solo-primary);
}
.channel-icon-wrap--secondary {
  background: var(--solo-bg-soft);
  color: var(--solo-text-secondary);
}
.channel-content {
  flex: 1;
}
.channel-title {
  font-size: 16px;
  font-weight: 650;
  color: var(--solo-text-primary);
  margin-bottom: 8px;
}
.channel-desc {
  font-size: 13px;
  line-height: 1.6;
  color: var(--solo-text-secondary);
  margin: 0 0 16px;
  min-height: 42px;
}
.channel-meta {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin-bottom: 20px;
}
.meta-item {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--solo-text-secondary);
}
.meta-item i {
  color: var(--solo-primary);
  font-size: 14px;
}
.channel-action {
  margin-top: auto;
}
.channel-btn {
  width: 100%;
  font-weight: 600;
  height: 38px;
}
.channel-guide {
  margin-top: 10px;
  text-align: center;
}

/* ── 已就绪安心总结卡 ── */
.ready-summary-card {
  background: var(--solo-bg-card);
  border: 1px solid color-mix(in srgb, var(--solo-success) 30%, transparent);
  border-radius: 12px;
  padding: 20px 24px;
  margin-bottom: 20px;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.03);
}
.ready-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.ready-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 16px;
  font-weight: 700;
  color: var(--solo-text-primary);
}
.ready-icon {
  font-size: 20px;
  color: var(--solo-success);
}
.ready-actions {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}
.go-dashboard-btn {
  font-weight: 600;
}
.ready-metrics-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
  padding-top: 14px;
  border-top: 1px dashed var(--solo-border);
}
.metric-item {
  padding: 10px 14px;
  background: var(--solo-bg-soft);
  border-radius: 8px;
}
.metric-label {
  font-size: 12px;
  color: var(--solo-text-secondary);
  margin-bottom: 4px;
}
.metric-value {
  font-size: 16px;
  font-weight: 700;
  color: var(--solo-text-primary);
  margin-bottom: 2px;
}
.metric-desc {
  font-size: 11px;
  color: var(--solo-text-muted);
}

@media (max-width: 992px) {
  .ready-metrics-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

@media (max-width: 768px) {
  .channels-grid {
    grid-template-columns: 1fr;
  }
  .ready-head {
    flex-direction: column;
  }
  .ready-metrics-grid {
    grid-template-columns: 1fr;
  }
}
</style>