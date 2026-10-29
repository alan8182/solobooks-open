<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
/**
 * 凭证版式（屏幕与打印共用）。
 *
 * 从 voucher/detail.vue 抽出：详情页与连续打印页必须是同一套版式，
 * 否则「打印出来的和屏幕上看到的不一样」——这是凭证被质疑时最难解释的问题。
 */
import { computed } from 'vue'
import { formatDate, formatMoney, amountToChineseCapital } from '@/utils/format'
import { generateVoucherLines, normalizeVoucherLines } from '@/utils/accounting'

const props = withDefaults(
  defineProps<{
    entry: Record<string, any>
    companyName?: string
    rdProjectLabels?: Record<string, string>
    showBlankRows?: boolean
    pageBreak?: boolean
  }>(),
  { companyName: '', rdProjectLabels: () => ({}), showBlankRows: true, pageBreak: false },
)

const RD_TYPE_LABELS: Record<string, string> = {
  personnel: '人员人工',
  direct_input: '直接投入',
  depreciation_amortization: '折旧与摊销',
  design: '设计费',
  testing: '试验费',
  outsourced: '委外研发费',
  other: '其他研发费用',
}

const voucherLines = computed(() => {
  const stored = normalizeVoucherLines(props.entry?.voucherLines)
  if (stored.length > 0) return stored
  return generateVoucherLines({
    type: (props.entry?.direction || 'expense') as 'income' | 'expense' | 'owner_transfer',
    amount: Number(props.entry?.amount || 0),
    category: props.entry?.category || '',
    payer: (props.entry?.payer as 'company_account' | 'boss_personal') || 'company_account',
  })
})

const totalDebit = computed(() =>
  voucherLines.value.filter((line) => line.side === 'debit').reduce((sum, line) => sum + Number(line.amount), 0),
)
const totalCredit = computed(() =>
  voucherLines.value.filter((line) => line.side === 'credit').reduce((sum, line) => sum + Number(line.amount), 0),
)
const capitalAmount = computed(() => amountToChineseCapital(Math.max(totalDebit.value, totalCredit.value)))
const blankRowCount = computed(() => (props.showBlankRows ? Math.max(0, 6 - voucherLines.value.length) : 0))
const rdVoucherLines = computed(() => voucherLines.value.filter((line) => line.rdDetail))
const dateCN = computed(() =>
  formatDate(props.entry?.occurDate || props.entry?.created_at || '', 'YYYY年MM月DD日'),
)
const voucherSeq = computed(() => {
  const matched = String(props.entry?.voucherRef || '').match(/(\d+)\s*$/)
  return matched ? String(Number(matched[1])) : props.entry?.voucherRef || '—'
})

