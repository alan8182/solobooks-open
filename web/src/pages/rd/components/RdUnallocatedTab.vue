<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { useRouter } from 'vue-router'
import {
  NAlert, NButton, NCard, NSpin, NTag,
} from 'naive-ui'
import type { RdUnallocatedItem } from '@/api/rd'
import { formatMoney } from '@/utils/format'
import { SoloEmpty } from '@/components/ui'

const props = defineProps<{
  items: RdUnallocatedItem[]
  loading: boolean
}>()

const emit = defineEmits<{
  (e: 'correct', item: RdUnallocatedItem): void
}>()

const router = useRouter()

function navigateToVoucher(entryId: string) {
  if (entryId) {
    router.push(`/vouchers/${entryId}`)
  }
}
</script>

<template>
  <div class="rd-unallocated-tab">
    <NAlert type="warning" :bordered="false" class="mb-4">
      <div class="alert-content">
        <i class="ri-alert-line alert-icon" />
        <div class="alert-text">
          <strong>待补充研发归集队列（共 {{ items.length }} 笔）</strong>
          <p>
            以下科目分录缺少关联的研发项目或费用类别。遵循权威会计准则，系统不会自动猜测或改写已过账凭证；点击「补充归集」可指定具体研发项目、费用分类与分摊依据。已过账凭证将自动生成红字冲销与合规新凭证，全流程可信留痕。
          </p>
        </div>
      </div>
    </NAlert>

    <NSpin :show="loading">
      <NCard :bordered="false" class="table-card">
        <div v-if="items.length" class="table-wrap">
          <table class="solo-table">
            <thead>
              <tr>
                <th style="width: 110px;">日期</th>
                <th style="width: 140px;">凭证号</th>
                <th>摘要</th>
                <th style="width: 180px;">原科目编码与名称</th>
                <th style="width: 100px;">凭证状态</th>
                <th style="width: 140px;" class="amount">待归集金额</th>
                <th style="width: 220px;">诊断原因</th>
                <th style="width: 120px;" class="action-th">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in items" :key="`${item.entryId}-${item.accountCode}`">
                <td class="font-mono">{{ item.date }}</td>
                <td>
                  <NButton
                    v-if="item.voucherNo"
                    text
                    type="primary"
                    size="small"
                    class="voucher-link"
                    @click="navigateToVoucher(item.entryId)"
                  >
                    <i class="ri-file-text-line" style="margin-right: 4px;" />
                    {{ item.voucherNo }}
                  </NButton>
                  <span v-else class="text-muted">-</span>
                </td>
                <td class="cell-summary" :title="item.summary">{{ item.summary }}</td>
                <td>
                  <span class="font-mono text-code">{{ item.accountCode }}</span>
                  <span class="account-name">{{ item.accountName }}</span>
                </td>
                <td>
                  <NTag
                    size="small"
                    :type="item.status === 'posted' ? 'warning' : 'info'"
                    :bordered="false"
                  >
                    {{ item.status === 'posted' ? '已过账' : '草稿' }}
                  </NTag>
                </td>
                <td class="amount font-mono amount-cell">
                  {{ formatMoney(item.amount) }}
                </td>
                <td class="reason-cell" :title="item.reason">
                  <span class="reason-text">{{ item.reason }}</span>
                </td>
                <td class="action-td">
                  <NButton
                    size="small"
                    type="primary"
                    secondary
                    @click="emit('correct', item)"
                  >
                    补充归集
                  </NButton>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
        <SoloEmpty
          v-else
          variant="hint"
          icon="ri-checkbox-circle-line"
          title="所有研发费用均已规范归集"
          description="当前账簿中没有缺少研发项目或费用类别的分录，研发费用核算链条完整。"
        />
      </NCard>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-4 {
  margin-bottom: 16px;
}
.alert-content {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}
.alert-icon {
  font-size: 18px;
  color: var(--solo-warning);
  margin-top: 2px;
}
.alert-text p {
  margin: 4px 0 0 0;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--solo-text-secondary);
}
.table-card {
  border-radius: var(--solo-radius-lg, 12px);
  background: var(--solo-bg-card);
}
.table-wrap {
  overflow-x: auto;
}
.solo-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.solo-table th,
.solo-table td {
  padding: 12px 14px;
  border-bottom: 1px solid var(--solo-border-light);
  text-align: left;
  white-space: nowrap;
}
.solo-table th {
  color: var(--solo-text-secondary);
  font-weight: 600;
  background: var(--solo-bg-soft);
}
.amount {
  text-align: right;
}
.amount-cell {
  font-weight: 600;
  color: var(--solo-expense);
}
.action-th,
.action-td {
  text-align: right;
}
.font-mono {
  font-family: 'Outfit', 'Inter', monospace;
  font-variant-numeric: tabular-nums;
}
.voucher-link {
  font-family: 'Outfit', 'Inter', monospace;
  font-size: 13px;
  font-weight: 500;
}
.text-code {
  color: var(--solo-primary);
  margin-right: 6px;
}
.account-name {
  color: var(--solo-text-primary);
}
.cell-summary {
  max-width: 200px;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--solo-text-primary);
}
.reason-cell {
  max-width: 220px;
  overflow: hidden;
  text-overflow: ellipsis;
}
.reason-text {
  font-size: 12px;
  color: var(--solo-text-secondary);
}
.text-muted {
  color: var(--solo-text-muted);
}
</style>
