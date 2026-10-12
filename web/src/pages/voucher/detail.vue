<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NTag, useMessage } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { generateVoucherLines, normalizeVoucherLines } from '@/utils/accounting'
import { formatMoney, formatDate } from '@/utils/format'
import { getRdExpenseTypes, getRdProjects } from '@/api/rd'
import type { RdExpenseTypeOption, RdProject } from '@/api/types'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()

const entry = ref<(typeof entryStore.activeEntries)[0] | null>(null)
const loading = ref(true)
const rdProjects = ref<RdProject[]>([])
const rdExpenseTypes = ref<RdExpenseTypeOption[]>([])

const voucherLines = computed(() => {
  if (!entry.value) return []
  const storedLines = normalizeVoucherLines((entry.value as any).voucherLines)
  if (storedLines.length > 0) return storedLines
  return generateVoucherLines({
    type: entry.value.direction as 'income' | 'expense' | 'owner_transfer',
    amount: entry.value.amount,
    category: entry.value.category || '',
    payer: (entry.value.payer as 'company_account' | 'boss_personal') || 'company_account',
  })
})

const totalDebit = computed(() =>
  voucherLines.value.filter((l) => l.side === 'debit').reduce((s, l) => s + l.amount, 0),
)
const totalCredit = computed(() =>
  voucherLines.value.filter((l) => l.side === 'credit').reduce((s, l) => s + l.amount, 0),
)
const rdVoucherLines = computed(() => voucherLines.value.filter(line => line.rdDetail))

function rdProjectLabel(projectId: string) {
  const project = rdProjects.value.find(item => item.id === projectId)
  return project ? `${project.code} · ${project.name}` : projectId
}

function rdExpenseTypeLabel(expenseType: string) {
  return rdExpenseTypes.value.find(item => item.value === expenseType)?.label || expenseType
}

async function loadRdReferences() {
  try {
    const [projects, types] = await Promise.all([getRdProjects(true), getRdExpenseTypes()])
    rdProjects.value = projects
    rdExpenseTypes.value = types.items
  } catch {
    // 打印凭证仍可使用项目 ID 和费用类型代码。
  }
}

function handlePrint() {
  window.print()
}

onMounted(async () => {
  await loadRdReferences()
  const id = route.params.id as string
  if (entryStore.activeEntries.length === 0) {
    await entryStore.fetchEntries()
  }
  let found = entryStore.activeEntries.find((e) => e.id === id)
  if (!found) {
    // 缓存可能过期，尝试重新从后端获取最新记录
    await entryStore.fetchEntries()
    found = entryStore.activeEntries.find((e) => e.id === id)
  }
  if (!found) {
    message.error('未找到记录')
    router.push('/vouchers')
    return
  }
  entry.value = found
  loading.value = false
})
</script>

