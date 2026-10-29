<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NCard, NTag, NSpin, useMessage, NButton, NInput, NSpace,
  NRadioGroup, NRadioButton, NTooltip,
} from 'naive-ui'
import { SoloEmpty, SoloMonthPicker } from '@/components/ui'
import { getArchiveChecklist } from '@/api/report'
import { formatMoney, formatDate, getCurrentMonth } from '@/utils/format'
import { getEvidenceTip } from '@/utils/archiveEvidence'
import type { ArchiveChecklistResponse, ArchiveItem } from '@/api/types'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const requestedMonth = String(route.query.month || '')
const hasRequestedMonth = /^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth)
const month = ref(hasRequestedMonth ? requestedMonth : getCurrentMonth())
const loading = ref(false)
const failed = ref(false)
const result = ref<ArchiveChecklistResponse | null>(null)

type Filter = 'all' | 'missing' | 'compliant' | 'questionable'
const filter = ref<Filter>('all')
const keyword = ref('')
const showGuide = ref(true)

// 后端 complianceStatus 为英文编码；旧后端只返回中文 compliance，这里兜底
function statusOf(item: ArchiveItem): Filter {
  if (item.complianceStatus === 'compliant' || item.complianceStatus === 'missing' || item.complianceStatus === 'questionable') {
    return item.complianceStatus
  }
  return item.compliance === '合规' ? 'compliant' : 'missing'
}

const STATUS_TAG = {
  compliant: { label: '合规', type: 'success' },
  missing: { label: '待补单据', type: 'error' },
  questionable: { label: '存疑', type: 'warning' },
} as const

const DOC_LABEL: Record<string, string> = {
  has_invoice: '有发票',
  has_receipt: '有收据',
  no_document: '无单据',
  unknown: '未确认',
}

const items = computed(() => result.value?.items ?? [])

const counts = computed(() => {
  const c = { all: items.value.length, missing: 0, compliant: 0, questionable: 0 }
  for (const i of items.value) c[statusOf(i)]++
  return c
})

const rate = computed(() =>
  counts.value.all ? Math.round(((counts.value.compliant + counts.value.questionable) / counts.value.all) * 100) : 0,
)

const rows = computed(() => {
  const kw = keyword.value.trim().toLowerCase()
  return items.value
    .filter((i) => filter.value === 'all' || statusOf(i) === filter.value)
    .filter((i) =>
      !kw || [i.summary, i.voucherNo, i.invoiceNumber, i.category].some((v) => String(v || '').toLowerCase().includes(kw)),
    )
    .sort((a, b) => Math.abs(Number(b.amount)) - Math.abs(Number(a.amount)))
})

async function load() {
  loading.value = true
  failed.value = false
  try {
    result.value = await getArchiveChecklist(month.value)
  } catch {
    failed.value = true
    result.value = null
    message.error('加载归档清单失败')
  } finally {
    loading.value = false
  }
}

watch(month, (m) => {
  router.replace({ query: { ...route.query, month: m } })
  load()
})

function handleBack() {
  if (route.query.from === 'reports') {
    router.push({ path: '/reports', query: { month: month.value } })
  } else if (window.history.length > 1) {
    router.back()
  } else {
    router.push({ path: '/vouchers', query: { month: month.value } })
  }
}

function openVoucher(item: ArchiveItem) {
  if (item.id) router.push(`/vouchers/${item.id}`)
}

// ponytail: 前端直出 CSV（带 BOM 供 Excel 识别中文）；超大账套再换后端导出
function exportCsv() {
  const esc = (v: unknown) => `"${String(v ?? '').replace(/"/g, '""')}"`
  const head = ['凭证号', '日期', '摘要', '分类', '金额', '单据形式', '发票号', '合规状态', '风险', '归档要件建议']
  const body = rows.value.map((i) => {
    const tip = getEvidenceTip(i)
    return [
      i.voucherNo, i.date, i.summary, i.category, i.amount, DOC_LABEL[i.docType] || i.docType,
      i.invoiceNumber, STATUS_TAG[statusOf(i) as keyof typeof STATUS_TAG].label, i.riskTag, tip?.tip || '',
    ].map(esc).join(',')
  })
  const blob = new Blob(['\uFEFF' + [head.map(esc).join(','), ...body].join('\n')], { type: 'text/csv;charset=utf-8' })
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = `单据归档清单_${month.value}.csv`
  a.click()
  URL.revokeObjectURL(a.href)
}

