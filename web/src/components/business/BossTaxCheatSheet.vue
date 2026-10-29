<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { NButton, useMessage } from 'naive-ui'
import {
  calculateExemptionGauge,
  formatCopyAmount,
} from '@/utils/taxFiling'
import { copyToClipboard } from '@/utils/clipboard'
import { formatMoney } from '@/utils/format'

const props = defineProps<{
  pkg: any
  period: string
  loading?: boolean
  exporting?: boolean
}>()

const emit = defineEmits<{
  (e: 'switchMode', mode: 'accounting'): void
  (e: 'refresh'): void
  (e: 'markFiled', deadlineId: string | null): void
  (e: 'exportXls'): void
}>()

const message = useMessage()

// 销售额与免税额度监控
const totalSales = computed(() => Number(props.pkg?.vat?.totalSales || 0))
const periodType = computed(() => props.pkg?.periodType || 'quarter')
const gauge = computed(() => calculateExemptionGauge(totalSales.value, props.period, periodType.value))

// 预计税额
const vatPayable = computed(() => Number(props.pkg?.summary?.vatPayable ?? props.pkg?.vat?.totalVatPayable ?? 0))
const citPayable = computed(() => Number(props.pkg?.summary?.citPayable ?? props.pkg?.cit?.preferentialTax ?? 0))
const citRevenue = computed(() => Number(props.pkg?.cit?.revenue || 0))
const citCost = computed(() => {
  if (props.pkg?.cit?.cost != null) return Number(props.pkg.cit.cost)
  const costField = (props.pkg?.cit?.fields || []).find((f: any) => f.line === '2')
  return Number(costField?.amount || 0)
})
const citProfit = computed(() => Number(props.pkg?.cit?.totalProfit || 0))
// A200000 第 28 栏减免所得税额 = 法定 25% 税额 − 小微 5% 实际税额
const citRelief = computed(() => {
  const cit = props.pkg?.cit
  if (!cit) return 0
  if (cit.preferentialRelief != null) return Number(cit.preferentialRelief)
  return Math.max(0, Number(cit.standardTax || 0) - Number(cit.preferentialTax || 0))
})

const vatDeadline = computed(() => props.pkg?.vat?.deadline || null)
const citDeadline = computed(() => props.pkg?.cit?.deadline || null)
const isVatDone = computed(() => vatDeadline.value?.status === 'filed' || vatDeadline.value?.status === 'waived')
const isCitDone = computed(() => citDeadline.value?.status === 'filed' || citDeadline.value?.status === 'waived')

// 申报任务清单（含主表与附表）
const tasks = computed(() => props.pkg?.checklist || [])
const finStatementTask = computed(() => tasks.value.find((t: any) => t.taxKind === 'fin_statements') || null)
const otherTasks = computed(() => tasks.value.filter((t: any) => t.taxKind !== 'fin_statements'))

const allDeadlines = computed(() => {
  const list: any[] = []
  if (vatDeadline.value) list.push(vatDeadline.value)
  if (citDeadline.value) list.push(citDeadline.value)
  list.push(...tasks.value)
  return list.filter(d => d && d.status && d.status !== 'untracked')
})

// 本期口径：仅统计近期截止（≤90 天）或已逾期的待办事项。残保金等按年申报、
// 次年才到期的远期事项单独在清单里展示，不混入「本期申报截止/待办数」。
const nearTermPending = computed(() =>
  allDeadlines.value.filter((t: any) => {
    if (t.status === 'filed' || t.status === 'waived') return false
    const days = t.daysLeft
    if (days == null) return true
    return days <= 90
  })
)

const pendingCount = computed(() => nearTermPending.value.length)

async function copyAmount(val: number, label: string) {
  const text = formatCopyAmount(val)
  try {
    await copyToClipboard(text)
    message.success(`已复制 ${label} 金额：${text}`)
  } catch {
    message.info(`金额：${text}（复制失败，请手动输入）`)
  }
}
</script>

