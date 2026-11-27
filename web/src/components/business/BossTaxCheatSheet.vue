<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { NButton, useMessage } from 'naive-ui'
import {
  VAT_DECLARATION_DEMO_URL,
  calculateExemptionGauge,
  formatCopyAmount,
  type VatFilingMode,
} from '@/utils/taxFiling'
import { copyToClipboard } from '@/utils/clipboard'
import { formatMoney } from '@/utils/format'
import GuideTrigger from '@/components/business/GuideTrigger.vue'

const props = withDefaults(defineProps<{
  pkg: any
  period: string
  loading?: boolean
  exporting?: boolean
  /** 税局新版表单入口：确认式（本期销售情况）/ 填表式（报表列表） */
  vatMode?: VatFilingMode
}>(), {
  vatMode: 'confirm',
})

const emit = defineEmits<{
  (e: 'switchMode', mode: 'accounting'): void
  (e: 'refresh'): void
  (e: 'markFiled', deadlineId: string | null): void
  (e: 'exportXls'): void
  (e: 'calibrateCit'): void
}>()

const message = useMessage()

// 销售额与免税额度监控
const totalSales = computed(() => Number(props.pkg?.vat?.totalSales || 0))
const periodType = computed(() => props.pkg?.periodType || 'quarter')
const gauge = computed(() => calculateExemptionGauge(totalSales.value, props.period, periodType.value))

// 预计税额
const vatPayable = computed(() => Number(props.pkg?.summary?.vatPayable ?? props.pkg?.vat?.totalVatPayable ?? 0))
const vatReduction = computed(() => Number(props.pkg?.vat?.vatReduction || 0))
const uninvoicedPositive = computed(() => Number(props.pkg?.vat?.uninvoicedPositiveSales || 0))
const hasUninvoiced = computed(() =>
  uninvoicedPositive.value > 0 || Number(props.pkg?.vat?.uninvoicedNegativeSales || 0) < 0,
)
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
const citPriorLoss = computed(() => Number(props.pkg?.cit?.priorYearLoss ?? 0))
const citPrepaid = computed(() => Number(props.pkg?.cit?.prepaidCitTax ?? 0))

const vatDeadline = computed(() => props.pkg?.vat?.deadline || null)
const citDeadline = computed(() => props.pkg?.cit?.deadline || null)
const isTaskDone = (t: any) => t?.status === 'filed' || t?.status === 'waived'
const isVatDone = computed(() => isTaskDone(vatDeadline.value))
const isCitDone = computed(() => isTaskDone(citDeadline.value))

// 申报任务清单（含主表与附表）
const tasks = computed(() => props.pkg?.checklist || [])
const finStatementTask = computed(() => tasks.value.find((t: any) => t.taxKind === 'fin_statements') || null)
const isFinDone = computed(() => isTaskDone(finStatementTask.value))
const otherTasks = computed(() => {
  const list = tasks.value.filter((t: any) => t.taxKind !== 'fin_statements')
  return [...list.filter((t: any) => !isTaskDone(t)), ...list.filter((t: any) => isTaskDone(t))]
})
const isOtherDone = computed(() => otherTasks.value.every((t: any) => isTaskDone(t)))

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
const isAllNearTermDone = computed(() => allDeadlines.value.length > 0 && pendingCount.value === 0)

// 已办结卡片默认折叠，点击「展开核对」可随时回看填报金额或导出三表
const expandedDoneSteps = ref<Record<string, boolean>>({})
const isStepCollapsed = (key: string, done: boolean) => done && !expandedDoneSteps.value[key]
function toggleStep(key: string) {
  expandedDoneSteps.value[key] = !expandedDoneSteps.value[key]
}

