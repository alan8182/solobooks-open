<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script lang="ts">
// 报表口径规则 + 双轨对比 —— 纯逻辑部分（可被 vitest 直接覆盖）。
//
// 为什么单独一个 <script> 块：<script setup> 不允许具名导出，而本页最需要单测保护的
// 恰恰是「保存前全量校验」——PUT /report-line-rules 在后端是 delete + 重建
// （report_line_rule_service.py:400-404），且派生行 / 重复 lineCode / 空 patterns
// 都在**循环内**才抛错 → 整批事务回滚。用户配 20 行、第 18 行出错，
// 原有 20 行规则全部回滚、用户输入丢失。所以必须**保存前一次性拦掉全部错误**，
// 这段逻辑不能藏在组件里靠人肉点按钮验证。

import { DERIVED_LINES, validatePattern } from '@/utils/aggregation'
import type {
  ReportKind,
  ReportLinePatternMode,
  ReportLineRuleInput,
  ReportLineSign,
} from '@/api/types'

/** 页面上的规则草稿行（比后端模型多一个本地 key，用于 v-for 与行内定位） */
export interface ReportLineRuleDraft {
  key: string
  lineCode: string
  lineName: string
  patterns: PatternDraft[]
  sign: ReportLineSign
  status: 'active' | 'archived'
  effectiveFrom: string
  effectiveTo: string
}

/** 本地 pattern 草稿：mode/value/sign 之外还带一个本地 key */
export interface PatternDraft {
  key: string
  mode: ReportLinePatternMode
  value: string
  sign: 1 | -1
}

let draftSeq = 0
/** 单调递增的本地 key。只用于 v-for :key 与错误定位，不参与提交 */
export function nextKey(prefix = 'row'): string {
  draftSeq += 1
  return `${prefix}-${draftSeq}`
}

/** 新建一条空白规则草稿 */
export function emptyDraft(): ReportLineRuleDraft {
  return {
    key: nextKey('rule'),
    lineCode: '',
    lineName: '',
    patterns: [],
    sign: 'net',
    status: 'active',
    effectiveFrom: '',
    effectiveTo: '',
  }
}

/** 由后端返回的规则回填成草稿行（进入编辑态时用） */
export function draftFromRule(rule: {
  lineCode: string
  lineName?: string | null
  patterns?: Array<{ mode: ReportLinePatternMode; value: string; sign: number }>
  sign?: string
  status?: string
  effectiveFrom?: string | null
  effectiveTo?: string | null
}): ReportLineRuleDraft {
  return {
    key: nextKey('rule'),
    lineCode: rule.lineCode,
    lineName: rule.lineName ?? '',
    patterns: (rule.patterns ?? []).map((pattern) => ({
      key: nextKey('pat'),
      mode: pattern.mode,
      value: pattern.value,
      sign: pattern.sign === -1 ? -1 : 1,
    })),
    sign: (rule.sign === 'debit' || rule.sign === 'credit' ? rule.sign : 'net') as ReportLineSign,
    status: rule.status === 'archived' ? 'archived' : 'active',
    effectiveFrom: rule.effectiveFrom ?? '',
    effectiveTo: rule.effectiveTo ?? '',
  }
}

/** 草稿 → 提交体。空的可选字段一律转 null，避免把 '' 当成有值提交 */
export function draftToInput(draft: ReportLineRuleDraft): ReportLineRuleInput {
  return {
    lineCode: draft.lineCode.trim(),
    lineName: draft.lineName.trim() || null,
    patterns: draft.patterns.map((pattern) => ({
      mode: pattern.mode,
      value: pattern.value.trim(),
      sign: pattern.sign,
    })),
    sign: draft.sign,
    status: draft.status,
    effectiveFrom: draft.effectiveFrom.trim(),
    effectiveTo: draft.effectiveTo.trim() || null,
  }
}

/** 该 lineCode 是否是派生行（不可配置化）。与后端 DERIVED_LINES 同步 */
export function isDerived(report: ReportKind, lineCode: string): boolean {
  return (DERIVED_LINES[report] ?? []).includes(lineCode.trim())
}