<template>
  <div class="boss-tax-sheet-container">
    <!-- 电子税务局极简小抄 -->
    <section class="cheat-sheet-card">
      <div class="sheet-steps">
        <!-- 步骤 1：财务报表报送（申报顺序：先报送财报，再增值税、企业所得税） -->
        <div v-if="finStatementTask" class="step-card" :class="{ 'step-done': finStatementTask.status === 'filed' || finStatementTask.status === 'waived' }">
          <div class="step-badge">{{ (finStatementTask.status === 'filed' || finStatementTask.status === 'waived') ? '✓' : '1' }}</div>
          <div class="step-content">
            <div class="step-head">
              <h3>{{ finStatementTask.name || '财务报表报送' }}（资产负债表 + 利润表）</h3>
              <span class="step-tag" :class="{ 'tag-done': finStatementTask.status === 'filed' || finStatementTask.status === 'waived' }">
                {{ (finStatementTask.status === 'filed' || finStatementTask.status === 'waived') ? '已报送办结' : '必报附表' }}
              </span>
            </div>
            <p class="step-instruction">
              {{ finStatementTask.guide || '进入电子税局【财务报表报送及更正】➔ 导出下方标准三表 XLS 后直接上传或照抄填报。' }}
            </p>
            <div class="task-action-buttons">
              <NButton
                size="small"
                secondary
                :loading="exporting"
                @click="emit('exportXls')"
              >
                <template #icon><i class="ri-download-2-line" /></template>
                一键导出标准财务三表 (XLS)
              </NButton>
              <NButton
                v-if="finStatementTask.status !== 'filed' && finStatementTask.status !== 'waived'"
                type="primary"
                secondary
                size="small"
                @click="emit('markFiled', finStatementTask.deadlineId)"
              >
                <template #icon><i class="ri-check-line" /></template>
                标记「{{ finStatementTask.name }}」已申报办结
              </NButton>
            </div>
          </div>
        </div>

        <!-- 步骤 2：增值税申报 -->
        <div class="step-card" :class="{ 'step-done': isVatDone }">
          <div class="step-badge">{{ isVatDone ? '✓' : '2' }}</div>
          <div class="step-content">
            <div class="step-head">
              <h3>增值税及附加税费申报（小规模纳税人）</h3>
              <span class="step-tag" :class="{ 'tag-done': isVatDone }">
                {{ isVatDone ? '已申报办结' : (gauge.isExempt ? '免税申报 · 应缴 ¥0.00' : `正常纳税 · 应缴 ${formatMoney(vatPayable)}`) }}
              </span>
              <div class="step-head-actions">
                <NButton
                  v-if="vatDeadline?.deadlineId && !isVatDone"
                  type="primary"
                  secondary
                  size="tiny"
                  @click="emit('markFiled', vatDeadline.deadlineId)"
                >
                  <template #icon><i class="ri-check-line" /></template>
                  标记「增值税及附加税费」已申报办结
                </NButton>
              </div>
            </div>
            <p class="step-instruction">
              进入电子税局【我要办税】➔【税费申报及缴纳】➔【增值税及附加税费申报】
            </p>

            <div class="fields-copy-box">
              <div class="field-row">
                <span class="field-name">
                  {{ gauge.isExempt
                    ? '弹窗「1% 征收率」行填入销售额（税局自动归入第 10 栏免税销售额，第 1 栏【本期应税销售额】为 0.00）：'
                    : '第 1 栏【本期应税销售额】（1% 征收率不含税销售额）：' }}
                </span>
                <span class="field-val">{{ formatMoney(totalSales) }}</span>
                <NButton size="tiny" secondary @click="copyAmount(totalSales, '销售额')">
                  一键复制
                </NButton>
              </div>

              <div v-if="gauge.isExempt" class="field-row tip-row">
                <span class="field-name">主表核对要点：</span>
                <span class="field-desc">
                  在「本期销售情况」弹窗填入 {{ formatMoney(totalSales) }} 后，主表<strong>第 10 栏【小微企业免税销售额】</strong>自动带出 {{ formatMoney(totalSales) }}，第 1 栏为 0.00，<strong>第 22 栏【本期应补(退)税额】为 0.00</strong>，核对无误直接提交即可。
                </span>
              </div>
            </div>
          </div>
        </div>

        <!-- 步骤 3：企业所得税预缴 -->
        <div class="step-card" :class="{ 'step-done': isCitDone }">
          <div class="step-badge">{{ isCitDone ? '✓' : '3' }}</div>
          <div class="step-content">
            <div class="step-head">
              <h3>企业所得税季度预缴（A200000 查账征收）</h3>
              <span class="step-tag" :class="{ 'tag-done': isCitDone }">
                {{ isCitDone ? '已申报办结' : (citPayable === 0 ? '应纳 0 元' : `预缴 ${formatMoney(citPayable)}`) }}
              </span>
              <div class="step-head-actions">
                <NButton
                  v-if="citDeadline?.deadlineId && !isCitDone"
                  type="primary"
                  secondary
                  size="tiny"
                  @click="emit('markFiled', citDeadline.deadlineId)"
                >
                  <template #icon><i class="ri-check-line" /></template>
                  标记「企业所得税预缴」已申报办结
                </NButton>
              </div>
            </div>
            <p class="step-instruction">
              电子税局要求填写<strong>本年累计</strong>金额。小微企业年应纳税所得额 300 万以内实际税负 5%（亏损或 0 利润时应纳税额为 0.00）。
            </p>

            <div class="fields-copy-box">
              <div class="field-row">
                <span class="field-name">第 1 栏【营业收入】（本年累计）：</span>
                <span class="field-val">{{ formatMoney(citRevenue) }}</span>
                <NButton size="tiny" secondary @click="copyAmount(citRevenue, '营业收入')">
                  一键复制
                </NButton>
              </div>
              <div class="field-row">
                <span class="field-name">第 2 栏【营业成本】（本年累计）：</span>
                <span class="field-val">{{ formatMoney(citCost) }}</span>
                <NButton size="tiny" secondary @click="copyAmount(citCost, '营业成本')">
                  一键复制
                </NButton>
              </div>
              <div class="field-row">
                <span class="field-name">第 3 栏【利润总额】/ 第 25 栏【实际利润额】（本年累计）：</span>
                <span class="field-val">{{ formatMoney(citProfit) }}</span>
                <NButton size="tiny" secondary @click="copyAmount(citProfit, '实际利润额')">
                  一键复制
                </NButton>
              </div>
              <div class="field-row">
                <span class="field-name">第 28 栏【减免所得税额】（系统自动计算，核对即可）：</span>
                <span class="field-val">{{ formatMoney(citRelief) }}</span>
                <NButton size="tiny" secondary @click="copyAmount(citRelief, '减免所得税额')">
                  一键复制
                </NButton>
              </div>
            </div>
          </div>
        </div>

        <!-- 步骤 4：其他必报事项确认（印花税 / 个税扣缴 / 工商年报） -->
        <div class="step-card">
          <div class="step-badge">4</div>
          <div class="step-content">
            <div class="step-head">
              <h3>其他税种及必报事项确认</h3>
            </div>
            <p class="step-instruction">
              除增值税、企业所得税和财报外，请按下方指引完成个税扣缴、印花税等常规申报并点击归档：
            </p>
            <div v-if="otherTasks.length > 0" class="other-tasks-list">
              <div
                v-for="task in otherTasks"
                :key="task.deadlineId || task.taxKind"
                class="other-task-row"
              >
                <div class="other-task-info">
                  <div class="other-task-title">
                    <strong>{{ task.name }}</strong>
                    <span v-if="task.frequency" class="other-task-freq">{{ task.frequency }}</span>
                  </div>
                  <p class="other-task-guide">{{ task.guide || task.hint }}</p>
                </div>
                <div class="other-task-action">
                  <span
                    v-if="task.status === 'filed' || task.status === 'waived'"
                    class="all-done-note"
                  >
                    <i class="ri-checkbox-circle-fill" /> 已办结
                  </span>
                  <NButton
                    v-else
                    type="primary"
                    secondary
                    size="small"
                    @click="emit('markFiled', task.deadlineId)"
                  >
                    <template #icon><i class="ri-check-line" /></template>
                    标记「{{ task.name }}」已申报办结
                  </NButton>
                </div>
              </div>
            </div>
            <div v-if="pendingCount === 0" class="task-action-buttons">
              <span class="all-done-note">
                <i class="ri-checkbox-circle-fill" /> 本期全部税种均已办结归档
              </span>
            </div>
          </div>
        </div>
      </div>

      <div class="cheat-sheet-footer">
        <NButton text size="small" @click="emit('switchMode', 'accounting')">
          查看完整专业税表底稿 →
        </NButton>
      </div>
    </section>
  </div>
