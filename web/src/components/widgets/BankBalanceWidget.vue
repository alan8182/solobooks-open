<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import apiClient from '@/api/client'
import { useAuthStore } from '@/stores/auth'
import { usePageRefresh } from '@/composables/usePageRefresh'
import { formatMoney } from '@/utils/format'
import { SoloSkeleton } from '@/components/ui'

const router = useRouter()
const authStore = useAuthStore()

const loading = ref(false)
const hasStatements = ref(false)
const bankBalance = ref<number>(0)
const unmatchedCount = ref(0)
const fullyReconciled = ref(false)
const latestDate = ref('')

async function loadBankData() {
  loading.value = true
  try {
    const month = new Date().toISOString().slice(0, 7)
    const [statementsRes, diffRes] = await Promise.allSettled([
      apiClient.get('/api/bank/statements'),
      apiClient.get('/api/bank/difference', { params: { month } }),
    ])

    let foundStatements = false
    if (statementsRes.status === 'fulfilled') {
      const items = (statementsRes.value as any)?.items || []
      if (Array.isArray(items) && items.length > 0) {
        foundStatements = true
        hasStatements.value = true
        // 按照日期与行序升序排列，取最新一笔流水的实时结存余额
        const sorted = [...items].sort((a, b) => {
          const dateCmp = String(a.transaction_date || '').localeCompare(String(b.transaction_date || ''))
          if (dateCmp !== 0) return dateCmp
          const rowA = a.source_row_index ?? 0
          const rowB = b.source_row_index ?? 0
          if (rowA !== rowB) return rowA - rowB
          return String(a.id || '').localeCompare(String(b.id || ''))
        })
        const latest = sorted[sorted.length - 1]
        bankBalance.value = Number(latest.balance ?? 0)
        latestDate.value = String(latest.transaction_date || '').slice(0, 10)
      } else {
        hasStatements.value = false
      }
    }

    if (diffRes.status === 'fulfilled') {
      const diffData = diffRes.value as any
      const recon = diffData?.balance_reconciliation
      unmatchedCount.value = Number(diffData?.bank_only_count || 0) + Number(diffData?.book_only_count || 0)
      fullyReconciled.value = Boolean(recon?.fully_reconciled)

      // 如果尚未导入银行流水但账本银行存款有期初/记账余额，降级透出账本银行存款
      if (!foundStatements && recon?.book_bank_balance != null) {
        bankBalance.value = Number(recon.book_bank_balance || 0)
      }
    }
  } catch {
    // 失败时保持静默兜底，不阻断概览渲染
  } finally {
    loading.value = false
  }
}

onMounted(loadBankData)
watch(() => authStore.currentCompanyId, loadBankData)
usePageRefresh(loadBankData)

const statusMeta = computed(() => {
  if (!hasStatements.value && bankBalance.value === 0) {
    return {
      text: '待导入',
      className: 'status-empty',
      icon: 'ri-upload-2-line',
      hint: '点击上传流水，自动对账入账',
    }
  }
  if (unmatchedCount.value > 0) {
    return {
      text: `待核对 ${unmatchedCount.value} 笔`,
      className: 'status-warning',
      icon: 'ri-alert-line',
      hint: '存在未匹配收支，点击核对',
    }
  }
  if (fullyReconciled.value) {
    return {
      text: '账银已对齐',
      className: 'status-success',
      icon: 'ri-checkbox-circle-line',
      hint: latestDate.value ? `最新流水截至 ${latestDate.value}` : '本月流水与记账完全吻合',
    }
  }
  return {
    text: '已对齐',
    className: 'status-normal',
    icon: 'ri-bank-line',
    hint: latestDate.value ? `最新流水截至 ${latestDate.value}` : '点击查看公户收支流水',
  }
})

function handleClick() {
  router.push('/bank')
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    handleClick()
  }
}
</script>

<template>
  <div
    class="bank-balance-widget"
    role="button"
    tabindex="0"
    aria-label="公司公户实时余额，点击进入银行流水与对账"
    @click="handleClick"
    @keydown="handleKeydown"
  >
    <div class="widget-header">
      <div class="widget-title">
        <i class="ri-bank-line title-icon" aria-hidden="true" />
        <span>公司公户实时余额</span>
      </div>
      <div class="status-pill" :class="statusMeta.className">
        <i :class="statusMeta.icon" aria-hidden="true" />
        <span>{{ statusMeta.text }}</span>
      </div>
    </div>

    <div class="widget-body">
      <SoloSkeleton v-if="loading && !hasStatements && bankBalance === 0" variant="text" width="60%" />
      <span v-else class="balance-amount" :title="formatMoney(bankBalance)">
        {{ formatMoney(bankBalance) }}
      </span>
    </div>

    <div class="widget-footer">
      <span class="footer-hint">{{ statusMeta.hint }}</span>
      <span class="footer-action">
        <span>去对账</span>
        <i class="ri-arrow-right-line" aria-hidden="true" />
      </span>
    </div>
  </div>
</template>

<style scoped>
.bank-balance-widget {
  padding: 22px 24px;
  border-left: 1px solid var(--solo-border-light);
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  gap: 12px;
  background: var(--solo-bg-card);
  cursor: pointer;
  outline: none;
  transition: background-color 0.18s ease-out, border-color 0.18s ease-out;
  user-select: none;
}

.bank-balance-widget:hover {
  background: var(--solo-bg-blue-soft);
}

.bank-balance-widget:focus-visible {
  outline: 2px solid var(--solo-primary);
  outline-offset: -2px;
}

.widget-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.widget-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 650;
  color: var(--solo-text-secondary);
}

.title-icon {
  color: var(--solo-primary);
  font-size: 15px;
}

.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 7px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
  line-height: 1.3;
}

.status-pill.status-success {
  background: var(--solo-income-bg);
  color: var(--solo-income);
}

.status-pill.status-warning {
  background: var(--solo-warning-bg);
  color: var(--solo-warning);
}

.status-pill.status-empty,
.status-pill.status-normal {
  background: var(--solo-bg-soft);
  color: var(--solo-text-muted);
}

.widget-body {
  display: flex;
  align-items: baseline;
  min-width: 0;
}

.balance-amount {
  font-family: 'Outfit', 'Inter', sans-serif;
  font-size: 26px;
  font-weight: 700;
  color: var(--solo-text-primary);
  letter-spacing: -0.4px;
  line-height: 1.2;
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.widget-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding-top: 6px;
  border-top: 1px dashed var(--solo-border-light);
}

.footer-hint {
  font-size: 11.5px;
  color: var(--solo-text-muted);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.footer-action {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--solo-primary);
  flex-shrink: 0;
  transition: transform 0.16s ease-out;
}

.bank-balance-widget:hover .footer-action {
  transform: translateX(2px);
}
</style>