/**
 * 保存前全量本地校验。返回**全部**问题（不是第一个），因为后端是整批 delete+重建，
 * 逐个弹窗既慢又会让人漏看。返回空数组表示通过。
 *
 * 校验项与后端 report_line_rule_service._validate_line / _parse_patterns 镜像：
 * ① lineCode 非空  ② 不在派生行清单  ③ 草稿内不重复（Set 先于提交拦截）
 * ④ patterns 非空且每条过 validatePattern  ⑤ effectiveFrom 必填  ⑥ effectiveTo ≥ effectiveFrom
 */
export function validateDrafts(report: ReportKind, drafts: ReportLineRuleDraft[]): string[] {
  const errors: string[] = []

  if (!drafts.length) return ['请至少配置一条规则']

  const seen = new Map<string, number>()
  drafts.forEach((draft, index) => {
    const rowNo = index + 1
    const label = draft.lineCode.trim() || `第 ${rowNo} 行`
    const code = draft.lineCode.trim()

    if (!code) {
      errors.push(`第 ${rowNo} 行：报表项编码（lineCode）不能为空`)
    } else {
      if (isDerived(report, code)) {
        errors.push(`第 ${rowNo} 行「${code}」：派生行由基础行计算得出，不允许配置规则`)
      }
      if (seen.has(code)) {
        errors.push(`第 ${rowNo} 行「${code}」：报表项编码与第 ${seen.get(code)} 行重复`)
      } else {
        seen.set(code, rowNo)
      }
    }

    if (!draft.patterns.length) {
      errors.push(`第 ${rowNo} 行「${label}」：至少配置一个科目匹配（patterns）`)
    } else {
      draft.patterns.forEach((pattern, patternIndex) => {
        const message = validatePattern({
          mode: pattern.mode,
          value: pattern.value,
          sign: pattern.sign,
        })
        if (message) {
          errors.push(`第 ${rowNo} 行「${label}」第 ${patternIndex + 1} 个科目匹配：${message}`)
        }
      })
    }

    if (!draft.effectiveFrom.trim()) {
      errors.push(`第 ${rowNo} 行「${label}」：生效起始期（effectiveFrom）必填`)
    }

    const from = draft.effectiveFrom.trim()
    const to = draft.effectiveTo.trim()
    if (to && from && to < from) {
      errors.push(`第 ${rowNo} 行「${label}」：失效期（${to}）不得早于生效期（${from}）`)
    }
  })

  return errors
}

/**
 * 双轨对比结果的三态文案。
 *
 * ⚠️ skipped（未配规则）与 clean（零差异）**必须区分**：
 * 后端在 skipped 分支里 clean 也是 true，但那是占位值、没有真正比对过。
 * 若一律显示「零差异通过」，用户会以为口径已验证——这是本页最容易发生的误导。
 */
export type CompareOutcome = 'skipped' | 'clean' | 'diff'

export function compareOutcome(result: {
  skipped: boolean
  clean: boolean
  diffs?: unknown[]
  compared?: number
  ruleCount?: number
}): CompareOutcome {
  if (result.skipped) return 'skipped'
  if ((result.diffs?.length ?? 0) > 0) return 'diff'
  if (result.clean) return 'clean'
  return 'diff'
}

/** 三态对应的 NAlert 类型与文案 */
export function compareAlert(result: {
  skipped: boolean
  clean: boolean
  diffs?: Array<unknown>
  compared?: number
  ruleCount?: number
}): { type: 'info' | 'success' | 'warning'; text: string } {
  const outcome = compareOutcome(result)
  // ⚠️ ruleCount 是可选字段：diff_report_lines 的 skipped 早退分支不返回它
  const ruleCountText = result.ruleCount === undefined ? '' : `，规则数 ${result.ruleCount}`

  if (outcome === 'skipped') {
    return {
      type: 'info',
      text: '未配置任何启用中的规则，本次未做对比。「没配规则」不等于「口径有差异」。',
    }
  }
  if (outcome === 'clean') {
    return {
      type: 'success',
      text: `本期 ${result.compared ?? 0} 个基础行逐行一致（对比行数 ${result.compared ?? 0}${ruleCountText}）。`,
    }
  }
  return {
    type: 'warning',
    text: `本期存在 ${result.diffs?.length ?? 0} 行口径差异（对比行数 ${result.compared ?? 0}${ruleCountText}）。差异不消除，连续零差异期数不会增长。`,
  }
}