const printList = () => window.print()

onMounted(load)
</script>

<template>
  <div class="report-container archive-page">
    <div class="report-header">
      <div class="report-header-left">
        <NButton text class="report-back-btn no-print" @click="handleBack">
          <template #icon><i class="ri-arrow-left-line" /></template>
          {{ route.query.from === 'reports' ? '返回报表中心' : '返回' }}
        </NButton>
        <div class="report-title-section">
          <h2>单据归档清单</h2>
          <p class="report-subtitle">检查凭证单据的完备度与合规度，月末结账前先补齐缺票，再装订归档</p>
        </div>
      </div>
      <div class="report-header-right no-print">
        <SoloMonthPicker v-model:value="month" :clearable="false" style="width: 150px;" />
        <NButton size="small" :disabled="!rows.length" @click="exportCsv">导出 CSV</NButton>
        <NButton size="small" type="primary" :disabled="!rows.length" @click="printList">打印装订清单</NButton>
      </div>
    </div>

    <NSpin :show="loading">
      <template v-if="result">
        <!-- 统计指标看板 -->
        <NCard :bordered="false" class="report-card">
          <div class="report-summary-row">
            <div class="report-summary-item">
              <span class="report-summary-label">归档合规率</span>
              <span class="report-summary-val" :class="rate === 100 ? 'income' : 'expense'">{{ rate }}%</span>
            </div>
            <div class="report-summary-item">
              <span class="report-summary-label">合规单据</span>
              <span class="report-summary-val income">{{ counts.compliant + counts.questionable }} 笔</span>
            </div>
            <div class="report-summary-item">
              <span class="report-summary-label">待补单据</span>
              <span class="report-summary-val expense">{{ counts.missing }} 笔</span>
            </div>
            <div v-if="result.missingAmount !== undefined" class="report-summary-item">
              <span class="report-summary-label">待补金额敞口（税前扣除风险）</span>
              <span class="report-summary-val expense">{{ formatMoney(result.missingAmount) }}</span>
            </div>
            <div class="report-summary-item">
              <span class="report-summary-label">本期凭证总数</span>
              <span class="report-summary-val">{{ counts.all }} 笔</span>
            </div>
          </div>
        </NCard>

        <!-- 归档证据链与老板避坑指南卡片 -->
        <div class="guide-banner no-print">
          <div class="guide-header" @click="showGuide = !showGuide">
            <div class="guide-title">
              <i class="ri-shield-check-line guide-icon" />
              <span>老板必读：常见业务单据归档与证据链避坑指引</span>
            </div>
            <NButton text size="tiny" class="guide-toggle">
              {{ showGuide ? '收起指南' : '展开指南' }}
              <i :class="showGuide ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'" />
            </NButton>
          </div>
          <div v-show="showGuide" class="guide-content">
            <div class="guide-grid">
              <div class="guide-item">
                <div class="guide-item-title">
                  <span class="badge boss">老板垫资</span>
                  <strong>老板自己垫付开销</strong>
                </div>
                <p class="guide-item-rule"><strong>归档要件：</strong>公司抬头增值税发票 + 老板个人支付记录 + 简要业务说明。</p>
                <p class="guide-item-tip">💡 <strong>避坑：</strong>不需要自己审批自己，但必须写明用于公司何项业务；公司还款时附还款流水，防范被税局认定为个人分红补交 20% 个税。</p>
              </div>

              <div class="guide-item">
                <div class="guide-item-title">
                  <span class="badge staff">费用报销</span>
                  <strong>员工差旅/办公/餐饮报销</strong>
                </div>
                <p class="guide-item-rule"><strong>归档要件：</strong>合规发票 + 报销审批单/费用明细 + 支付给员工的水单。</p>
                <p class="guide-item-tip">💡 <strong>避坑：</strong>必须留存公司转给员工的支付记录，证明确实发生并已由公司出资，防范拿他人发票虚假报销。</p>
              </div>

              <div class="guide-item">
                <div class="guide-item-title">
                  <span class="badge purchase">采购外包</span>
                  <strong>技术服务/外包/咨询费</strong>
                </div>
                <p class="guide-item-rule"><strong>归档要件：</strong>发票 + 银行对公转账水单 + 服务合同 + 交付验收成果。</p>
                <p class="guide-item-tip">💡 <strong>避坑：</strong>无实物交付的无形技术/咨询服务是税务严查重点，务必留存交付报告、验收记录等真实凭据防范虚开。</p>
              </div>

              <div class="guide-item">
                <div class="guide-item-title">
                  <span class="badge small">小额支出</span>
                  <strong>500 元以下零星小额开支</strong>
                </div>
                <p class="guide-item-rule"><strong>归档要件：</strong>个人收款凭证（写明经办人姓名、身份证号、明细） + 付款截图。</p>
                <p class="guide-item-tip">💡 <strong>避坑：</strong>500 元以下合法收款凭据税法允许税前扣除；超出 500 元的无票白条年底汇算必须纳税调增。</p>
              </div>
            </div>
          </div>
        </div>

        <!-- 单据列表卡片与打印核验表 -->
        <template v-if="items.length">
          <NCard :bordered="false" class="report-card">
            <div class="toolbar no-print">
              <NRadioGroup v-model:value="filter" size="small">
                <NRadioButton value="all">全部 ({{ counts.all }})</NRadioButton>
                <NRadioButton value="missing">待补 ({{ counts.missing }})</NRadioButton>
                <NRadioButton value="compliant">合规 ({{ counts.compliant }})</NRadioButton>
                <NRadioButton value="questionable">存疑 ({{ counts.questionable }})</NRadioButton>
              </NRadioGroup>
              <NInput v-model:value="keyword" size="small" clearable placeholder="搜索凭证号 / 摘要 / 发票号" style="width: 260px;" />
            </div>
            <div class="report-table-wrapper">
              <table class="report-table">
                <thead>
                  <tr>
                    <th style="width: 100px;">凭证号</th>
                    <th style="width: 105px;">发生日期</th>
                    <th>摘要说明</th>
                    <th style="width: 120px;">账目分类</th>
                    <th style="text-align: right; width: 120px;">金额</th>
                    <th style="width: 140px;">单据形式</th>
                    <th style="width: 130px;">合规状态</th>
                    <th style="width: 170px;">归档指引</th>
                    <th class="no-print" style="width: 75px;">操作</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(item, i) in rows" :key="item.id || i">
                    <td>{{ item.voucherNo || '—' }}</td>
                    <td>{{ formatDate(item.date) }}</td>
                    <td>{{ item.summary }}</td>
                    <td>{{ item.category || '—' }}</td>
                    <td class="amount-cell">{{ formatMoney(item.amount) }}</td>
                    <td>
                      {{ DOC_LABEL[item.docType] || item.docType }}
                      <div v-if="item.invoiceNumber" class="sub">{{ item.invoiceNumber }}</div>
                    </td>
                    <td>
                      <NSpace align="center" size="small">
                        <NTag size="small" :type="STATUS_TAG[statusOf(item) as keyof typeof STATUS_TAG].type" :bordered="false" round>
                          {{ STATUS_TAG[statusOf(item) as keyof typeof STATUS_TAG].label }}
                        </NTag>
                        <NTag v-if="item.riskTag" size="small" type="error" :bordered="false" round>{{ item.riskTag }}</NTag>
                      </NSpace>
                    </td>
                    <td>
                      <template v-if="getEvidenceTip(item)">
                        <NTooltip trigger="hover">
                          <template #trigger>
                            <span class="evidence-pill" :class="getEvidenceTip(item)?.level">
                              <i class="ri-lightbulb-line" />
                              {{ getEvidenceTip(item)?.tag }}
                            </span>
                          </template>
                          <div class="tip-popover-content">{{ getEvidenceTip(item)?.tip }}</div>
                        </NTooltip>
                      </template>
                      <span v-else class="sub">—</span>
                    </td>
                    <td class="no-print">
                      <NButton v-if="item.id" text type="primary" size="small" @click="openVoucher(item)">查凭证</NButton>
                    </td>
                  </tr>
                  <tr v-if="!rows.length">
                    <td colspan="9" class="sub" style="text-align: center; padding: 24px;">没有符合条件的单据</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </NCard>

          <!-- 打印装订专用核对栏（仅纸质打印时输出） -->
          <div class="print-binding-checklist only-print">
            <div class="print-checklist-title">记账凭证装订自查与签核表</div>
            <div class="print-checklist-grid">
              <div class="print-checklist-item">□ 发票抬头与统一社会信用代码已核验无误，发票真伪查验完毕</div>
              <div class="print-checklist-item">□ 费用报销与个人垫付已附转账流水及业务事由说明单</div>
              <div class="print-checklist-item">□ 采购、技术服务及外包支出已归档对公水单、合同与交付成果</div>
              <div class="print-checklist-item">□ 工资发放记录与个人所得税代扣代缴申报数据核对一致</div>
            </div>
            <div class="print-signature-row">
              <span>装订经手人：____________</span>
              <span>财务复核人：____________</span>
              <span>档案归档日期：____年__月__日</span>
            </div>
          </div>
        </template>

        <div v-else class="empty-section">
          <SoloEmpty
            title="该月份没有记账数据"
            description="没有账就无需归档自查。换一个有凭证的月份，或先去记账。"
            icon="ri-archive-line"
          />
        </div>
      </template>
      <div v-else-if="failed" class="empty-section">
        <SoloEmpty title="归档清单加载失败" description="请检查网络后重试。" icon="ri-error-warning-line">
          <template #action><NButton size="small" @click="load">重试</NButton></template>
        </SoloEmpty>
      </div>
    </NSpin>
  </div>
