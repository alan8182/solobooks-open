<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NDatePicker, useMessage } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { useAuthStore } from '@/stores/auth'
import { getRdProjects } from '@/api/rd'
import { getCurrentMonth } from '@/utils/format'
import VoucherSheet from '@/components/business/VoucherSheet.vue'
import { SoloEmpty } from '@/components/ui'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()
const authStore = useAuthStore()

const loading = ref(true)
const month = ref(String(route.query.month || getCurrentMonth()))
const rdProjectLabels = ref<Record<string, string>>({})
const selectedIds = computed(() =>
  String(route.query.ids || '')
    .split(',')
    .map((value) => value.trim())
    .filter(Boolean),
)

const companyName = computed(
  () => authStore.user?.companies.find((company) => company.company_id === authStore.currentCompanyId)?.name || '',
)

const entries = computed(() => {
  const all = entryStore.activeEntries.filter((entry) => entry.voucherStatus !== 'reversed')
  if (selectedIds.value.length > 0) {
    const wanted = new Set(selectedIds.value)
    return all.filter((entry) => wanted.has(entry.id))
  }
  return all
    .filter((entry) => String((entry as any).period || entry.occurDate || '').slice(0, 7) === month.value)
    .sort((a, b) => String(a.voucherRef || '').localeCompare(String(b.voucherRef || '')))
})

const totalAmount = computed(() =>
  entries.value.reduce((sum, entry) => sum + Number(entry.amount || 0), 0),
)

async function load() {
  loading.value = true
  try {
    await entryStore.fetchEntries()
    try {
      const projects = await getRdProjects(true)
      rdProjectLabels.value = Object.fromEntries(projects.map((item) => [item.id, `${item.code} · ${item.name}`]))
    } catch {
      // 研发项目名解析失败不影响打印
    }
  } catch {
    message.error('加载凭证失败')
  } finally {
    loading.value = false
  }
}

function print() {
  window.print()
}

function reloadByMonth() {
  router.replace({ path: '/vouchers/print', query: { month: month.value } })
  load()
}

onMounted(load)
</script>

<template>
  <div class="print-page">
    <div class="no-print toolbar">
      <NButton text @click="router.back()">← 返回</NButton>
      <div class="toolbar-center">
        <span class="toolbar-label">按期间连续打印</span>
        <NDatePicker v-model:formatted-value="month" type="month" value-format="yyyy-MM" format="yyyy-MM" style="width: 140px;" />
        <NButton size="small" @click="reloadByMonth">重载</NButton>
        <span class="count-hint">共 {{ entries.length }} 张凭证 · 合计 {{ totalAmount.toFixed(2) }}</span>
      </div>
      <NButton type="primary" @click="print">
        <template #icon><i class="ri-printer-line" /></template>
        打印
      </NButton>
    </div>

    <div v-if="!loading && entries.length === 0" class="empty no-print">
      <SoloEmpty
        title="该期间没有可打印的凭证"
        description="可从凭证列表勾选后再进入打印。"
        icon="ri-printer-line"
      />
    </div>

    <div class="sheet-stack">
      <VoucherSheet
        v-for="(entry, index) in entries"
        :key="entry.id"
        :entry="entry as unknown as Record<string, any>"
        :company-name="companyName"
        :rd-project-labels="rdProjectLabels"
        :page-break="index < entries.length - 1"
      />
    </div>
  </div>
</template>

<style scoped>
.print-page { max-width: 820px; margin: 0 auto; }
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
  padding: 10px 16px;
  background: #fff;
  border: 1px solid #e5e9ef;
  border-radius: 8px;
}
.toolbar-center { display: flex; align-items: center; gap: 10px; }
.toolbar-label, .count-hint { font-size: 12px; color: var(--solo-text-muted); }
.sheet-stack { display: flex; flex-direction: column; gap: 24px; }
.empty { padding: 48px; text-align: center; color: var(--solo-text-muted); background: #fff; border-radius: 8px; }
@media print {
  .print-page { max-width: 100%; }
  .sheet-stack { gap: 0; }
}
</style>