/** 差异金额的展示。正差额（配置口径更高）用绿、负差额用红——沿用 pending 页的语义色 */
export function deltaTagType(delta: number): 'success' | 'error' | 'default' {
  if (delta > 0) return 'success'
  if (delta < 0) return 'error'
  return 'default'
}
</script>

<script setup lang="ts">
// 本页的三条「产品级」铁律（设计文档 §7.1 / §11 风险 5）：
//
// 1. **不接管对外报表**。规则只用于双轨对比；未达连续 3 期零差异前，所有对外报表与
//    申报导出仍按现有硬编码口径生成。所以本页**不提供任何「切换 / 生效」按钮**——
//    后端也没有切换端点（require_switch_ready 无路由调用），UI 给出入口就是骗人。
// 2. **派生行不可配置**。三重表达：编辑按钮 disabled + 「派生」NTag + 手填 lineCode 即时红字。
//    前端靠 utils/aggregation.ts 的 DERIVED_LINES 常量提前拦，不依赖后端 422。
// 3. **保存是整批 PUT（delete + 重建）**，任一行出错整批回滚。所以保存前必须全量本地校验、
//    错误汇总在**一个** NAlert 里一次性列出；保存失败时**保留本地 draft**，让用户能改。
import { computed, h, onMounted, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NDatePicker,
  NInput,
  NModal,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSpace,
  NSwitch,
  NTable,
  NTag,
  NText,
  NTooltip,
  useDialog,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import {
  archiveReportLineRule,
  compareReportLineRules,
  getSwitchReadiness,
  listReportLineRules,
  replaceReportLineRules,
} from '@/api/aggregation'
import type { ReportLineCompareResult, SwitchReadinessResult } from '@/api/aggregation'
import type { ReportLineRule } from '@/api/types'
// ⚠️ ReportKind / validatePattern / DERIVED_LINES 等已在上方 <script> 块导入。
// Vue SFC 会把两个块合并进同一个模块作用域，重复 import 会触发 TS2300，故此处不再导入。
import {
  DERIVED_LINE_LABELS,
  REPORT_LINE_SIGN_OPTIONS,
  RULE_SAVED_NOTE,
  errorText,
  patternExpression,
} from '@/utils/aggregation'
import { formatMoney } from '@/utils/format'

const message = useMessage()
const dialog = useDialog()

const reportKind = ref<ReportKind>('profit')

const rules = ref<ReportLineRule[]>([])
const loading = ref(false)
const saving = ref(false)

/**
 * ⚠️ 后端 list 端点**没有** status/includeArchived 过滤参数，只能拉全量后本地过滤。
 * 开关文案必须明说「含已归档」，否则用户以为看到的是全量有效规则。
 */
const includeArchived = ref(false)

const draft = ref<ReportLineRuleDraft[]>([])
/** 保存前全量校验的错误汇总。一次全列出，不逐个弹窗（风险 5 的直接对策） */
const draftErrors = ref<string[]>([])
const editing = ref(false)

const readiness = ref<SwitchReadinessResult | null>(null)
const compareResult = ref<ReportLineCompareResult | null>(null)
const comparing = ref(false)

/** 对比期间。月报 YYYY-MM，季报 YYYY-Qn */
const comparePeriodType = ref<'month' | 'quarter'>('month')
const compareMonth = ref('')
const compareQuarter = ref('')

/**
 * NDatePicker 在 value-format='yyyy-MM' 时**运行期**交出 YYYY-MM 字符串，
 * 但它的 Value 类型声明只认 number。用 computed 收敛边界，避免模板里写断言
 * （与 pending.vue 的 applyMonthRange 同一手法）。
 */
const compareMonthValue = computed<any>(() => compareMonth.value || null)
function onCompareMonthUpdate(value: any): void {
  compareMonth.value = typeof value === 'string' ? value : ''
}

/** 规则生效期的两个 DatePicker 同样走 formatted-value 桥接 */
function draftFromValue(ruleKey: string, field: 'effectiveFrom' | 'effectiveTo'): any {
  const target = draft.value.find((row) => row.key === ruleKey)
  return target?.[field] || null
}
function onDraftDateUpdate(ruleKey: string, field: 'effectiveFrom' | 'effectiveTo', value: any): void {
  const target = draft.value.find((row) => row.key === ruleKey)
  if (!target) return
  target[field] = typeof value === 'string' ? value : ''
}

const SWITCH_NOTE =
  '本模块不接管对外报表：规则仅用于双轨对比，未达连续 3 期零差异前，对外报表口径不变。'

const ARCHIVE_NOTE = '归档后该行不再参与口径计算，历史可追溯。'

const reportOptions: Array<{ label: string; value: ReportKind }> = [
  { label: '利润表', value: 'profit' },
  { label: '资产负债表', value: 'balance' },
]

const derivedSet = computed(() => new Set(DERIVED_LINES[reportKind.value] ?? []))

const visibleRules = computed(() =>
  includeArchived.value ? rules.value : rules.value.filter((rule) => rule.status === 'active'),
)

const archivedCount = computed(() => rules.value.filter((rule) => rule.status === 'archived').length)

/** 编辑弹层里「当前聚焦行」的即时派生行红字 */
const editingDerivedHit = computed(() => {
  if (!editing.value) return false
  return draft.value.some((row) => row.lineCode.trim() && isDerived(reportKind.value, row.lineCode))
})

// ===== 数据加载 =====

async function load(): Promise<void> {
  loading.value = true
  try {
    rules.value = await listReportLineRules(reportKind.value)
  } catch (err) {
    rules.value = []
    message.error(errorText(err, '报表口径规则加载失败'))
  } finally {
    loading.value = false
  }
}

async function loadReadiness(): Promise<void> {
  try {
    readiness.value = await getSwitchReadiness(reportKind.value)
  } catch {
    // 就绪度是辅助信息，取不到不打扰用户
    readiness.value = null
  }
}

// ===== 草稿与校验 =====

function openEditor(): void {
  // ⚠️ 草稿永远取**全量 rules**（含已归档），不能取 visibleRules：
  // PUT 是整批 delete+重建，若草稿漏了归档行，保存一次就把它们永久删掉。
  // includeArchived 只影响表格展示，不影响草稿与提交范围。
  draft.value = rules.value.map((item) => draftFromRule(item))
  draftErrors.value = []
  editing.value = true
}

function addRule(): void {
  draft.value = [...draft.value, emptyDraft()]
}

function removeRule(key: string): void {
  draft.value = draft.value.filter((row) => row.key !== key)
}

function addPattern(ruleKey: string): void {
  const target = draft.value.find((row) => row.key === ruleKey)
  if (!target) return
  target.patterns = [...target.patterns, { key: nextKey('pat'), mode: 'prefix', value: '', sign: 1 }]
}

function removePattern(ruleKey: string, patternKey: string): void {
  const target = draft.value.find((row) => row.key === ruleKey)
  if (!target) return
  target.patterns = target.patterns.filter((pattern) => pattern.key !== patternKey)
}

/** 单个 pattern 的即时校验（输入即红字，不等提交）。直接复用 utils 的实现，不写第二份 */
function patternError(mode: ReportLinePatternMode, value: string): string | null {
  return validatePattern({ mode, value, sign: 1 })
}

const signOptions = REPORT_LINE_SIGN_OPTIONS.map((option) => ({
  label: option.label,
  value: option.value,
}))

function signTooltip(value: string): string {
  return REPORT_LINE_SIGN_OPTIONS.find((option) => option.value === value)?.tooltip ?? ''
}

const patternModeOptions = [
  { label: '科目前缀', value: 'prefix' },
  { label: '精确科目', value: 'exact' },
  { label: '科目区间', value: 'range' },
]

async function saveRules(): Promise<void> {
  // 风险 5：全量校验先行，一次性汇总所有问题
  const errors = validateDrafts(reportKind.value, draft.value)
  draftErrors.value = errors
  if (errors.length) return

  saving.value = true
  try {
    // PUT 返回**裸数组**（后端 replace_rules 末 return after，after 是 list），
    // 直接用它刷新本地列表，不用再发一次 GET
    rules.value = await replaceReportLineRules(
      reportKind.value,
      draft.value.map((row) => draftToInput(row)),
    )
    editing.value = false
    message.success(RULE_SAVED_NOTE)
    await loadReadiness()
  } catch (err) {
    // ⚠️ 失败时**保留本地 draft**：整批 PUT 已回滚，用户输入不能被清掉
    message.error(errorText(err, '规则保存失败，草稿已保留，请修改后重试'))
  } finally {
    saving.value = false
  }
}

function confirmArchive(rule: ReportLineRule): void {
  dialog.warning({
    title: '归档规则',
    content: `${ARCHIVE_NOTE}（${rule.lineCode}）`,
    positiveText: '归档',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        const archived = await archiveReportLineRule(rule.id)
        // 后端是软删（status='archived'），用返回值就地更新，不必重拉
        rules.value = rules.value.map((item) => (item.id === archived.id ? archived : item))
        message.success('已归档。该行不再参与口径计算，历史可追溯。')
      } catch (err) {
        message.error(errorText(err, '归档失败'))
      }
    },
  })
}