const celebrationMessage = computed(() => {
  const isZeroTax = vatPayable.value === 0 && citPayable.value === 0
  const pool = isZeroTax
    ? [
        '本期零税额丝滑通关，老板安心搞业务！',
        '账清税明，免税申报完美收官！',
        '本期全部办结，合规拿捏得稳稳的！',
      ]
    : [
        '本期税费全数通关，完美收官！',
        '全部申报打卡成功，放心翻篇！',
        '本期已全部办结，祝生意长红！',
      ]
  const seed = props.period.split('').reduce((acc, ch) => acc + ch.charCodeAt(0), 0)
  return pool[seed % pool.length]
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
    <!-- 电子税务局极简小抄 -->
    <section class="cheat-sheet-card">
      <div v-if="isAllNearTermDone" class="all-done-celebration">
        <div class="celebration-icon" aria-hidden="true">
          <i class="ri-thumb-up-fill" />
        </div>
        <div class="celebration-title">{{ celebrationMessage }}</div>
      </div>

      <div class="sheet-steps">
        <!-- 步骤 1：财务报表报送（申报顺序：先报送财报，再增值税、企业所得税） -->
        <div
          v-if="finStatementTask"
          class="step-card"
          :class="{ 'step-done': isFinDone, 'step-collapsed': isStepCollapsed('fin', isFinDone) }"
        >
          <div class="step-badge">{{ isFinDone ? '✓' : '1' }}</div>
          <div class="step-content">
            <div class="step-head">
              <h3>{{ finStatementTask.name || '财务报表报送' }}（资产负债表 + 利润表）</h3>
              <span class="step-tag" :class="{ 'tag-done': isFinDone }">
                {{ isFinDone ? '已报送办结' : '必报附表' }}
              </span>
              <div v-if="isFinDone" class="step-head-actions">
                <NButton text size="tiny" @click="toggleStep('fin')">
                  {{ expandedDoneSteps.fin ? '收起' : '展开核对' }}
                </NButton>
              </div>
            </div>
            <template v-if="!isStepCollapsed('fin', isFinDone)">
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
                  v-if="!isFinDone"
                  type="primary"
                  size="small"
                  class="btn-mark-filed"
                  @click="emit('markFiled', finStatementTask.deadlineId)"
                >
                  <template #icon><i class="ri-checkbox-circle-line" /></template>
                  标记「{{ finStatementTask.name }}」已申报办结
                </NButton>
              </div>
            </template>
          </div>
        </div>

        <!-- 步骤 2：增值税申报 -->
        <div
          class="step-card"
          :class="{ 'step-done': isVatDone, 'step-collapsed': isStepCollapsed('vat', isVatDone) }"
        >
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
                  size="small"
                  class="btn-mark-filed"
                  @click="emit('markFiled', vatDeadline.deadlineId)"
                >
                  <template #icon><i class="ri-checkbox-circle-line" /></template>
                  标记「增值税及附加税费」已申报办结
                </NButton>
                <NButton v-else-if="isVatDone" text size="tiny" @click="toggleStep('vat')">
                  {{ expandedDoneSteps.vat ? '收起' : '展开核对' }}
                </NButton>
              </div>
            </div>
            <template v-if="!isStepCollapsed('vat', isVatDone)">
              <p class="step-instruction">
                <template v-if="vatMode === 'form'">
                  进入电子税局【我要办税】➔【税费申报及缴纳】➔【增值税及附加税费申报（小规模纳税人）】➔ 左侧「报表列表」（填表式）
                </template>
                <template v-else>
                  进入电子税局【我要办税】➔【税费申报及缴纳】➔【增值税及附加税费申报】
                </template>
              </p>
              <a
                class="step-demo-link"
                :href="VAT_DECLARATION_DEMO_URL"
                target="_blank"
                rel="noopener noreferrer"
              >
                <i class="ri-play-circle-line" aria-hidden="true"></i>
                看新版税局操作演示（浙江版，第三方）
              </a>

              <div class="fields-copy-box">
                <div class="field-row">
                  <span class="field-name">
                    <template v-if="vatMode === 'form'">
                      {{ gauge.isExempt
                        ? '本期销售额（已开票自动预填，未开票收入填附1 第 2 列；主表归入第 10 栏免税销售额）：'
                        : '主表第 1 栏【应征增值税不含税销售额】（1% 征收率不含税销售额）：' }}
                    </template>
                    <template v-else>
                      {{ gauge.isExempt
                        ? '弹窗「1% 征收率」行填入销售额（税局自动归入第 10 栏免税销售额，第 1 栏【本期应税销售额】为 0.00）：'
                        : '第 1 栏【本期应税销售额】（1% 征收率不含税销售额）：' }}
                    </template>
                  </span>
                  <span class="field-val">{{ formatMoney(totalSales) }}</span>
                  <NButton size="tiny" secondary @click="copyAmount(totalSales, '销售额')">
                    一键复制
                  </NButton>
                </div>

                <div v-if="gauge.isExempt" class="field-row tip-row">
                  <span class="field-name">主表核对要点：</span>
                  <span v-if="vatMode === 'form'" class="field-desc">
                    先在《业务信息表》勾「未开具发票销售业务」（有未开票收入时），{{ hasUninvoiced
                      ? `再到附1 第 2 列填 ${formatMoney(uninvoicedPositive)}；`
                      : '本期无未开票收入，附1 无需填写；' }}回到主表核对<strong>第 10 栏【小微企业免税销售额】</strong>= {{ formatMoney(totalSales) }}，第 1 栏为 0.00，<strong>第 22 栏【本期应补(退)税额】为 0.00</strong>。
                  </span>
                  <span v-else class="field-desc">
                    在「本期销售情况」弹窗填入 {{ formatMoney(totalSales) }} 后，主表<strong>第 10 栏【小微企业免税销售额】</strong>自动带出 {{ formatMoney(totalSales) }}，第 1 栏为 0.00，<strong>第 22 栏【本期应补(退)税额】为 0.00</strong>，核对无误直接提交即可。
                  </span>
                </div>

                <div v-else-if="vatMode === 'form'" class="field-row tip-row">
                  <span class="field-name">减免税核对：</span>
                  <span class="field-desc">
                    在《增值税减免税申报明细表》「减税项目」选择系统预填的「小规模纳税人减按1%征收率征收增值税」，本期减税额 <strong>{{ formatMoney(vatReduction) }}</strong>；主表<strong>第 22 栏【本期应补(退)税额】</strong>应为 {{ formatMoney(vatPayable) }}。
                  </span>
                </div>
              </div>
            </template>
          </div>
        </div>

        <!-- 步骤 3：企业所得税预缴 -->
        <div
          class="step-card"
          :class="{ 'step-done': isCitDone, 'step-collapsed': isStepCollapsed('cit', isCitDone) }"
        >
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
                  size="small"
                  class="btn-mark-filed"
                  @click="emit('markFiled', citDeadline.deadlineId)"
                >
                  <template #icon><i class="ri-checkbox-circle-line" /></template>
                  标记「企业所得税预缴」已申报办结
                </NButton>
                <NButton v-else-if="isCitDone" text size="tiny" @click="toggleStep('cit')">
                  {{ expandedDoneSteps.cit ? '收起' : '展开核对' }}
                </NButton>
              </div>
            </div>
            <template v-if="!isStepCollapsed('cit', isCitDone)">
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

              <div class="cit-calibrate-row">
                <span v-if="citPriorLoss > 0 || citPrepaid > 0">
                  已按校准基数扣减：弥补以前年度亏损 ¥{{ formatMoney(citPriorLoss) }} · 本年已预缴 ¥{{ formatMoney(citPrepaid) }}
                </span>
                <span v-else>
                  去税局填表时，以前亏损和本年已预缴会自动带出；如需同步校准此处的预估税额 →
                </span>
                <NButton text size="tiny" type="primary" @click="emit('calibrateCit')">
                  {{ citPriorLoss > 0 || citPrepaid > 0 ? '修改校准' : '校准亏损 / 已预缴' }}
                </NButton>
              </div>
            </template>
          </div>
        </div>

        <!-- 步骤 4：其他必报事项确认（印花税 / 个税扣缴 / 工商年报） -->
        <div
          class="step-card"
          :class="{ 'step-done': isOtherDone, 'step-collapsed': isStepCollapsed('other', isOtherDone) }"
        >
          <div class="step-badge">{{ isOtherDone ? '✓' : '4' }}</div>
          <div class="step-content">
            <div class="step-head">
              <h3>其他税种及必报事项确认</h3>
              <span v-if="isOtherDone" class="step-tag tag-done">已全部办结</span>
              <div v-if="isOtherDone && otherTasks.length > 0" class="step-head-actions">
                <NButton text size="tiny" @click="toggleStep('other')">
                  {{ expandedDoneSteps.other ? '收起' : '展开核对' }}
                </NButton>
              </div>
            </div>
            <template v-if="!isStepCollapsed('other', isOtherDone)">
              <p class="step-instruction">
                除增值税、企业所得税和财报外，请按下方指引完成个税扣缴、印花税等常规申报并点击归档：
                <GuideTrigger guide="iit" label="个税怎么申报？" />
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
                    <GuideTrigger
                      v-if="task.taxKind === 'social_insurance' && task.status !== 'filed' && task.status !== 'waived'"
                      guide="social-insurance"
                      label="社保怎么申报？"
                    />
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
                      size="small"
                      class="btn-mark-filed"
                      @click="emit('markFiled', task.deadlineId)"
                    >
                      <template #icon><i class="ri-checkbox-circle-line" /></template>
                      标记「{{ task.name }}」已申报办结
                    </NButton>
                  </div>
                </div>
              </div>
            </template>
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

.all-done-celebration {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 12px 18px;
  margin-bottom: 16px;
  border-radius: 12px;
  background: linear-gradient(135deg, #fffbeb 0%, #f0f7ff 100%);
  border: 1px solid #fde68a;
}

.celebration-icon {
  width: 48px;
  height: 48px;
  border-radius: 50%;
  background: linear-gradient(135deg, #f59e0b 0%, #f97316 100%);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 24px;
  flex-shrink: 0;
  animation: thumb-ring-pulse 2.2s ease-out 1;
}

.celebration-icon i {
  display: inline-block;
  transform-origin: 35% 75%;
  animation: thumb-like-pop 0.75s cubic-bezier(0.22, 1.4, 0.36, 1) 1;
}

.celebration-title {
  font-size: 14.5px;
  font-weight: 700;
  color: var(--solo-text-primary, #182230);
  line-height: 1.45;
}

@keyframes thumb-like-pop {
  0% {
    transform: scale(0.55) rotate(14deg);
    opacity: 0;
  }
  45% {
    transform: scale(1.25) rotate(-14deg);
    opacity: 1;
  }
  72% {
    transform: scale(0.95) rotate(6deg);
  }
  100% {
    transform: scale(1) rotate(0deg);
  }
}

@keyframes thumb-ring-pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(245, 158, 11, 0.36);
  }
  60% {
    box-shadow: 0 0 0 12px rgba(245, 158, 11, 0);
  }
  100% {
    box-shadow: 0 0 0 0 rgba(245, 158, 11, 0);
  }
}

@media (prefers-reduced-motion: reduce) {
  .celebration-icon,
  .celebration-icon i {
    animation: none;
  }
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
  order: 1;
  background: #f8fbfa;
  border-color: #d6ede3;
}

.step-card.step-done.step-collapsed {
  padding: 12px 18px;
  align-items: center;
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
  color: var(--solo-income);
}

.step-instruction {
  margin: 0;
  font-size: 13px;
  color: var(--solo-text-secondary, #586474);
}

.step-demo-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: 6px;
  font-size: 12px;
  color: var(--solo-primary, #1747b0);
  text-decoration: none;
}

.step-demo-link:hover {
  text-decoration: underline;
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

.cit-calibrate-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
  margin-top: 8px;
  padding: 7px 10px;
  border-radius: var(--solo-radius-sm);
  background: var(--solo-bg-soft);
  color: var(--solo-text-secondary);
  font-size: 12px;
  line-height: 1.45;
}

:deep(.btn-mark-filed) {
  font-weight: 600;
  box-shadow: 0 1.5px 4px rgba(2, 132, 199, 0.28), 0 1px 2px rgba(2, 132, 199, 0.16);
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
  letter-spacing: 0.2px;
}

:deep(.btn-mark-filed:hover) {
  transform: translateY(-1px);
  box-shadow: 0 4px 10px rgba(2, 132, 199, 0.38);
}

:deep(.btn-mark-filed:active) {
  transform: translateY(0);
}
</style>