function plainMoney(value: number) {
  return Number(value || 0).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

function auxiliaryText(line: any): string {
  const auxiliary = line?.auxiliary || {}
  const parts = [auxiliary.partner_name, auxiliary.department_name, auxiliary.project_name].filter(Boolean)
  return parts.join(' / ')
}
</script>

<template>
  <div class="voucher-sheet" :class="{ 'page-break': pageBreak }">
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
        <tr v-for="(line, index) in voucherLines" :key="index">
          <td class="cell-summary" :title="line.code">{{ entry.title }}</td>
          <td>
            <div class="line-name-row">
              <span>{{ line.name }}</span>
              <span v-if="line.code === '2221026' && line.side === 'credit'" class="vat-exempt-tag">
                💡 季≤30万免税（结账自动转出）
              </span>
            </div>
            <div v-if="auxiliaryText(line)" class="aux-hint">辅助：{{ auxiliaryText(line) }}</div>
          </td>
          <td class="amount">
            {{ line.side === 'debit' ? plainMoney(Number(line.amount)) : '' }}
            <div v-if="line.side === 'debit' && line.quantity != null" class="qty-hint">
              ×{{ line.quantity }}{{ line.unit || '' }}<template v-if="line.unitPrice != null"> @{{ plainMoney(line.unitPrice) }}</template>
            </div>
          </td>
          <td class="amount">
            {{ line.side === 'credit' ? plainMoney(Number(line.amount)) : '' }}
            <div v-if="line.side === 'credit' && line.quantity != null" class="qty-hint">
              ×{{ line.quantity }}{{ line.unit || '' }}<template v-if="line.unitPrice != null"> @{{ plainMoney(line.unitPrice) }}</template>
            </div>
          </td>
        </tr>
        <tr v-for="n in blankRowCount" :key="`blank-${n}`" class="blank-row">
          <td>&nbsp;</td>
          <td></td>
          <td></td>
          <td></td>
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

    <div v-if="rdVoucherLines.length" class="rd-section">
      <div class="rd-section-title">研发费用归集</div>
      <div v-for="line in rdVoucherLines" :key="`${line.code}-${line.rdDetail?.projectId}`" class="rd-line">
        <span>项目：{{ rdProjectLabels[line.rdDetail!.projectId] || line.rdDetail!.projectId }}</span>
        <span>类别：{{ RD_TYPE_LABELS[line.rdDetail!.expenseType] || line.rdDetail!.expenseType }}</span>
        <span>研发金额：{{ formatMoney(Number(line.rdDetail!.rdAmount ?? line.amount)) }}</span>
        <span>原金额：{{ formatMoney(Number(line.rdDetail!.originalAmount)) }}</span>
        <span>分摊：{{ line.rdDetail!.allocationMethod }}<template v-if="line.rdDetail!.allocationRate">（{{ Number(line.rdDetail!.allocationRate) * 100 }}%）</template></span>
        <span>依据：{{ line.rdDetail!.allocationBasis || '直接归集' }}</span>
      </div>
    </div>

    <div v-if="entry.riskTags?.length" class="risk-tags">
      <span v-for="tag in entry.riskTags" :key="tag" class="risk-tag">{{ tag }}</span>
    </div>
  </div>
</template>

<style scoped>
.voucher-sheet {
  background: #fff;
  padding: 32px 36px;
  border-radius: var(--solo-radius-lg);
  box-shadow: var(--solo-shadow);
}
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
.meta-date { grid-column: 2; color: var(--solo-text-primary); }
.meta-ref { grid-column: 3; text-align: right; font-weight: 500; }
.voucher-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.voucher-table th,
.voucher-table td { border: 1px solid #9ca3af; }
.voucher-table th {
  padding: 10px 12px;
  text-align: center;
  font-weight: 600;
  color: var(--solo-text-primary);
  font-size: 13px;
}
.voucher-table td { padding: 10px 12px; text-align: left; vertical-align: top; }
.col-summary { width: 22%; }
.col-account { width: 42%; }
.col-amount { width: 18%; }
.amount { text-align: right; font-variant-numeric: tabular-nums; }
.qty-hint { font-size: 11px; color: var(--solo-text-muted, #636e7c); line-height: 1.4; }
.total-capital, .total { font-weight: 700; }
.aux-hint { margin-top: 2px; font-size: 11px; color: #4b5563; }
.line-name-row {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.vat-exempt-tag {
  font-size: 11px;
  color: var(--solo-income, #0f7354);
  font-weight: normal;
}
.blank-row { display: none; }
.voucher-sign {
  display: flex;
  justify-content: space-between;
  margin-top: 22px;
  font-size: 13px;
  color: var(--solo-text-primary);
}
.rd-section {
  margin-top: 16px;
  padding: 12px;
  border: 1px solid var(--solo-border-light);
  border-radius: 4px;
  font-size: 12px;
}
.rd-section-title { font-weight: 700; margin-bottom: 8px; }
.rd-line { display: grid; grid-template-columns: 1fr 1fr; gap: 6px 16px; }
.rd-line + .rd-line { border-top: 1px solid var(--solo-border-light); margin-top: 8px; padding-top: 8px; }
.risk-tags { display: flex; gap: 6px; margin-top: 16px; }
.risk-tag { font-size: 11px; color: var(--solo-expense); background: #fef2f2; padding: 2px 8px; border-radius: 4px; }

@media print {
  .voucher-sheet {
    box-shadow: none;
    padding: 0;
    border-radius: 0;
    break-inside: avoid;
  }
  .voucher-title { border-bottom-color: #000; }
  .voucher-table th,
  .voucher-table td { border-color: #000; }
  .vat-exempt-tag { display: none !important; }
  .blank-row { display: table-row; }
  .page-break { break-after: page; page-break-after: always; }
}
</style>
