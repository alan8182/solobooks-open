<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, useMessage } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { generateVoucherLines, normalizeVoucherLines } from '@/utils/accounting'
import { formatMoney, formatDate, amountToChineseCapital } from '@/utils/format'
import { getRdExpenseTypes, getRdProjects } from '@/api/rd'
import type { RdExpenseTypeOption, RdProject } from '@/api/types'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()
const authStore = useAuthStore()

const entry = ref<(typeof entryStore.activeEntries)[0] | null>(null)
const loading = ref(true)
const rdProjects = ref<RdProject[]>([])
const rdExpenseTypes = ref<RdExpenseTypeOption[]>([])

const companyName = computed(() =>
  authStore.user?.companies.find((c) => c.company_id === authStore.currentCompanyId)?.name || '',
)
const dateCN = computed(() =>
  formatDate(entry.value?.occurDate || entry.value?.created_at || '', 'YYYY年MM月DD日'),
)
// 「记-202609-001」→「记字第1号」；解析失败回退显示原凭证号
const voucherSeq = computed(() => {
  const m = (entry.value?.voucherRef || '').match(/(\d+)\s*$/)
  return m ? String(Number(m[1])) : entry.value?.voucherRef || '—'
})
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
const capitalAmount = computed(() => amountToChineseCapital(Math.max(totalDebit.value, totalCredit.value)))
// 固定行数占位：分录少于 6 行时补空行撑满版面（传统凭证样式）
const blankRowCount = computed(() => Math.max(0, 6 - voucherLines.value.length))
const rdVoucherLines = computed(() => voucherLines.value.filter(line => line.rdDetail))

// 凭证金额列不带 ¥ 前缀（对齐传统凭证版式）
function plainMoney(value: number) {
  return Number(value || 0).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

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
      <div class="voucher-company">{{ companyName }}</div>
      <div class="voucher-title-wrap">
        <span class="voucher-title">记 账 凭 证</span>
      </div>
      <div class="voucher-meta">
        <span class="meta-date">{{ dateCN }}</span>
        <span class="meta-ref">记字第{{ voucherSeq }}号</span>
      </div>

      <table class="voucher-table">
        <thead>
          <tr>
            <th class="col-summary">摘要</th>
            <th class="col-account">科目</th>
            <th class="col-amount">借方金额</th>
            <th class="col-amount">贷方金额</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(line, i) in voucherLines" :key="i">
            <td class="cell-summary" :title="line.code">{{ entry.title }}</td>
            <td>{{ line.name }}</td>
            <td class="amount">{{ line.side === 'debit' ? plainMoney(line.amount) : '' }}</td>
            <td class="amount">{{ line.side === 'credit' ? plainMoney(line.amount) : '' }}</td>
          </tr>
          <!-- 空行仅打印时显示：纸质凭证惯例（固定版面/防加行篡改），屏幕上无意义 -->
          <tr v-for="n in blankRowCount" :key="`blank-${n}`" class="blank-row">
            <td>&nbsp;</td><td></td><td></td><td></td>
          </tr>
        </tbody>
        <tfoot>
          <tr>
            <td colspan="2" class="total-capital">合　计：{{ capitalAmount }}</td>
            <td class="amount total">{{ plainMoney(totalDebit) }}</td>
            <td class="amount total">{{ plainMoney(totalCredit) }}</td>
          </tr>
        </tfoot>
      </table>

      <div class="voucher-sign">
        <span class="sign-item">财务主管</span>
        <span class="sign-item">复核 {{ entry.reviewedByName || '' }}</span>
        <span class="sign-item">记账 {{ entry.postedByName || '' }}</span>
        <span class="sign-item">出纳</span>
        <span class="sign-item">经办</span>
        <span class="sign-item">制单 {{ entry.createdByName || '' }}</span>
      </div>

      <!-- 研发归集附注：不进凭证主体，打印时位于签章行之后 -->
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
    </div>
  </div>
</template>

<style scoped>
.voucher-detail-page {
  max-width: 760px;
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

/* 抬头：公司名 + 标题 + 日期/凭证号 */
.voucher-company {
  text-align: center;
  font-size: 15px;
  font-weight: 500;
  color: var(--solo-text-primary);
}
.voucher-title-wrap {
  text-align: center;
  margin: 6px 0 14px;
}
.voucher-title {
  display: inline-block;
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 10px;
  margin-right: -10px;
  padding: 0 2px 6px;
  border-bottom: 3px solid var(--solo-text-primary);
}
.voucher-meta {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  margin-bottom: 14px;
  font-size: 13px;
}
.meta-date {
  grid-column: 2;
  color: var(--solo-text-primary);
}
.meta-ref {
  grid-column: 3;
  text-align: right;
  font-weight: 500;
}

/* 凭证表格：摘要 | 科目 | 借方金额 | 贷方金额 */
.voucher-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.voucher-table th,
.voucher-table td {
  border: 1px solid #9ca3af;
}
.voucher-table th {
  padding: 10px 12px;
  text-align: center;
  font-weight: 600;
  color: var(--solo-text-primary);
  font-size: 13px;
}
.voucher-table td {
  padding: 10px 12px;
  text-align: left;
  vertical-align: top;
}
.col-summary { width: 22%; }
.col-account { width: 42%; }
.col-amount { width: 18%; }
.cell-summary {
  color: var(--solo-text-primary);
}
.amount {
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.total-capital {
  font-weight: 700;
}
.total {
  font-weight: 700;
}

/* 空行占位仅打印输出，屏幕隐藏 */
.blank-row {
  display: none;
}

/* 签章行 */
.voucher-sign {
  display: flex;
  justify-content: space-between;
  margin-top: 22px;
  font-size: 13px;
  color: var(--solo-text-primary);
}

/* 研发归集附注 */
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

/* Risk tags */
.risk-tags {
  display: flex;
  gap: 6px;
  margin-top: 16px;
}
.risk-tag {
  font-size: 11px;
  color: var(--solo-expense);
  background: #fef2f2;
  padding: 2px 8px;
  border-radius: 4px;
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
  .voucher-title {
    border-bottom-color: #000;
  }
  .voucher-table th,
  .voucher-table td {
    border-color: #000;
  }
  .blank-row {
    display: table-row;
  }
}
</style>