<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NButton, NTag, NSpin, useMessage, NEmpty } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { generateVoucher } from '@/api/ledger'
import { formatMoney, formatDate } from '@/utils/format'

const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()

const loading = ref(true)
const generating = ref(false)

const voucheredEntries = computed(() =>
  entryStore.activeEntries.filter((e) => e.voucherStatus && e.voucherRef),
)

const unvoucheredEntries = computed(() =>
  entryStore.activeEntries.filter((e) => !e.voucherStatus || !e.voucherRef),
)

const displayedUnvoucheredEntries = computed(() => unvoucheredEntries.value.slice(0, 10))
const hiddenUnvoucheredCount = computed(() =>
  unvoucheredEntries.value.length - displayedUnvoucheredEntries.value.length,
)

async function handleGenerateAll() {
  const entriesToGenerate = unvoucheredEntries.value
  if (entriesToGenerate.length === 0) {
    message.warning('没有待生成的凭证')
    return
  }

  generating.value = true
  try {
    const resp = await generateVoucher(entriesToGenerate.map(entry => entry.id))
    if (resp.vouchers.length === 0) {
      message.warning('没有新增凭证需要生成')
    } else {
      message.success(`已生成 ${resp.vouchers.length} 份凭证`)
    }
    await entryStore.fetchEntries()
  } catch {
    message.error('生成凭证失败')
  } finally {
    generating.value = false
  }
}

onMounted(async () => {
  if (entryStore.activeEntries.length === 0) {
    await entryStore.fetchEntries()
  }
  loading.value = false
})
</script>

<template>
  <div class="voucher-page">
    <div class="page-header">
      <h2>凭证管理</h2>
      <NButton
        v-if="unvoucheredEntries.length > 0"
        size="small"
        type="primary"
        :loading="generating"
        @click="handleGenerateAll"
      >
        批量生成 {{ unvoucheredEntries.length }} 份凭证
      </NButton>
    </div>

    <NSpin :show="loading">
      <!-- Unvouchered entries -->
      <div v-if="unvoucheredEntries.length > 0" class="section mb-4">
        <div class="section-label-row">
          <div class="section-label">待生成凭证 ({{ unvoucheredEntries.length }})</div>
          <span v-if="hiddenUnvoucheredCount > 0" class="section-hint">
            仅预览前 10 条，批量生成会处理全部
          </span>
        </div>
        <NCard
          v-for="entry in displayedUnvoucheredEntries"
          :key="entry.id"
          :bordered="false"
          class="entry-card"
          @click="router.push(`/ledger/${entry.id}`)"
        >
          <div class="entry-info">
            <span class="entry-title">{{ entry.title }}</span>
            <span class="entry-amount">{{ formatMoney(entry.amount) }}</span>
          </div>
          <div class="entry-meta">
            <NTag size="tiny" :bordered="false">{{ entry.category }}</NTag>
            <span>{{ formatDate(entry.occurDate || entry.created_at || '') }}</span>
          </div>
        </NCard>
      </div>

      <!-- Vouchered entries -->
      <div v-if="voucheredEntries.length > 0" class="section">
        <div class="section-label">已生成凭证 ({{ voucheredEntries.length }})</div>
        <NCard
          v-for="entry in voucheredEntries"
          :key="entry.id"
          :bordered="false"
          class="entry-card"
          @click="router.push(`/vouchers/${entry.id}`)"
        >
          <div class="entry-info">
            <span class="entry-title">{{ entry.title }}</span>
            <span class="voucher-ref">{{ entry.voucherRef }}</span>
          </div>
          <div class="entry-meta">
            <NTag size="tiny" :bordered="false">{{ entry.category }}</NTag>
            <span class="entry-amount">{{ formatMoney(entry.amount) }}</span>
          </div>
        </NCard>
      </div>

      <div v-if="entryStore.activeEntries.length === 0 && !loading" class="empty-section">
        <NEmpty description="暂无数据" />
      </div>
    </NSpin>
  </div>
</template>

<style scoped>
.voucher-page {
  max-width: 720px;
  margin: 0 auto;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}
.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.mb-4 { margin-bottom: 16px; }
.section {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.section-label-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  flex-wrap: wrap;
  gap: 12px;
}
.section-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-secondary);
}
.section-hint {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.entry-card {
  border-radius: var(--solo-radius-lg);
  cursor: pointer;
}
.entry-card:hover {
  box-shadow: var(--solo-shadow);
}
.entry-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
}
.entry-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--solo-text-primary);
}
.voucher-ref {
  font-size: 12px;
  color: var(--solo-primary);
  font-family: 'Inter', monospace;
}
.entry-amount {
  font-size: 14px;
  font-weight: 700;
  color: var(--solo-text-primary);
}
.entry-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--solo-text-muted);
}
.empty-section {
  margin-top: 48px;
}
</style>
