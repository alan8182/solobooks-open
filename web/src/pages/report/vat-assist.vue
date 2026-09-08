<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NAlert, NButton, NSelect, NSpin, NTag, useMessage } from 'naive-ui'
import { getVatAssist } from '@/api/report'
import { formatMoney } from '@/utils/format'
import type { VatAssistResponse, VatFilingField } from '@/api/types'

const router = useRouter()
const message = useMessage()
const periodType = ref<'month' | 'quarter'>('quarter')
const loading = ref(false)
const result = ref<VatAssistResponse | null>(null)

function periodOptions(type: 'month' | 'quarter') {
  const options: { label: string; value: string }[] = []
  const now = new Date()
  if (type === 'month') {
    for (let i = 1; i <= 12; i++) {
      const d = new Date(now.getFullYear(), now.getMonth() - i, 1)
      const value = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
      options.push({ label: `${d.getFullYear()}年${d.getMonth() + 1}月`, value })
    }
    return options
  }
  const current = now.getFullYear() * 4 + Math.floor(now.getMonth() / 3)
  for (let i = 1; i <= 8; i++) {
    const absolute = current - i
    const year = Math.floor(absolute / 4)
    const quarter = ((absolute % 4) + 4) % 4 + 1
    options.push({ label: `${year}年第${quarter}季度`, value: `${year}-Q${quarter}` })
  }
  return options
}

const options = computed(() => periodOptions(periodType.value))
const period = ref(periodOptions('quarter')[0].value)
const typeOptions = [
  { label: '季度申报', value: 'quarter' },
  { label: '月度申报', value: 'month' },
]

const filingSections = computed(() => {
  const order = ['货物及劳务', '服务、不动产和无形资产', '税费试算', '附加税费']
  return order.map(section => ({
    section,
    fields: result.value?.filingFields.filter(field => field.section === section) ?? [],
  }))
})

function formatPlainMoney(value: string | number) {
  return Number(value).toFixed(2)
}

async function copyField(field: VatFilingField) {
  await navigator.clipboard.writeText(formatPlainMoney(field.amount))
  message.success(`已复制“${field.label}”`)
}

async function copyAllFields() {
  if (!result.value) return
  const text = result.value.filingFields
    .filter(field => field.editable)
    .map(field => `${field.label}：${formatPlainMoney(field.amount)}`)
    .join('\n')
  await navigator.clipboard.writeText(text)
  message.success('申报字段已全部复制')
}

async function load() {
  loading.value = true
  try {
    result.value = await getVatAssist(period.value, periodType.value)
  } catch {
    message.error('加载增值税申报辅助失败')
  } finally {
    loading.value = false
  }
}

function changePeriodType() {
  period.value = periodOptions(periodType.value)[0].value
  load()
}

onMounted(load)
</script>

