<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NAlert, NButton, NCard, NFormItem, NInput, NSelect, NSpin, NTab, NTabs, NTag, useMessage } from 'naive-ui'
import { createArchive, downloadBackupRecord, getBackupRecords } from '@/api/backup'
import type { BackupRecordItem } from '@/api/backup'
import { formatDateTime } from '@/utils/format'
import { SoloEmpty } from '@/components/ui'
import AuditLogTab from '@/pages/mine/audit-log.vue'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const activeTab = ref<'archive' | 'audit'>((route.query.tab as string) === 'audit' ? 'audit' : 'archive')
const auditLoadedOnce = ref(activeTab.value === 'audit')

watch(
  () => route.query.tab,
  (tab) => {
    if (tab === 'audit') {
      activeTab.value = 'audit'
      auditLoadedOnce.value = true
    } else if (tab === 'archive') {
      activeTab.value = 'archive'
    }
  },
)

function handleTabChange(tab: string) {
  activeTab.value = tab as 'archive' | 'audit'
  if (tab === 'audit') auditLoadedOnce.value = true
  router.replace({ query: { ...route.query, tab } })
}

const subtitle = computed(() =>
  activeTab.value === 'audit'
    ? '系统关键操作行为留痕追溯，记录创建、过账、结账等动作与变更明细'
    : '一键导出全账套归档 Excel（含封面与数据指纹），可直接交会计或审计；历史归档可随时重新下载',
)

const currentYear = new Date().getFullYear()
const yearOptions = [0, 1, 2].map((offset) => ({ label: `${currentYear - offset} 年`, value: currentYear - offset }))
const year = ref(currentYear)
const months = ref('1-12')
const generating = ref(false)
const loading = ref(false)
const records = ref<BackupRecordItem[]>([])

const scopeHint = computed(() => (months.value.trim() ? `期间 ${months.value}` : '全年'))

function formatSize(bytes: number) {
  if (!bytes) return '—'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

async function load() {
  loading.value = true
  try {
    const data = await getBackupRecords(50)
    records.value = data.items
  } catch {
    message.error('加载归档记录失败')
  } finally {
    loading.value = false
  }
}

async function generate() {
  generating.value = true
  try {
    const blob = await createArchive(year.value, months.value.trim() || undefined)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = `归档_${year.value}_${(months.value || '全年').replace('-', '至')}.xlsx`
    link.click()
    URL.revokeObjectURL(url)
    message.success('归档工作簿已生成并开始下载')
    await load()
  } catch (error: unknown) {
    const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : '生成失败，请缩小期间范围后重试')
  } finally {
    generating.value = false
  }
}

async function reDownload(record: BackupRecordItem) {
  try {
    await downloadBackupRecord(record.id, record.fileName)
  } catch {
    message.error('文件已失效，请重新生成')
    await load()
  }
}

onMounted(load)
</script>

<template>
  <div class="report-container">
    <div class="report-header">
      <div class="report-header-left">
        <NButton text class="report-back-btn" @click="router.push('/mine')">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回设置
        </NButton>
        <div class="report-title-section">
          <h2>备份与归档</h2>
          <p class="report-subtitle">{{ subtitle }}</p>
        </div>
      </div>
    </div>

    <div class="mode-tabs-wrap mb-4">
      <NTabs :value="activeTab" type="segment" class="backup-tabs" @update:value="handleTabChange">
        <NTab name="archive">数据归档与备份</NTab>
        <NTab name="audit">审计日志</NTab>
      </NTabs>
    </div>

    <div v-show="activeTab === 'archive'">
      <NCard :bordered="false" class="report-card mb-4">
        <div class="generate-row">
          <NFormItem label="会计年度" label-placement="left" :show-feedback="false">
            <NSelect v-model:value="year" :options="yearOptions" style="width: 120px;" />
          </NFormItem>
          <NFormItem label="期间" label-placement="left" :show-feedback="false">
            <NInput v-model:value="months" placeholder="如 1-10，留空为全年" style="width: 200px;" />
          </NFormItem>
          <NButton type="primary" :loading="generating" @click="generate">
            <template #icon><i class="ri-download-cloud-2-line" /></template>
            生成并下载归档
          </NButton>
          <span class="generate-hint">{{ scopeHint }} · 一次最多 24 个月</span>
        </div>
      </NCard>

      <NAlert type="info" :bordered="false" class="mb-4">
        归档簿包含：封面（主体/期间/生成时间/数据指纹）、科目余额表、凭证清单、明细账、进销项发票台账、固定资产、工资表、三大报表。
        数据指纹由「期间 + 凭证张数 + 借贷合计」计算，用于核对导出的就是系统里的账。
      </NAlert>

      <NCard :bordered="false" class="report-card">
        <div class="section-head">
          <span class="section-title">历史归档</span>
          <span class="section-sub">文件保存在服务端，清理后需重新生成</span>
        </div>
        <NSpin :show="loading">
          <table class="report-table">
            <thead>
              <tr>
                <th style="width: 200px;">生成时间</th>
                <th style="width: 170px;">期间</th>
                <th style="width: 110px; text-align: right;">凭证张数</th>
                <th style="width: 110px; text-align: right;">大小</th>
                <th style="width: 230px;">数据指纹</th>
                <th style="width: 90px;">状态</th>
                <th style="width: 120px; text-align: right;">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="record in records" :key="record.id">
                <td>{{ record.createdAt ? formatDateTime(record.createdAt) : '—' }}</td>
                <td>{{ record.periodFrom }} ~ {{ record.periodTo }}</td>
                <td class="amount-cell">{{ record.voucherCount }}</td>
                <td class="amount-cell">{{ formatSize(record.sizeBytes) }}</td>
                <td class="mono">{{ record.fingerprint }}</td>
                <td>
                  <NTag size="small" :type="record.exists ? 'success' : 'default'" :bordered="false">
                    {{ record.exists ? '可用' : '已清理' }}
                  </NTag>
                </td>
                <td class="actions">
                  <NButton size="tiny" text type="primary" :disabled="!record.exists" @click="reDownload(record)">下载</NButton>
                </td>
              </tr>
              <tr v-if="records.length === 0">
                <td colspan="7" class="report-empty-cell is-component">
                  <SoloEmpty
                    variant="hint"
                    dense
                    title="还没有归档记录"
                    description="选择期间后点上方「生成并下载归档」。"
                  />
                </td>
              </tr>
            </tbody>
          </table>
        </NSpin>
      </NCard>
    </div>

    <div v-show="activeTab === 'audit'">
      <NCard :bordered="false" class="report-card">
        <AuditLogTab v-if="auditLoadedOnce" embedded />
      </NCard>
    </div>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.report-header { align-items: flex-start; }
.mode-tabs-wrap { display: flex; }
.backup-tabs { max-width: 320px; }
.generate-row { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.generate-row :deep(.n-form-item) { margin-bottom: 0; width: auto; flex: 0 0 auto; }
.generate-hint { font-size: 12px; color: var(--solo-text-muted); }
.section-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; }
.section-title { font-weight: 700; }
.section-sub { font-size: 12px; color: var(--solo-text-muted); }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.amount-cell { text-align: right; font-variant-numeric: tabular-nums; }
.actions { text-align: right; }
</style>
