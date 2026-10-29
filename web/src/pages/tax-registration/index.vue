<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  useDialog,
  useMessage,
} from 'naive-ui'
import {
  applyTaxSchedule,
  buildTaxRegistrationTemplate,
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

const message = useMessage()
const dialog = useDialog()

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

// ── 提示文案 ──────────────────────────────────────────────────────────────
const filingEntryHint = computed(() =>
  preview.value?.source === 'registration'
    ? `已按税费种认定表生成征期：${stats.value.active} 项认定 · ${preview.value.summary.itemCount} 项应报事项`
    : '尚未同步税费种认定表，当前按内置税种清单生成征期',
)

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
    message.success(
      `${summarizeImport(result)}；已按新认定重算 ${year.value} 年征期（新增 ${result.refresh?.created ?? 0} 条待办）`,
    )
    await load()
  } catch (error) {
    message.error(accountingErrorText(error, '导入失败'))
  } finally {
    importing.value = false
  }
}

async function useTemplate() {
  try {
    const result = await buildTaxRegistrationTemplate()
    dialog.info({
      title: '按主体类型带出默认认定',
      content: `${result.notice}\n将带出 ${result.items.length} 行（${result.entityType === 'ltd' ? '有限责任公司' : '个体户 / 个人独资'}口径）。`,
      positiveText: '带出并保存',
      negativeText: '取消',
      onPositiveClick: async () => {
        const imported = await importTaxRegistrations(result.rows, 'merge', 'template')
        message.success(`已带出 ${imported.created} 行默认认定，请按税局认定表核对`)
        await load()
      },
    })
  } catch (error) {
    message.error(accountingErrorText(error, '带出模板失败'))
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
    <!-- 头部与指标卡 -->
    <TaxRegistrationHeader
      :loading="loading"
      :stats="stats"
      :preview="preview"
      :year="year"
      @refresh="load"
      @use-template="useTemplate"
      @open-import="openImport"
    />

    <!-- 异常与同步指引提醒条 -->
    <NAlert
      v-if="!stats.active"
      type="info"
      :bordered="false"
      class="block-alert"
      :show-icon="true"
    >
      <template #header>还没有同步税局认定表</template>
      <div class="alert-desc">
        当前征期按内置税种清单生成。建议从电子税务局「税费种认定信息」页复制整张表格粘进来，或先按主体类型带出一份默认值再逐行核对。
      </div>
      <div class="alert-actions">
        <NButton size="small" type="primary" @click="openImport">去同步</NButton>
        <NButton size="small" @click="useTemplate">按主体类型带出</NButton>
      </div>
    </NAlert>

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
      :filing-entry-hint="filingEntryHint"
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
  max-width: 1440px;
  margin: 0 auto;
}
.block-alert {
  margin-bottom: 14px;
  border-radius: 8px;
  border: 1px solid color-mix(in srgb, var(--solo-primary) 15%, transparent);
}
.alert-desc {
  font-size: 13px;
  line-height: 1.6;
}
.alert-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}
.conflict-item {
  display: inline-block;
  margin-right: 8px;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--solo-bg-soft);
  font-size: 12px;
}
</style>
