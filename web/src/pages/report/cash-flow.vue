<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NSpin, useMessage, NSelect, NButton, NTag } from 'naive-ui'
import { getCashFlow } from '@/api/report'
import { formatMoney, getCurrentMonth } from '@/utils/format'
import type { CashFlowResponse } from '@/api/types'

const router = useRouter()
const message = useMessage()
const month = ref(getCurrentMonth())
const loading = ref(false)
const result = ref<CashFlowResponse | null>(null)

const monthOptions = computed(() => {
  const options: { label: string; value: string }[] = []
  const now = new Date()
  for (let i = 0; i < 12; i++) {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1)
    const val = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
    const label = `${d.getFullYear()}年${d.getMonth() + 1}月`
    options.push({ label, value: val })
  }
  return options
})

async function load() {
  loading.value = true
  try {
    result.value = await getCashFlow(month.value)
  } catch {
    message.error('加载现金流量表失败')
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
        <NButton text class="report-back-btn" @click="router.push('/reports')">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回
        </NButton>
        <div class="report-title-section">
          <h2>现金流量表</h2>
          <p class="report-subtitle">按经营、投资及筹资三分类活动，分析当期现金流量变动及净增加额</p>
        </div>
      </div>
      <div class="report-header-right">
        <NSelect
          v-model:value="month"
          :options="monthOptions"
          size="small"
          style="width: 140px;"
          @update:value="load"
        />
      </div>
    </div>

    <NSpin :show="loading">
      <template v-if="result">
        <!-- 经营活动 -->
        <NCard :bordered="false" class="report-card mb-4">
          <h3 class="report-section-title">经营活动现金流</h3>
          <div class="cf-row-group">
            <div v-for="item in result.operating" :key="item.description" class="cf-detail">
              <span class="cf-label">{{ item.description }}</span>
              <span class="cf-amt" :class="item.inflow > 0 ? 'income' : 'expense'">
                {{ item.inflow > 0 ? formatMoney(item.inflow) : formatMoney(-item.outflow) }}
              </span>
            </div>
            <div class="cf-divider" />
            <div class="cf-row bold">
              <span class="cf-label">经营活动产生现金流量净额</span>
              <span class="cf-amt" :class="result.operatingNet >= 0 ? 'income' : 'expense'">
                {{ formatMoney(result.operatingNet) }}
              </span>
            </div>
          </div>
        </NCard>

        <!-- 投资活动 -->
        <NCard :bordered="false" class="report-card mb-4">
          <h3 class="report-section-title">投资活动现金流</h3>
          <div class="cf-row-group">
            <div v-for="item in result.investing" :key="item.description" class="cf-detail">
              <span class="cf-label">{{ item.description }}</span>
              <span class="cf-amt" :class="item.inflow > 0 ? 'income' : item.outflow > 0 ? 'expense' : ''">
                {{ item.inflow > 0 ? formatMoney(item.inflow) : formatMoney(-item.outflow) }}
              </span>
            </div>
            <div class="cf-divider" />
            <div class="cf-row bold">
              <span class="cf-label">投资活动产生现金流量净额</span>
              <span class="cf-amt" :class="result.investingNet >= 0 ? 'income' : 'expense'">
                {{ formatMoney(result.investingNet) }}
              </span>
            </div>
          </div>
        </NCard>

        <!-- 筹资活动 -->
        <NCard :bordered="false" class="report-card mb-4">
          <h3 class="report-section-title">筹资活动现金流</h3>
          <div class="cf-row-group">
            <div v-for="item in result.financing" :key="item.description" class="cf-detail">
              <span class="cf-label">{{ item.description }}</span>
              <span class="cf-amt" :class="item.inflow > 0 ? 'income' : item.outflow > 0 ? 'expense' : ''">
                {{ item.inflow > 0 ? formatMoney(item.inflow) : formatMoney(-item.outflow) }}
              </span>
            </div>
            <div class="cf-divider" />
            <div class="cf-row bold">
              <span class="cf-label">筹资活动产生现金流量净额</span>
              <span class="cf-amt" :class="result.financingNet >= 0 ? 'income' : 'expense'">
                {{ formatMoney(result.financingNet) }}
              </span>
            </div>
          </div>
        </NCard>

        <!-- 汇总 -->
        <NCard :bordered="false" class="report-card check-card">
          <div class="cf-row">
            <span class="cf-label-summary">加：期初现金余额</span>
            <span class="cf-amt">{{ formatMoney(result.openingCash) }}</span>
          </div>
          <div class="cf-row bold large">
            <span class="cf-label-summary">本期现金及现金等价物净增加额</span>
            <span class="cf-amt-summary" :class="result.netIncrease >= 0 ? 'income' : 'expense'">
              {{ formatMoney(result.netIncrease) }}
            </span>
          </div>
          <div class="cf-divider" />
          <div class="cf-row bold large">
            <span class="cf-label-summary">期末现金余额</span>
            <span class="cf-amt-summary">{{ formatMoney(result.endingCash) }}</span>
          </div>
          <div class="reconciliation-list">
            <div class="reconciliation-row">
              <span>三类活动净额＝现金净增加额</span>
              <NTag size="small" :type="result.classificationBalanced ? 'success' : 'error'">
                {{ result.classificationBalanced ? '已勾稽' : `差额 ${formatMoney(result.classificationDifference)}` }}
              </NTag>
            </div>
            <div class="reconciliation-row">
              <span>期初现金＋净增加额＝期末现金</span>
              <NTag size="small" :type="result.cashRollForwardBalanced ? 'success' : 'error'">
                {{ result.cashRollForwardBalanced ? '已勾稽' : `差额 ${formatMoney(result.cashRollForwardDifference)}` }}
              </NTag>
            </div>
            <div class="reconciliation-row">
              <span>期末现金＝资产负债表货币资金</span>
              <NTag size="small" :type="result.balanceSheetCashBalanced ? 'success' : 'error'">
                {{ result.balanceSheetCashBalanced ? '已勾稽' : `差额 ${formatMoney(result.balanceSheetCashDifference)}` }}
              </NTag>
            </div>
          </div>
        </NCard>
      </template>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.cf-row-group {
  padding: 4px 12px;
}
.cf-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  font-size: 13.5px;
}
.cf-detail {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 6px 0 6px 16px;
  font-size: 13px;
  border-left: 2px solid var(--solo-border-light);
  margin-left: 4px;
  background: rgba(0,0,0,0.005);
}
.cf-label {
  color: var(--solo-text-secondary);
}
.cf-amt {
  font-family: 'Outfit', 'Inter', monospace;
  font-weight: 600;
  white-space: nowrap;
}
.cf-empty {
  padding: 10px 16px;
  color: var(--solo-text-muted);
  font-size: 13px;
  font-style: italic;
}
.cf-divider {
  height: 1px;
  background: var(--solo-border-light);
  margin: 6px 0;
}
.bold .cf-label,
.bold .cf-amt {
  font-weight: 750;
  color: var(--solo-text-primary);
}
.check-card {
  padding: 16px 20px;
}
.cf-label-summary {
  font-size: 14.5px;
  font-weight: 750;
}
.cf-amt-summary {
  font-size: 20px;
  font-weight: 800;
  font-family: 'Outfit', 'Inter', sans-serif;
}
.income { color: var(--solo-income); }
.expense { color: var(--solo-expense); }
.reconciliation-list {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--solo-border-light);
}
.reconciliation-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 6px 0;
  color: var(--solo-text-secondary);
  font-size: 13px;
}
</style>