<template>
  <div class="report-container vat-workbench">
    <div class="report-header">
      <div class="report-header-left">
        <NButton text class="report-back-btn" @click="router.push('/reports')">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回
        </NButton>
        <div class="report-title-section">
          <h2>增值税简易申报辅助</h2>
          <p class="report-subtitle">从已过账凭证生成税务局简易申报字段，提交前仍需人工核对发票信息</p>
        </div>
      </div>
      <div class="report-header-right period-controls">
        <NSelect
          v-model:value="periodType"
          :options="typeOptions"
          style="width: 124px;"
          @update:value="changePeriodType"
        />
        <NSelect
          v-model:value="period"
          :options="options"
          style="width: 150px;"
          @update:value="load"
        />
      </div>
    </div>

    <NSpin :show="loading">
      <template v-if="result">
        <div class="status-band" :class="result.readyToFile ? 'status-ready' : 'status-review'">
          <div>
            <div class="status-title">
              <i :class="result.readyToFile ? 'ri-checkbox-circle-line' : 'ri-error-warning-line'" />
              {{ result.readyToFile ? '字段已齐，可进入税务局复核' : `还有 ${result.blockingIssues.length} 项需要确认` }}
            </div>
            <div class="status-meta">
              税款所属期 {{ result.periodStart }} 至 {{ result.periodEnd }} · 小规模纳税人
            </div>
          </div>
          <NTag :type="result.isBelowThreshold ? 'success' : 'warning'" :bordered="false">
            {{ result.isBelowThreshold ? '未达起征点' : '达到起征点' }}
          </NTag>
        </div>

        <NAlert
          v-for="issue in result.blockingIssues"
          :key="issue"
          type="warning"
          :bordered="false"
          class="notice"
        >
          {{ issue }}
        </NAlert>

        <section class="summary-strip" aria-label="申报概览">
          <div>
            <span>销售额合计</span>
            <strong>{{ formatMoney(result.totalSales) }}</strong>
          </div>
          <div>
            <span>起征点</span>
            <strong>{{ formatMoney(result.threshold) }}</strong>
          </div>
          <div>
            <span>增值税免税额</span>
            <strong>{{ formatMoney(result.vatExemption) }}</strong>
          </div>
          <div>
            <span>本期应补（退）税费</span>
            <strong class="payable">{{ formatMoney(Number(result.totalVatPayable) + Number(result.surchargeTotal)) }}</strong>
          </div>
        </section>

        <section class="filing-section">
          <div class="section-heading">
            <div>
              <h3>税务局简易申报填写字段</h3>
              <p>带“手工填写”的金额录入税务局弹窗；免税和税额由税局系统自动计算。</p>
            </div>
            <NButton secondary @click="copyAllFields">
              <template #icon><i class="ri-file-copy-line" /></template>
              复制全部字段
            </NButton>
          </div>

          <div v-for="group in filingSections" :key="group.section" class="filing-group">
            <div class="filing-group-title">{{ group.section }}</div>
            <div class="field-list">
              <button
                v-for="field in group.fields"
                :key="field.code"
                class="filing-field"
                :class="{ 'system-field': !field.editable }"
                type="button"
                :disabled="!field.editable"
                @click="field.editable && copyField(field)"
              >
                <span>{{ field.label }}</span>
                <NTag size="small" :type="field.editable ? 'info' : 'default'" :bordered="false">
                  {{ field.editable ? '手工填写' : '税局自动计算' }}
                </NTag>
                <strong>{{ formatPlainMoney(field.amount) }}</strong>
                <i v-if="field.editable" class="ri-file-copy-line" aria-hidden="true" />
              </button>
            </div>
          </div>
        </section>

        <section class="basis-section">
          <div class="section-heading compact">
            <div>
              <h3>销售额来源核对</h3>
              <p>只归集已过账的6001/6051营业收入，资金结算、银行利息和验证款不进入销售额。</p>
            </div>
            <span class="source-count">{{ result.sourceEntryCount }} 笔</span>
          </div>
          <div v-if="result.items.length" class="source-table-wrap">
            <table class="source-table">
              <thead>
                <tr>
                  <th>日期</th>
                  <th>业务</th>
                  <th>申报分类</th>
                  <th>发票状态</th>
                  <th>销售额</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in result.items" :key="item.entryId">
                  <td>{{ item.occurDate }}</td>
                  <td>{{ item.title }}</td>
                  <td>{{ item.salesCategory === 'goods_labor' ? '货物及劳务' : '服务、不动产和无形资产' }}</td>
                  <td>
                    <NTag size="small" :type="item.invoiceNumber ? 'success' : 'warning'" :bordered="false">
                      {{ item.invoiceNumber ? item.invoiceType : '待补充' }}
                    </NTag>
                  </td>
                  <td class="amount">{{ formatMoney(item.salesAmount) }}</td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="empty-state">
            该申报期没有已过账的营业收入分录，简易申报字段均为 0.00。
          </div>
        </section>

        <div class="policy-note">
          计算口径：2026年至2027年小规模纳税人起征点及1%减征政策；规则版本 {{ result.policyVersion }}。
          本页面提供申报辅助，不代替税务机关最终审核。
        </div>
      </template>
    </NSpin>
  </div>
</template>