<template>
  <div class="voucher-detail-page" v-if="!loading && entry">
    <!-- Toolbar (hidden when printing) -->
    <div class="no-print toolbar">
      <NButton text @click="router.push('/vouchers')">← 返回凭证列表</NButton>
      <NButton type="primary" @click="handlePrint">打印凭证</NButton>
    </div>

    <!-- Voucher printable area -->
    <div class="print-area">
      <div class="voucher-title">记 账 凭 证</div>

      <div class="voucher-header-info">
        <div class="info-row">
          <span class="info-label">凭证号：</span>
          <span class="info-value">{{ entry.voucherRef || '—' }}</span>
        </div>
        <div class="info-row">
          <span class="info-label">日期：</span>
          <span class="info-value">{{ formatDate(entry.occurDate || entry.created_at || '') }}</span>
        </div>
        <div class="info-row">
          <span class="info-label">摘要：</span>
          <span class="info-value">{{ entry.title }}</span>
        </div>
      </div>

      <table class="voucher-table">
        <thead>
          <tr>
            <th class="col-code">科目编码</th>
            <th class="col-name">科目名称</th>
            <th class="col-side">方向</th>
            <th class="col-amount">金额</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(line, i) in voucherLines" :key="i">
            <td>{{ line.code }}</td>
            <td>{{ line.name }}</td>
            <td>
              <NTag size="tiny" :type="line.side === 'debit' ? 'error' : 'success'" :bordered="false">
                {{ line.side === 'debit' ? '借方' : '贷方' }}
              </NTag>
            </td>
            <td class="amount">{{ formatMoney(line.amount) }}</td>
          </tr>
        </tbody>
        <tfoot>
          <tr>
            <td colspan="3" class="total-label">合计</td>
            <td class="amount total">{{ formatMoney(Math.max(totalDebit, totalCredit)) }}</td>
          </tr>
        </tfoot>
      </table>

      <div v-if="rdVoucherLines.length" class="rd-section">
        <div class="rd-section-title">研发费用归集</div>
        <div v-for="line in rdVoucherLines" :key="`${line.code}-${line.rdDetail?.projectId}`" class="rd-line">
          <span>项目：{{ rdProjectLabel(line.rdDetail!.projectId) }}</span>
          <span>类别：{{ rdExpenseTypeLabel(line.rdDetail!.expenseType) }}</span>
          <span>研发金额：{{ formatMoney(line.rdDetail!.rdAmount ?? line.amount) }}</span>
          <span>原金额：{{ formatMoney(line.rdDetail!.originalAmount) }}</span>
          <span>分摊：{{ line.rdDetail!.allocationMethod }}<template v-if="line.rdDetail!.allocationRate">（{{ Number(line.rdDetail!.allocationRate) * 100 }}%）</template></span>
          <span>依据：{{ line.rdDetail!.allocationBasis || '直接归集' }}</span>
        </div>
      </div>

      <div v-if="entry.riskTags?.length" class="risk-tags">
        <span v-for="tag in entry.riskTags" :key="tag" class="risk-tag">{{ tag }}</span>
      </div>

      <div class="voucher-footer">
        <div class="footer-row">
          <span>制单人：</span>
          <span class="footer-name">{{ entry.createdByName || '—' }}</span>
        </div>
        <div class="footer-row">
          <span>审核人：</span>
          <span class="footer-name">{{ entry.reviewedByName || '—' }}</span>
        </div>
        <div class="footer-row">
          <span>记账人：</span>
          <span class="footer-name">{{ entry.postedByName || '—' }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.voucher-detail-page {
  max-width: 680px;
  margin: 0 auto;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

/* Print area */
.print-area {
  background: #fff;
  padding: 32px 36px;
  border-radius: var(--solo-radius-lg);
  box-shadow: var(--solo-shadow);
}
.voucher-title {
  text-align: center;
  font-size: 20px;
  font-weight: 700;
  letter-spacing: 6px;
  margin-bottom: 24px;
  color: var(--solo-text-primary);
}
.rd-section {
  margin-top: 16px;
  padding: 12px;
  border: 1px solid var(--solo-border-light);
  border-radius: 6px;
  font-size: 12px;
}
.rd-section-title {
  font-weight: 700;
  margin-bottom: 8px;
}
.rd-line {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 16px;
}
.rd-line + .rd-line {
  border-top: 1px solid var(--solo-border-light);
  margin-top: 8px;
  padding-top: 8px;
}
.voucher-header-info {
  display: flex;
  flex-wrap: wrap;
  gap: 24px;
  margin-bottom: 20px;
  font-size: 13px;
}
.info-row {
  display: flex;
  align-items: center;
  gap: 4px;
}
.info-label {
  color: var(--solo-text-muted);
}
.info-value {
  color: var(--solo-text-primary);
  font-weight: 500;
}

/* Table */
.voucher-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
  margin-bottom: 16px;
}
.voucher-table th,
.voucher-table td {
  padding: 10px 12px;
  text-align: left;
  border: 1px solid #d1d5db;
}
.voucher-table th {
  background: #f9fafb;
  font-weight: 600;
  color: var(--solo-text-secondary);
  font-size: 12px;
}
.col-code { width: 20%; }
.col-name { width: 35%; }
.col-side { width: 15%; }
.col-amount { width: 30%; text-align: right; }
.amount {
  text-align: right;
  font-weight: 600;
  font-family: 'Inter', monospace;
}
.total {
  font-size: 14px;
}

/* Risk tags */
.risk-tags {
  display: flex;
  gap: 6px;
  margin-bottom: 16px;
}
.risk-tag {
  font-size: 11px;
  color: var(--solo-expense);
  background: #fef2f2;
  padding: 2px 8px;
  border-radius: 4px;
}

/* Footer */
.voucher-footer {
  display: flex;
  justify-content: flex-end;
  gap: 40px;
  margin-top: 24px;
  padding-top: 16px;
}
.footer-row {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: var(--solo-text-muted);
}
.footer-name {
  display: inline-block;
  min-width: 80px;
  border-bottom: 1px solid #d1d5db;
  text-align: center;
  font-weight: 500;
  color: var(--solo-text-primary, #182230);
}

/* Print styles */
@media print {
  .no-print { display: none; }
  .voucher-detail-page {
    max-width: 100%;
    margin: 0;
    padding: 0;
  }
  .print-area {
    box-shadow: none;
    padding: 40px;
    border-radius: 0;
  }
  .voucher-table th,
  .voucher-table td {
    border-color: #000;
  }
}
</style>
