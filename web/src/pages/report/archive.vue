<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NCard, NTag, NSpin, useMessage, NSelect, NButton } from 'naive-ui'
import { SoloEmpty } from '@/components/ui'
import { useEntryStore } from '@/stores/entry'
import { getArchiveChecklist } from '@/api/report'
import { formatMoney, formatDate, getCurrentMonth } from '@/utils/format'
import type { ArchiveChecklistResponse } from '@/api/types'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()

const requestedMonth = String(route.query.month || '')
const hasRequestedMonth = /^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth)
const month = ref(hasRequestedMonth ? requestedMonth : getCurrentMonth())
const loading = ref(false)
const result = ref<ArchiveChecklistResponse | null>(null)

const monthOptions = computed(() => {
  const opts: { label: string; value: string }[] = []
  const now = new Date()
  for (let i = 0; i < 12; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1)
    const val = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
    opts.push({ label: `${d.getFullYear()}年${d.getMonth() + 1}月`, value: val })
  }
  return opts
})

function handleMonthChange() {
  router.replace({ query: { ...route.query, month: month.value } })
  load()
}

function handleBack() {
  router.push({ path: '/reports', query: { month: month.value } })
}

function complianceConfig(status: string) {
  const map: Record<string, { label: string; type: 'success' | 'error' | 'warning' | 'info' }> = {
    compliant: { label: '合规', type: 'success' },
    missing: { label: '缺失', type: 'error' },
    incomplete: { label: '不完整', type: 'warning' },
    questionable: { label: '存疑', type: 'warning' },
  }
  return map[status] || { label: status, type: 'info' }
}

async function load() {
  loading.value = true
  try {
    if (entryStore.activeEntries.length === 0) {
      await entryStore.fetchEntries()
    }
    result.value = await getArchiveChecklist(month.value)
  } catch {
    message.error('加载归档清单失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="report-container">
    <div class="report-header">
      <div class="report-header-left">
        <NButton text class="report-back-btn" @click="handleBack">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回报表中心
        </NButton>
        <div class="report-title-section">
          <h2>单据归档清单</h2>
          <p class="report-subtitle">检查凭证单据的完备度与合规度，辅助企业建立规范的档案归档机制</p>
        </div>
      </div>
      <div class="report-header-right">
        <NSelect
          v-model:value="month"
          :options="monthOptions"
          size="small"
          style="width: 140px;"
          @update:value="handleMonthChange"
        />
      </div>
    </div>

    <NSpin :show="loading">
      <template v-if="result">
        <!-- Stats Dashboard -->
        <NCard :bordered="false" class="report-card mb-4">
          <div class="report-summary-row">
            <div class="report-summary-item">
              <span class="report-summary-label">合规单据</span>
              <span class="report-summary-val income">{{ result.compliantCount }} 笔</span>
            </div>
            <div class="report-summary-item">
              <span class="report-summary-label">待补充/缺失</span>
              <span class="report-summary-val expense">{{ result.missingCount }} 笔</span>
            </div>
            <div class="report-summary-item">
              <span class="report-summary-label">总计交易笔数</span>
              <span class="report-summary-val">{{ result.totalCount }} 笔</span>
            </div>
          </div>
        </NCard>

        <!-- Items Table -->
        <NCard :bordered="false" class="report-card" v-if="result.items.length > 0">
          <h3 class="report-section-title">交易凭证合规清单</h3>
          <div class="report-table-wrapper">
            <table class="report-table">
              <thead>
                <tr>
                  <th style="width: 120px;">发生日期</th>
                  <th>摘要说明</th>
                  <th style="text-align: right; width: 140px;">交易金额</th>
                  <th style="width: 120px;">账目分类</th>
                  <th style="width: 160px;">合规状态</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(item, i) in result.items" :key="i">
                  <td>{{ formatDate(item.date) }}</td>
                  <td>{{ item.summary }}</td>
                  <td class="amount-cell">{{ formatMoney(item.amount) }}</td>
                  <td>{{ item.docType }}</td>
                  <td>
                    <NSpace align="center" size="small">
                      <NTag size="tiny" :type="complianceConfig(item.compliance).type" :bordered="false" round>
                        {{ complianceConfig(item.compliance).label }}
                      </NTag>
                      <NTag v-if="item.riskTag" size="tiny" type="error" :bordered="false" round>
                        {{ item.riskTag }}
                      </NTag>
                    </NSpace>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </NCard>

        <div v-else class="empty-section mt-4">
          <SoloEmpty
            title="该月份没有记账数据"
            description="没有账就无需归档自查。换一个有凭证的月份，或先去记账。"
            icon="ri-archive-line"
          />
        </div>
      </template>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.mt-4 { margin-top: 16px; }
.empty-section {
  padding: 48px;
  background: var(--solo-bg-card);
  border: 1px dashed var(--solo-border);
  border-radius: var(--solo-radius-lg);
  display: flex;
  justify-content: center;
}
</style>