// ===== 双轨对比 =====

const canCompare = computed(() =>
  Boolean(comparePeriodType.value === 'month' ? compareMonth.value : compareQuarter.value),
)

async function runCompare(): Promise<void> {
  const period = comparePeriodType.value === 'month' ? compareMonth.value : compareQuarter.value
  if (!period) return
  comparing.value = true
  try {
    const result = await compareReportLineRules({
      report: reportKind.value,
      period,
      periodType: comparePeriodType.value,
    })
    compareResult.value = result
    // 响应里已自带本次运行后的 switchReadiness，无需二次请求
    readiness.value = result.switchReadiness ?? readiness.value
    const alert = compareAlert(result)
    if (alert.type === 'success') message.success(alert.text)
    else if (alert.type === 'warning') message.warning(alert.text)
  } catch (err) {
    // ⚠️ 失败分支不按 REPORT_LINE_DUAL_TRACK_DIFF 写：compare_with_baseline 实际不抛该码，
    //    差异走 diffs 数组返回
    message.error(errorText(err, '双轨对比失败'))
  } finally {
    comparing.value = false
  }
}

// ===== 规则表 =====

function ruleColumns(): DataTableColumns<ReportLineRule> {
  return [
    { title: '报表项编码', key: 'lineCode', width: 200 },
    {
      title: '报表项名称',
      key: 'lineName',
      width: 180,
      render: (row) => row.lineName || DERIVED_LINE_LABELS[row.lineCode] || '-',
    },
    {
      title: '类型',
      key: 'derived',
      width: 90,
      render: (row) =>
        derivedSet.value.has(row.lineCode)
          ? h(NTag, { size: 'small', type: 'info' }, { default: () => '派生' })
          : h(NText, { depth: 3, style: 'font-size:12px' }, { default: () => '基础行' }),
    },
    {
      title: '科目组合',
      key: 'patterns',
      minWidth: 260,
      render: (row) => patternExpression(row.patterns ?? []),
    },
    {
      title: '方向',
      key: 'sign',
      width: 150,
      render: (row) =>
        signTooltip(row.sign)
          ? h(
              NTooltip,
              {},
              {
                trigger: () =>
                  h(
                    NTag,
                    { size: 'small' },
                    {
                      default: () =>
                        REPORT_LINE_SIGN_OPTIONS.find((option) => option.value === row.sign)?.label ??
                        row.sign,
                    },
                  ),
                content: () => signTooltip(row.sign),
              },
            )
          : row.sign,
    },
    { title: '生效期', key: 'effectiveFrom', width: 190,
      render: (row) => `${row.effectiveFrom || '-'} ~ ${row.effectiveTo || '长期'}` },
    {
      title: '状态',
      key: 'status',
      width: 90,
      render: (row) =>
        h(
          NTag,
          { size: 'small', type: row.status === 'active' ? 'success' : 'default' },
          { default: () => (row.status === 'active' ? '启用' : '已归档') },
        ),
    },
    {
      title: '操作',
      key: 'actions',
      width: 120,
      render: (row) =>
        derivedSet.value.has(row.lineCode)
          ? // 派生行三重禁用之一：没有编辑入口
            h(
              NTooltip,
              {},
              {
                trigger: () => h(NButton, { size: 'tiny', disabled: true }, { default: () => '编辑' }),
                content: () => '派生行由基础行计算得出，不允许配置化',
              },
            )
          : h(
              NSpace,
              { size: 4 },
              {
                default: () => [
                  h(
                    NButton,
                    { size: 'tiny', onClick: () => openEditor() },
                    { default: () => '编辑全部' },
                  ),
                  h(
                    NButton,
                    {
                      size: 'tiny',
                      type: 'error',
                      text: true,
                      disabled: row.status === 'archived',
                      onClick: () => confirmArchive(row),
                    },
                    { default: () => '归档' },
                  ),
                ],
              },
            ),
    },
  ]
}