</template>

<style scoped>
.empty-section {
  margin-top: 16px;
  padding: 48px;
  background: var(--solo-bg-card);
  border: 1px dashed var(--solo-border);
  border-radius: var(--solo-radius-lg);
  display: flex;
  justify-content: center;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}
.sub { font-size: 12px; color: var(--solo-text-muted); }

/* 指南卡片样式 */
.guide-banner {
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  margin-bottom: 16px;
  overflow: hidden;
}
.guide-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 16px;
  cursor: pointer;
  background: var(--solo-bg-elevated);
  transition: background 0.15s ease;
}
.guide-header:hover {
  background: var(--solo-bg-hover, var(--solo-bg-elevated));
}
.guide-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary);
}
.guide-icon {
  font-size: 16px;
  color: var(--solo-primary);
}
.guide-toggle {
  font-size: 12px;
  color: var(--solo-text-secondary);
}
.guide-content {
  padding: 14px 16px 16px 16px;
  border-top: 1px solid var(--solo-border);
}
.guide-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 16px;
}
.guide-item {
  background: var(--solo-bg);
  border: 1px solid var(--solo-border-subtle, var(--solo-border));
  border-radius: var(--solo-radius-md);
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.guide-item-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
.badge {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  font-weight: 500;
}
.badge.boss { background: #e0f2fe; color: #0284c7; }
.badge.staff { background: #f0fdf4; color: #16a34a; }
.badge.purchase { background: #fef3c7; color: #d97706; }
.badge.small { background: #f3f4f6; color: #4b5563; }

.guide-item-rule {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--solo-text-primary);
}
.guide-item-tip {
  margin: 0;
  font-size: 11px;
  line-height: 1.5;
  color: var(--solo-text-muted);
}

/* 行内智能提示 Pill */
.evidence-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 12px;
  cursor: help;
  user-select: none;
  transition: opacity 0.15s ease;
}
.evidence-pill:hover {
  opacity: 0.85;
}
.evidence-pill.warning {
  background: #fef3c7;
  color: #b45309;
}
.evidence-pill.info {
  background: #eff6ff;
  color: #1d4ed8;
}
.tip-popover-content {
  max-width: 260px;
  font-size: 12px;
  line-height: 1.5;
}

/* 打印装订专用区 */
.only-print {
  display: none;
}

@media print {
  .no-print { display: none !important; }
  .only-print { display: block !important; }

  .print-binding-checklist {
    margin-top: 24px;
    padding-top: 16px;
    border-top: 1px solid #333;
    page-break-inside: avoid;
  }
  .print-checklist-title {
    font-size: 14px;
    font-weight: bold;
    margin-bottom: 8px;
  }
  .print-checklist-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 6px;
    font-size: 12px;
    margin-bottom: 16px;
  }
  .print-signature-row {
    display: flex;
    justify-content: space-between;
    font-size: 12px;
    margin-top: 12px;
  }
}
</style>