</template>

<style scoped>
.boss-tax-sheet-container {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 小抄卡片 */
.cheat-sheet-card {
  background: var(--solo-bg-card, #ffffff);
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: var(--solo-radius-xl, 12px);
  padding: 24px;
}

.cheat-sheet-footer {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--solo-border-light, #edf0f4);
}

.header-titles h2 {
  margin: 0 0 2px;
  font-size: 18px;
  font-weight: 700;
  color: var(--solo-text-primary, #182230);
}

.header-titles p {
  margin: 0;
  font-size: 13px;
  color: var(--solo-text-secondary, #586474);
}

.sheet-steps {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.step-card {
  display: flex;
  gap: 16px;
  padding: 18px;
  background: var(--solo-bg-soft, #f1f4f8);
  border: 1px solid var(--solo-border-light, #edf0f4);
  border-radius: 12px;
}

.step-card.step-done {
  background: #f8fbfa;
  border-color: #d6ede3;
}

.step-badge {
  width: 28px;
  height: 28px;
  border-radius: 50%;
  background: var(--solo-primary, #1747b0);
  color: #fff;
  font-size: 13px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
}

.step-card.step-done .step-badge {
  background: var(--solo-income, #0f7354);
}

.step-content {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.step-head {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 10px;
}

.step-head h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--solo-text-primary, #182230);
}

.step-head-actions {
  margin-left: auto;
}

.step-tag {
  font-size: 11px;
  padding: 2px 8px;
  border-radius: 4px;
  background: var(--solo-income-soft, #eaf7f1);
  color: var(--solo-income, #0f7354);
  font-weight: 600;
}

.step-tag.tag-done {
  background: #d1fae5;
  color: #065f46;
}

.step-instruction {
  margin: 0;
  font-size: 13px;
  color: var(--solo-text-secondary, #586474);
}

.fields-copy-box {
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: #ffffff;
  padding: 12px 14px;
  border-radius: 8px;
  border: 1px solid var(--solo-border-light, #edf0f4);
}

.field-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  font-size: 13px;
}

.field-row.tip-row {
  align-items: flex-start;
  padding-top: 6px;
  border-top: 1px dashed var(--solo-border-light, #edf0f4);
}

.field-name {
  color: var(--solo-text-primary, #182230);
  font-weight: 550;
}

.field-val {
  font-family: var(--font-mono, monospace);
  font-size: 14px;
  font-weight: 700;
  color: var(--solo-primary, #1747b0);
  margin-left: auto;
}

.field-desc {
  flex: 1;
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
  line-height: 1.45;
}

.other-tasks-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 4px;
}

.other-task-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  background: #ffffff;
  border: 1px solid var(--solo-border-light, #edf0f4);
  border-radius: 8px;
  padding: 10px 14px;
}

.other-task-info {
  flex: 1;
  min-width: 0;
}

.other-task-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13.5px;
  color: var(--solo-text-primary, #182230);
  margin-bottom: 2px;
}

.other-task-freq {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #f1f5f9;
  color: #475569;
}

.other-task-guide {
  margin: 0;
  font-size: 12px;
  color: var(--solo-text-secondary, #586474);
  line-height: 1.4;
}

.other-task-action {
  flex-shrink: 0;
}

.task-action-buttons {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 4px;
}

.all-done-note {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  color: var(--solo-income, #0f7354);
  font-size: 13px;
  font-weight: 600;
}
</style>
