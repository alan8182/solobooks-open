<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { NButton, NProgress, useMessage } from 'naive-ui'
import {
  calculateExemptionGauge,
  formatCopyAmount,
  generateTaxCopilotInsight,
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
const insight = computed(() => generateTaxCopilotInsight(props.pkg || {}))
const blockingIssues = computed<string[]>(() => props.pkg?.summary?.blockingIssues || [])
const hasBlockers = computed(() => blockingIssues.value.length > 0)

// 预计税额
const vatPayable = computed(() => Number(props.pkg?.summary?.vatPayable ?? props.pkg?.vat?.totalVatPayable ?? 0))
const citPayable = computed(() => Number(props.pkg?.summary?.citPayable ?? props.pkg?.cit?.preferentialTax ?? 0))
const totalTax = computed(() => vatPayable.value + citPayable.value)
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

const pendingCount = computed(() =>
  allDeadlines.value.filter(t => t.status !== 'filed' && t.status !== 'waived').length
)

// 申报截止日期
const nearestDueDate = computed(() => {
  const dates = allDeadlines.value
    .filter((item: any) => item.status !== 'filed' && item.status !== 'waived')
    .map((item: any) => item.dueDate)
    .filter(Boolean)
    .sort()
  return dates[0] || '本月 15 日'
})

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
    <!-- 顶部核心免税结论 Banner -->
    <section
      class="tax-hero-banner"
      :class="hasBlockers ? 'has-blocker' : (gauge.isExempt ? 'exempt' : 'taxable')"
    >
      <div class="hero-left">
        <div class="hero-badge">
          <i :class="hasBlockers ? 'ri-alert-fill' : (gauge.isExempt ? 'ri-shield-check-fill' : 'ri-information-fill')" />
          <span>{{ insight.badgeText }}</span>
        </div>
        <h1 class="hero-title">
          {{ gauge.isExempt ? '本期享受小微免税，增值税应缴 ¥0.00' : `本期预计应缴税款 ¥${formatMoney(totalTax)}` }}
        </h1>
        <p class="hero-subtitle">
          {{ gauge.tip }}
        </p>

        <!-- 申报阻断项提醒（解决“申报受阻”但无原因说明的问题） -->
        <div v-if="hasBlockers" class="blocker-alert-box">
          <div class="blocker-alert-head">
            <i class="ri-error-warning-line" />
            <span>发现 {{ blockingIssues.length }} 项待确认事项，建议核对后再去电子税局申报：</span>
            <NButton size="tiny" type="warning" secondary @click="emit('switchMode', 'accounting')">
              去底稿核对处理
            </NButton>
          </div>
          <ul class="blocker-list">
            <li v-for="(issue, idx) in blockingIssues" :key="idx">{{ issue }}</li>
          </ul>
        </div>

        <!-- 额度进度条 -->
        <div class="gauge-bar-wrapper">
          <div class="gauge-labels">
            <span>已使用免税额度 {{ formatMoney(gauge.totalSales) }}</span>
            <span>免税上限 {{ formatMoney(gauge.threshold) }}</span>
          </div>
          <NProgress
            type="line"
            :percentage="gauge.percent"
            :status="gauge.status === 'danger' ? 'error' : (gauge.status === 'warning' ? 'warning' : 'success')"
            :show-indicator="false"
            style="height: 8px"
          />
        </div>
      </div>

      <div class="hero-right">
        <div class="due-card">
          <span class="due-label">申报截止日期</span>
          <strong class="due-date">{{ nearestDueDate }}</strong>
          <span class="due-status">
            {{ pendingCount ? `还有 ${pendingCount} 项申报待确认` : '本期申报已办结' }}
          </span>
        </div>
      </div>
    </section>

    <!-- 电子税务局极简小抄 -->
    <section class="cheat-sheet-card">
      <header class="cheat-sheet-header">
        <div class="header-titles">
          <i class="ri-file-copy-2-line header-icon" />
          <div>
            <h2>电子税务局「照抄申报卡」</h2>
            <p>登录电子税务局网页后，按照以下 4 步直接复制金额并确认归档（点击复制均为无逗号纯数字）：</p>
          </div>
        </div>
        <NButton secondary size="small" @click="emit('switchMode', 'accounting')">
          查看完整专业税表底稿
        </NButton>
      </header>

      <div class="sheet-steps">
        <!-- 步骤 1：增值税申报 -->
        <div class="step-card" :class="{ 'step-done': isVatDone }">
          <div class="step-badge">{{ isVatDone ? '✓' : '1' }}</div>
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

        <!-- 步骤 2：企业所得税预缴 -->
        <div class="step-card" :class="{ 'step-done': isCitDone }">
          <div class="step-badge">{{ isCitDone ? '✓' : '2' }}</div>
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

        <!-- 步骤 3：财务报表报送 -->
        <div v-if="finStatementTask" class="step-card" :class="{ 'step-done': finStatementTask.status === 'filed' || finStatementTask.status === 'waived' }">
          <div class="step-badge">{{ (finStatementTask.status === 'filed' || finStatementTask.status === 'waived') ? '✓' : '3' }}</div>
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

        <!-- 步骤 4：其他必报事项确认（印花税 / 个税扣缴 / 工商年报） -->
        <div class="step-card">
          <div class="step-badge">{{ finStatementTask ? '4' : '3' }}</div>
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
    </section>
  </div>
</template>

<style scoped>
.boss-tax-sheet-container {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

/* 顶部定心丸 Banner */
.tax-hero-banner {
  border-radius: var(--solo-radius-xl, 12px);
  padding: 24px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 24px;
  border: 1px solid var(--solo-border, #dde3ea);
  background: var(--solo-bg-card, #ffffff);
}

.tax-hero-banner.exempt {
  background: linear-gradient(135deg, #f0faf5 0%, #ffffff 70%);
  border-color: #bfe7d5;
}

.tax-hero-banner.taxable {
  background: linear-gradient(135deg, #f5f8ff 0%, #ffffff 70%);
  border-color: #cadcff;
}

.tax-hero-banner.has-blocker {
  background: linear-gradient(135deg, #fffaf0 0%, #ffffff 75%);
  border-color: #f3d29c;
}

.hero-left {
  flex: 1;
  max-width: 680px;
}

.hero-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  margin-bottom: 8px;
}

.tax-hero-banner.exempt .hero-badge {
  background: var(--solo-income-soft, #eaf7f1);
  color: var(--solo-income, #0f7354);
}

.tax-hero-banner.taxable .hero-badge {
  background: var(--solo-primary-soft, #edf3ff);
  color: var(--solo-primary, #1747b0);
}

.tax-hero-banner.has-blocker .hero-badge {
  background: #fef3c7;
  color: #b45309;
}

.blocker-alert-box {
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 8px;
  padding: 10px 14px;
  margin-bottom: 14px;
  color: #92400e;
  font-size: 12.5px;
}

.blocker-alert-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  margin-bottom: 6px;
}

.blocker-alert-head span {
  flex: 1;
}

.blocker-list {
  margin: 0;
  padding-left: 18px;
  line-height: 1.5;
}

.hero-title {
  margin: 0 0 6px;
  font-size: 24px;
  font-weight: 700;
  color: var(--solo-text-primary, #182230);
  letter-spacing: -0.3px;
}

.hero-subtitle {
  margin: 0 0 16px;
  font-size: 13.5px;
  color: var(--solo-text-secondary, #586474);
  line-height: 1.5;
}

.gauge-bar-wrapper {
  background: #ffffff;
  padding: 12px 14px;
  border-radius: 8px;
  border: 1px solid var(--solo-border-light, #edf0f4);
}

.gauge-labels {
  display: flex;
  justify-content: space-between;
  font-size: 11.5px;
  color: var(--solo-text-muted, #636e7c);
  margin-bottom: 6px;
}

.hero-right {
  flex-shrink: 0;
}

.due-card {
  background: #ffffff;
  border: 1px solid var(--solo-border-light, #edf0f4);
  border-radius: 12px;
  padding: 18px 24px;
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.03);
}

.due-label {
  font-size: 12px;
  color: var(--solo-text-secondary, #586474);
  margin-bottom: 4px;
}

.due-date {
  font-size: 20px;
  font-weight: 700;
  color: var(--solo-primary, #1747b0);
  margin-bottom: 4px;
}

.due-status {
  font-size: 11.5px;
  color: var(--solo-text-muted, #636e7c);
}

/* 小抄卡片 */
.cheat-sheet-card {
  background: var(--solo-bg-card, #ffffff);
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: var(--solo-radius-xl, 12px);
  padding: 24px;
}

.cheat-sheet-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--solo-border-light, #edf0f4);
}

.header-titles {
  display: flex;
  align-items: center;
  gap: 12px;
}

.header-icon {
  font-size: 26px;
  color: var(--solo-primary, #1747b0);
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

@media (max-width: 800px) {
  .tax-hero-banner {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>