// ===== 就绪度 =====

const readinessText = computed(() => {
  const value = readiness.value
  if (!value) return '就绪度未知'
  return `连续 ${value.currentCleanStreak}/${value.requiredCleanPeriods} 期零差异`
})

const readinessTone = computed(() => (readiness.value?.ready ? 'success' : 'default'))

onMounted(async () => {
  await Promise.all([load(), loadReadiness()])
})

watch(reportKind, async () => {
  compareResult.value = null
  editing.value = false
  draftErrors.value = []
  await Promise.all([load(), loadReadiness()])
})
</script>

<template>
  <div class="agg-line-rules">
    <div class="page-header">
      <div>
        <h2>报表口径双轨对比</h2>
        <p>维护报表项的声明式科目口径，并与现有硬编码口径逐行对比。</p>
      </div>
      <NRadioGroup v-model:value="reportKind" size="small">
        <NRadioButton v-for="option in reportOptions" :key="option.value" :value="option.value">
          {{ option.label }}
        </NRadioButton>
      </NRadioGroup>
    </div>

    <!-- 顶部常驻警示：不可关闭、不随滚动消失。这是本页最重要的一条文案 -->
    <NAlert type="warning" :bordered="false" class="mb-4" :title="SWITCH_NOTE" data-testid="switch-note" />

    <NCard :bordered="false" title="切换就绪度" class="mb-4">
      <NSpace align="center" :size="20" wrap>
        <NTag :type="readinessTone" size="large">{{ readinessText }}</NTag>
        <NText depth="2" style="font-size: 13px">
          已比对 {{ readiness?.checkedRuns ?? 0 }} 期
        </NText>
        <NText depth="3" style="font-size: 12px">
          {{
            readiness?.ready
              ? '已达标，但切换仍需显式操作（本页不提供切换入口）。'
              : '未达标：对外报表口径保持不变。'
          }}
        </NText>
      </NSpace>
      <NText v-if="readiness?.latest" depth="3" style="font-size: 12px">
        最近一次：{{ readiness.latest.period }} ·
        {{ readiness.latest.skipped ? '未做对比（未配规则）' : readiness.latest.clean ? '零差异' : `${readiness.latest.diffCount} 行差异` }}
      </NText>
    </NCard>

    <NCard :bordered="false" title="口径规则" class="mb-4">
      <template #header-extra>
        <NSpace align="center" :size="12">
          <NText depth="3" style="font-size: 12px">
            共 {{ rules.length }} 条（含已归档 {{ archivedCount }} 条）
          </NText>
          <NSwitch v-model:value="includeArchived" size="small">
            <template #checked>含已归档</template>
            <template #unchecked>仅启用</template>
          </NSwitch>
          <NButton size="small" type="primary" @click="openEditor">编辑规则</NButton>
        </NSpace>
      </template>

      <NAlert type="info" :bordered="false" class="mb-4">
        规则保存是<strong>整批替换</strong>（后端先删除再重建），任何一行不合法都会整批回滚；
        派生行由基础行计算得出，不允许配置化。列表含已归档规则，编辑草稿也会一并提交，保持归档状态不变。
      </NAlert>

      <NDataTable
        :columns="ruleColumns()"
        :data="visibleRules"
        :loading="loading"
        :row-key="(row: ReportLineRule) => row.id"
        size="small"
      />
      <NText v-if="!loading && !visibleRules.length" depth="3" style="font-size: 12px">
        当前报表下暂无{{ includeArchived ? '' : '启用中的' }}口径规则。
      </NText>
    </NCard>

    <NCard :bordered="false" title="双轨对比">
      <NSpace align="center" wrap :size="12">
        <NRadioGroup v-model:value="comparePeriodType" size="small">
          <NRadioButton value="month">按月</NRadioButton>
          <NRadioButton value="quarter">按季</NRadioButton>
        </NRadioGroup>
        <NDatePicker
          v-if="comparePeriodType === 'month'"
          :value="compareMonthValue"
          type="month"
          value-format="yyyy-MM"
          style="width: 160px"
          @update:value="onCompareMonthUpdate"
        />
        <NInput
          v-else
          v-model:value="compareQuarter"
          placeholder="YYYY-Qn，如 2026-Q1"
          style="width: 180px"
        />
        <NButton type="primary" :disabled="!canCompare" :loading="comparing" @click="runCompare">
          运行双轨对比（会记入审计）
        </NButton>
      </NSpace>

      <template v-if="compareResult">
        <NAlert
          class="mt-4"
          :bordered="false"
          :type="compareAlert(compareResult).type"
          :title="compareAlert(compareResult).text"
        />

        <NSpace class="mt-4" :size="20" wrap align="center">
          <NText depth="2" style="font-size: 13px">
            本次运行后：连续 {{ compareResult.switchReadiness?.currentCleanStreak ?? 0 }}/{{
              compareResult.switchReadiness?.requiredCleanPeriods ?? 0
            }} 期零差异
          </NText>
          <NText depth="3" style="font-size: 12px">
            对比期间 {{ compareResult.period }}（{{ compareResult.periodType === 'quarter' ? '季报' : '月报' }}）
          </NText>
        </NSpace>

        <div v-if="compareResult.diffs.length" class="table-wrap mt-4">
          <NTable size="small">
            <thead>
              <tr>
                <th>报表项</th>
                <th>名称</th>
                <th class="amount">现有口径</th>
                <th class="amount">配置口径</th>
                <th class="amount">差异</th>
                <th>标记</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="diff in compareResult.diffs" :key="diff.lineCode">
                <td>{{ diff.lineCode }}</td>
                <td>{{ diff.lineName || DERIVED_LINE_LABELS[diff.lineCode] || '-' }}</td>
                <td class="amount">{{ formatMoney(diff.baseline) }}</td>
                <td class="amount">{{ formatMoney(diff.configured) }}</td>
                <td class="amount">
                  <NTag size="small" :type="deltaTagType(diff.delta)">
                    {{ diff.delta > 0 ? '+' : '' }}{{ formatMoney(diff.delta) }}
                  </NTag>
                </td>
                <td>
                  <NTag v-if="diff.orphan" size="small" type="warning">配置了但现有报表没有此行</NTag>
                  <NTag v-else-if="!diff.hasRule" size="small" type="info">现有报表有此行但未配置规则</NTag>
                  <NText v-else depth="3" style="font-size: 12px">口径不一致</NText>
                </td>
              </tr>
            </tbody>
          </NTable>
        </div>
      </template>
    </NCard>

    <!-- 规则编辑：整批草稿，一次性保存 -->
    <NModal v-model:show="editing" preset="card" title="编辑口径规则" style="width: min(980px, 96vw)">
      <NAlert type="warning" :bordered="false" class="mb-4">
        保存会<strong>整批替换</strong>{{ reportKind === 'profit' ? '利润表' : '资产负债表' }}的全部规则（含已归档行，其归档状态保持不变）。
        任一行不合法都会整批回滚，因此下方会一次性列出所有问题。
      </NAlert>

      <NAlert v-if="draftErrors.length" type="error" :bordered="false" class="mb-4" title="保存前校验未通过">
        <ul class="error-list">
          <li v-for="(error, index) in draftErrors" :key="index">{{ error }}</li>
        </ul>
      </NAlert>

      <NAlert v-if="editingDerivedHit" type="error" :bordered="false" class="mb-4">
        存在派生行编码：派生行由基础行计算得出，不能配置规则。
      </NAlert>

      <NSpace vertical :size="12">
        <NCard
          v-for="(row, index) in draft"
          :key="row.key"
          size="small"
          :title="`规则 ${index + 1}`"
          :bordered="true"
        >
          <template #header-extra>
            <NButton size="tiny" type="error" text @click="removeRule(row.key)">移除</NButton>
          </template>

          <NSpace vertical :size="10">
            <NSpace align="center" :size="10" wrap>
              <NText style="width: 96px; font-size: 13px">报表项编码</NText>
              <NInput v-model:value="row.lineCode" placeholder="如 rd_expense" style="width: 220px" />
              <NTag v-if="isDerived(reportKind, row.lineCode)" size="small" type="info">派生</NTag>
              <NText
                v-if="row.lineCode.trim() && isDerived(reportKind, row.lineCode)"
                depth="1"
                style="font-size: 12px"
              >
                该行是派生行，不能配置规则
              </NText>
            </NSpace>

            <NSpace align="center" :size="10" wrap>
              <NText style="width: 96px; font-size: 13px">报表项名称</NText>
              <NInput v-model:value="row.lineName" placeholder="选填" style="width: 220px" />
            </NSpace>

            <NSpace align="center" :size="10" wrap>
              <NText style="width: 96px; font-size: 13px">方向语义</NText>
              <NSelect v-model:value="row.sign" :options="signOptions" style="width: 220px" />
              <NText depth="3" style="font-size: 12px">{{ signTooltip(row.sign) }}</NText>
            </NSpace>

            <NSpace align="start" :size="10" wrap>
              <NText style="width: 96px; font-size: 13px; padding-top: 4px">科目组合</NText>
              <NSpace vertical :size="8">
                <NSpace v-for="pattern in row.patterns" :key="pattern.key" align="center" :size="8">
                  <NSelect v-model:value="pattern.mode" :options="patternModeOptions" style="width: 130px" />
                  <NInput
                    v-model:value="pattern.value"
                    placeholder="prefix 至少 4 位，如 4301；区间形如 560101-560199"
                    style="width: 320px"
                    :status="patternError(pattern.mode, pattern.value) ? 'error' : undefined"
                  />
                  <NSelect
                    v-model:value="pattern.sign"
                    :options="[
                      { label: '+1 计入', value: 1 },
                      { label: '−1 减项', value: -1 },
                    ]"
                    style="width: 120px"
                  />
                  <NButton size="tiny" type="error" text @click="removePattern(row.key, pattern.key)">
                    移除
                  </NButton>
                  <NText
                    v-if="patternError(pattern.mode, pattern.value)"
                    depth="1"
                    style="font-size: 12px"
                  >
                    {{ patternError(pattern.mode, pattern.value) }}
                  </NText>
                </NSpace>
                <NSpace align="center" :size="12">
                  <NButton size="small" @click="addPattern(row.key)">添加科目匹配</NButton>
                  <NText depth="2" style="font-size: 13px">
                    表达式：{{ row.lineName || row.lineCode || '本行' }} = {{ patternExpression(row.patterns) }}
                  </NText>
                </NSpace>
              </NSpace>
            </NSpace>

            <NSpace align="center" :size="10" wrap>
              <NText style="width: 96px; font-size: 13px">生效期</NText>
              <NDatePicker
                :value="draftFromValue(row.key, 'effectiveFrom')"
                type="month"
                value-format="yyyy-MM"
                style="width: 150px"
                @update:value="(v: any) => onDraftDateUpdate(row.key, 'effectiveFrom', v)"
              />
              <NText depth="3" style="font-size: 12px">至</NText>
              <NDatePicker
                :value="draftFromValue(row.key, 'effectiveTo')"
                type="month"
                value-format="yyyy-MM"
                clearable
                style="width: 150px"
                @update:value="(v: any) => onDraftDateUpdate(row.key, 'effectiveTo', v)"
              />
            </NSpace>
          </NSpace>
        </NCard>

        <NButton dashed block @click="addRule">添加规则</NButton>
      </NSpace>

      <template #footer>
        <NSpace justify="end">
          <NButton @click="editing = false">取消</NButton>
          <NButton type="primary" :loading="saving" @click="saveRules">保存规则</NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.agg-line-rules { max-width: 1320px; margin: 0 auto; }
.page-header { display: flex; justify-content: space-between; gap: 20px; align-items: flex-start; margin-bottom: 20px; }
.page-header h2 { margin: 0; font-size: 22px; }
.page-header p { margin: 5px 0 0; color: var(--solo-text-secondary); font-size: 13px; }
.mb-4 { margin-bottom: 16px; }
.mt-4 { margin-top: 16px; }
.error-list { margin: 0; padding-left: 18px; font-size: 13px; line-height: 1.8; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { padding: 10px; border-bottom: 1px solid var(--solo-border-light); text-align: left; white-space: nowrap; }
th { color: var(--solo-text-secondary); font-weight: 600; }
.amount { text-align: right; font-variant-numeric: tabular-nums; }
@media (max-width: 900px) {
  .page-header { flex-direction: column; }
}
</style>