<style scoped>
.vat-workbench { padding-bottom: 40px; }
.period-controls { display: flex; gap: 8px; }
.status-band {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 18px 20px;
  border: 1px solid var(--solo-border);
  border-radius: 14px;
  background: var(--solo-surface);
  margin-bottom: 12px;
}
.status-ready { background: var(--solo-income-soft); }
.status-review { background: var(--solo-warning-soft); }
.status-title { display: flex; align-items: center; gap: 8px; font-size: 16px; font-weight: 650; }
.status-meta { margin-top: 5px; color: var(--solo-text-secondary); font-size: 13px; }
.notice { margin-bottom: 10px; }
.summary-strip {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  border: 1px solid var(--solo-border);
  border-radius: 14px;
  background: var(--solo-surface);
  margin: 16px 0 24px;
  overflow: hidden;
}
.summary-strip > div { padding: 18px 20px; border-right: 1px solid var(--solo-border-light); }
.summary-strip > div:last-child { border-right: 0; }
.summary-strip span { display: block; color: var(--solo-text-secondary); font-size: 12px; margin-bottom: 7px; }
.summary-strip strong { font-size: 20px; font-variant-numeric: tabular-nums; }
.summary-strip .payable { color: var(--solo-expense); }
.filing-section, .basis-section {
  border: 1px solid var(--solo-border);
  border-radius: 14px;
  background: var(--solo-surface);
  padding: 22px;
  margin-bottom: 20px;
}
.section-heading { display: flex; align-items: center; justify-content: space-between; gap: 20px; margin-bottom: 18px; }
.section-heading.compact { margin-bottom: 14px; }
.section-heading h3 { margin: 0 0 4px; font-size: 17px; }
.section-heading p { margin: 0; color: var(--solo-text-secondary); font-size: 13px; }
.filing-group { margin-top: 18px; }
.filing-group:first-of-type { margin-top: 0; }
.filing-group-title { margin-bottom: 8px; color: var(--solo-text-secondary); font-size: 13px; font-weight: 650; }
.field-list { border: 1px solid var(--solo-border); border-radius: 10px; overflow: hidden; }
.filing-field {
  width: 100%;
  min-height: 54px;
  display: grid;
  grid-template-columns: minmax(220px, 1fr) 110px 150px 24px;
  align-items: center;
  gap: 12px;
  padding: 0 16px;
  border: 0;
  border-bottom: 1px solid var(--solo-border-light);
  background: var(--solo-surface);
  color: var(--solo-text-primary);
  text-align: left;
  cursor: pointer;
}
.filing-field:last-child { border-bottom: 0; }
.filing-field:hover, .surcharge-field:hover { background: var(--solo-primary-soft); }
.filing-field.system-field { cursor: default; background: var(--solo-surface-soft); }
.filing-field.system-field:hover { background: var(--solo-surface-soft); }
.filing-field:focus-visible, .surcharge-field:focus-visible { outline: 2px solid rgba(22, 93, 255, .35); outline-offset: -2px; }
.filing-field strong { text-align: right; font-size: 16px; font-variant-numeric: tabular-nums; }
.filing-field i { color: var(--solo-primary); }
.surcharge-heading { margin: 20px 0 10px; color: var(--solo-text-secondary); font-size: 12px; font-weight: 600; }
.surcharge-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
.surcharge-field {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 14px;
  border: 1px solid var(--solo-border);
  border-radius: 10px;
  background: var(--solo-surface-soft);
  color: var(--solo-text-primary);
  cursor: pointer;
}
.surcharge-field strong { font-variant-numeric: tabular-nums; }
.source-count { color: var(--solo-text-secondary); font-variant-numeric: tabular-nums; }
.source-table-wrap { overflow-x: auto; }
.source-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.source-table th { padding: 10px 12px; background: var(--solo-surface-soft); color: var(--solo-text-secondary); text-align: left; font-weight: 600; }
.source-table td { padding: 11px 12px; border-bottom: 1px solid var(--solo-border-light); }
.source-table tbody tr:last-child td { border-bottom: 0; }
.source-table .amount { text-align: right; font-variant-numeric: tabular-nums; }
.empty-state { padding: 28px; text-align: center; color: var(--solo-text-muted); background: var(--solo-surface-soft); border-radius: 10px; }
.policy-note { color: var(--solo-text-muted); font-size: 12px; line-height: 1.6; }
@media (max-width: 900px) {
  .report-header { align-items: flex-start; gap: 16px; }
  .period-controls { width: 100%; }
  .period-controls :deep(.n-select) { flex: 1; width: auto !important; }
  .summary-strip { grid-template-columns: repeat(2, 1fr); }
  .summary-strip > div:nth-child(2) { border-right: 0; }
  .summary-strip > div:nth-child(-n+2) { border-bottom: 1px solid var(--solo-border-light); }
  .surcharge-grid { grid-template-columns: 1fr; }
}
@media (max-width: 600px) {
  .section-heading { align-items: flex-start; flex-direction: column; }
  .filing-field { grid-template-columns: 1fr 92px 20px; min-height: 64px; }
  .filing-field :deep(.n-tag) { display: none; }
}
</style>
