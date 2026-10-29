<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
/**
 * T03 · 通用归集 —— 规则配置 + 执行规则入口。
 *
 * 两条不可让步的设计约束（违反即数据丢失）：
 *
 * 1. **规则保存是整批 PUT**（后端 replace_rules: 先 delete 该维度全部规则再重建，
 *    `aggregation_service.py:459-462`）。所以本页任何增删改**只改本地草稿 draft**，
 *    只有点「保存规则」才 `replaceAggregationRules(dimensionId, draft)` 全量提交。
 *    前端**不存在**逐条增删改的 API 调用——后端也没提供。
 * 2. **保存前的本地校验必须汇总**。后端是整批 delete + 重建，一行错则整批回滚，
 *    用户的编辑会全部丢失。因此错误一次性列在 NAlert 里，不逐个弹窗。
 *
 * 规则匹配语义（`aggregation_service.apply_rules:1006-1009`）如实告知用户：
 *   - 绝不覆盖 source='manual' 的人工分配，已有人工分配的行**整行跳过**；
 *   - 同一行只由 priority **最小**的第一条命中规则分配；
 *   - rate < 1 产生的尾差不自动补齐，该行停在「部分归集」等人工处理。
 */
import { computed, h, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NDatePicker,
  NDivider,
  NForm,
  NFormItem,
  NGi,
  NGrid,
  NInput,
  NInputNumber,
  NModal,
  NPopconfirm,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSpace,
  NSpin,
  NStatistic,
  NTag,
  NTooltip,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns, SelectOption } from 'naive-ui'

import { useAggregationStore } from '@/stores/aggregation'
import DimensionPicker from './_components/DimensionPicker.vue'
import {
  applyRules,
  listAggregationRules,
  listMembers,
  replaceAggregationRules,
} from '@/api/aggregation'
import type { ApplyRulesResult } from '@/api/aggregation'
import type {
  AggregationBasis,
  AggregationMember,
  AggregationRule,
  AggregationRuleInput,
  LabelOption,
} from '@/api/types'
import { directionLabel, errorText } from '@/utils/aggregation'
import { SoloEmpty } from '@/components/ui'

/** 草稿行 key 的自增序号。必须早于任何 createDraft() 调用声明（TDZ） */
let draftSeq = 0

const store = useAggregationStore()
const router = useRouter()
const route = useRoute()
const message = useMessage()

const loading = ref(false)
const saving = ref(false)
const applying = ref(false)

/** 服务端已保存的规则（只读基线，用于「有未保存修改」提示） */
const savedRules = ref<AggregationRule[]>([])
/** 本地草稿。所有增删改只动它，保存成功后才同步 savedRules */
const draft = ref<RuleDraft[]>([])
/** 当前维度的归集对象，供规则表的「归集对象」列与编辑表单的 NSelect 使用 */
const members = ref<AggregationMember[]>([])

/** 本地校验结果（一次性汇总展示，不逐个弹窗） */
const validationIssues = ref<string[]>([])
/** 整批保存的二次确认 */
const showSaveConfirm = ref(false)

/** 规则编辑弹层 */
const showEditModal = ref(false)
const editingIndex = ref(-1)
const editing = ref<RuleDraft>(createDraft())

/** 执行规则弹层 */
const showApplyModal = ref(false)
const applyStart = ref<string | null>(null)
const applyEnd = ref<string | null>(null)
const applyBasis = ref<AggregationBasis>('period')
const applyResult = ref<ApplyRulesResult | null>(null)

/**
 * naive-ui 的 SelectOption 是个含 SelectIgnoredOption 的联合类型，
 * 直接把 LabelOption[] 传进 :options 会因为缺少 `type` 判别位而报 TS2322。
 * 这里在**页面边界**做一次收敛，不改共享的 LabelOption 类型（其它页面同样受益于该类型）。
 */
function toSelectOptions(options: LabelOption[]): SelectOption[] {
  return options.map((option) => ({ label: option.label, value: option.value }))
}

/** 规则的合法方向。空表示不限；中文标签统一走 utils/aggregation.directionLabel（与三态标签同一处） */
const DIRECTION_OPTIONS: SelectOption[] = [
  { value: '', label: '不限' },
  { value: 'debit', label: `${directionLabel('debit')}方` },
  { value: 'credit', label: `${directionLabel('credit')}方` },
]

/**
 * NDatePicker 的 monthrange 在 value-format='yyyy-MM' 时运行时给 string[]，
 * 但它的 Value 类型声明只认 [number, number]。用 applyPeriod 做一次边界收敛，
 * 避免在模板里写类型断言。
 */
const applyPeriod = computed<any>(() =>
  applyStart.value && applyEnd.value ? [applyStart.value, applyEnd.value] : null,
)

/** 期间选择的唯一写入口：把 [start, end] 拆回两个 ref */
function onPeriodUpdate(value: [string | null, string | null] | null): void {
  applyStart.value = value?.[0] ?? null
  applyEnd.value = value?.[1] ?? null
}

const PRIORITY_TOOLTIP = '数字越小越优先；同一凭证行只由第一条命中的规则分配。'

/**
 * 规则草稿行。
 * 用独立的可编辑形态（空串而非 null）承载表单，提交时才转成 AggregationRuleInput。
 */
interface RuleDraft {
  key: string
  memberId: string
  priority: number
  accountPrefix: string
  direction: '' | 'debit' | 'credit'
  keyword: string
  counterparty: string
  rate: number
  method: string
  basis: string
}

/** 新建一条空规则。分摊方法默认取 meta 的第一项（meta 未加载时留空，保存时会被校验拦住） */
function createDraft(): RuleDraft {
  draftSeq += 1
  return {
    key: `draft_${Date.now()}_${draftSeq}`,
    memberId: '',
    priority: 100,
    accountPrefix: '',
    direction: '',
    keyword: '',
    counterparty: '',
    rate: 1,
    method: store.methodOptions[0]?.value ?? '',
    basis: '',
  }
}

/** 草稿行 → 后端入参。⚠️ 入参**没有 status 字段**（后端强制写 active） */
function toInput(row: RuleDraft): AggregationRuleInput {
  return {
    memberId: row.memberId.trim(),
    priority: Number(row.priority) || 0,
    accountPrefix: row.accountPrefix.trim() || null,
    direction: row.direction || null,
    keyword: row.keyword.trim() || null,
    counterparty: row.counterparty.trim() || null,
    rate: row.rate,
    method: row.method ? (row.method as AggregationRuleInput['method']) : undefined,
    basis: row.basis.trim() || null,
  }
}

/** 服务端规则 → 草稿行 */
function toDraft(rule: AggregationRule): RuleDraft {
  draftSeq += 1
  return {
    key: `srv_${rule.id}_${draftSeq}`,
    memberId: rule.memberId,
    priority: rule.priority,
    accountPrefix: rule.accountPrefix ?? '',
    direction: (rule.direction === 'debit' || rule.direction === 'credit' ? rule.direction : ''),
    keyword: rule.keyword ?? '',
    counterparty: rule.counterparty ?? '',
    rate: rule.rate,
    method: rule.method,
    basis: rule.basis ?? '',
  }
}


/** 分摊方法选项**全部来自 GET /meta**，前端不硬编码中文或选项数组 */
const methodOptions = computed<SelectOption[]>(() => toSelectOptions(store.methodOptions))

const memberOptions = computed<SelectOption[]>(() =>
  toSelectOptions(members.value.map((m) => ({ value: m.id, label: `${m.code} · ${m.name}` }))),
)

const memberName = (memberId: string): string => {
  const found = members.value.find((m) => m.id === memberId)
  return found ? `${found.code} · ${found.name}` : memberId || '-'
}

const hasRules = computed(() => savedRules.value.length > 0)
const isDirty = computed(() => JSON.stringify(draft.value.map(toInput)) !== JSON.stringify(savedRules.value.map(toRuleInput)))

/** 服务端规则 → 用于比较的入参形态（与 toInput 保持同构，否则 isDirty 恒为 true） */
function toRuleInput(rule: AggregationRule): AggregationRuleInput {
  return {
    memberId: rule.memberId,
    priority: rule.priority,
    accountPrefix: rule.accountPrefix ?? null,
    direction: (rule.direction === 'debit' || rule.direction === 'credit' ? rule.direction : null),
    keyword: rule.keyword ?? null,
    counterparty: rule.counterparty ?? null,
    rate: rule.rate,
    method: rule.method as AggregationRuleInput['method'],
    basis: rule.basis ?? null,
  }
}

/** 规则数 = 0 → 顶部红条引导（与 pending 页的引导条呼应） */
/**
 * 规则数 = 0 时的红条引导（与 pending 页引导条呼应）。
 * 维度存在（store 已 bootstrap）但一条规则都没有时才提示——
 * 「没建维度」是另一种空态，由上面那条 warning 负责。
 */
const showEmptyGuide = computed(
  () => !loading.value && store.hasDimensions && !hasRules.value,
)

// ===== 数据加载 =====

async function loadRules(): Promise<void> {
  const dimensionId = store.currentDimensionId
  if (!dimensionId) {
    savedRules.value = []
    draft.value = []
    members.value = []
    return
  }
  loading.value = true
  try {
    const [ruleList, memberList] = await Promise.all([
      listAggregationRules(dimensionId),
      listMembers(dimensionId),
    ])
    savedRules.value = ruleList
    draft.value = ruleList.map(toDraft)
    members.value = memberList
  } catch (err) {
    message.error(errorText(err))
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  try {
    await store.bootstrap()
  } catch (err) {
    message.error(errorText(err))
    return
  }
  // 从「执行规则」结果卡跳回来时，把期间带进执行弹层
  const qStart = typeof route.query.start === 'string' ? route.query.start : ''
  const qEnd = typeof route.query.end === 'string' ? route.query.end : ''
  if (qStart && qEnd) {
    applyStart.value = qStart
    applyEnd.value = qEnd
    showApplyModal.value = true
  }
  await loadRules()
})

watch(() => store.currentDimensionId, () => {
  validationIssues.value = []
  applyResult.value = null
  loadRules()
})

// ===== 本地校验（逐条镜像后端 replace_rules:466-477）=====

/**
 * 校验整批草稿，返回**全部**问题（一次性汇总）。
 * 镜像后端：
 *   - memberId 必须能命中归集对象（空 → INVALID_ALLOCATION_MEMBER）
 *   - direction ∈ {debit, credit, 空}
 *   - method ∈ ALLOCATION_METHODS（前端从 GET /meta 拿，不硬编码）
 *   - rate > 0 且 ≤ 1
 */
function validateDraft(rows: RuleDraft[]): string[] {
  const issues: string[] = []
  const methodValues = new Set(store.methodOptions.map((o) => o.value))
  rows.forEach((row, index) => {
    const at = `第 ${index + 1} 条规则`
    if (!row.memberId.trim()) {
      issues.push(`${at}：未选择归集对象`)
    } else if (members.value.length && !memberOptions.value.some((o) => o.value === row.memberId)) {
      issues.push(`${at}：归集对象不存在于当前维度`)
    }
    if (row.direction && !DIRECTION_OPTIONS.some((o) => o.value === row.direction)) {
      issues.push(`${at}：方向只能是借方或贷方`)
    }
    if (!row.method || !methodValues.has(row.method)) {
      issues.push(`${at}：请选择分摊方法`)
    }
    const rate = Number(row.rate)
    if (!Number.isFinite(rate) || rate <= 0 || rate > 1) {
      issues.push(`${at}：比例必须大于 0 且不超过 100%（当前 ${row.rate}）`)
    }
    if (!Number.isFinite(Number(row.priority))) {
      issues.push(`${at}：优先级必须是数字`)
    }
  })
  return issues
}

// ===== 草稿增删改（只改本地）=====

/**
 * 新增规则：先插入一条空草稿并**直接打开编辑弹层**。
 * 空草稿必定校验不通过（memberId 为空），所以必须让用户立刻填，而不是留在表里等报错。
 */
function addRule(): void {
  const row = createDraft()
  draft.value = [...draft.value, row]
  editingIndex.value = draft.value.length - 1
  editing.value = { ...row }
  showEditModal.value = true
}

function removeRule(index: number): void {
  draft.value = draft.value.filter((_, i) => i !== index)
}

function openEdit(index: number): void {
  editingIndex.value = index
  editing.value = { ...draft.value[index] }
  showEditModal.value = true
}

function confirmEdit(): void {
  const next = { ...editing.value }
  if (editingIndex.value < 0) draft.value = [...draft.value, next]
  else draft.value = draft.value.map((row, i) => (i === editingIndex.value ? next : row))
  showEditModal.value = false
}

/** 放弃未保存的修改 */
function discardDraft(): void {
  draft.value = savedRules.value.map(toDraft)
  validationIssues.value = []
}

/** 排序用：priority 数字升序 = 优先 */
function sortedDraft(): RuleDraft[] {
  return [...draft.value].sort((a, b) => Number(a.priority) - Number(b.priority))
}

// ===== 保存（整批 PUT）=====

function requestSave(): void {
  validationIssues.value = validateDraft(draft.value)
  if (validationIssues.value.length) return
  showSaveConfirm.value = true
}

async function confirmSave(): Promise<void> {
  const dimensionId = store.currentDimensionId
  if (!dimensionId) return
  saving.value = true
  try {
    // ⚠️ 整批替换：漏传的规则会被后端删除，不是「不动」
    const result = await replaceAggregationRules(dimensionId, draft.value.map(toInput))
    savedRules.value = result
    draft.value = result.map(toDraft)
    showSaveConfirm.value = false
    message.success(`已保存 ${result.length} 条规则。规则只影响后续「执行规则」，不会改动已有归集。`)
  } catch (err) {
    // 保存失败**不清空草稿**，让用户改完再试（后端整批回滚，库里的旧规则没丢）
    message.error(errorText(err))
  } finally {
    saving.value = false
  }
}

// ===== 执行规则 =====

function openApply(): void {
  applyResult.value = null
  if (!applyStart.value) applyStart.value = defaultMonth(-1)
  if (!applyEnd.value) applyEnd.value = defaultMonth(0)
  showApplyModal.value = true
}

/** 默认期间：上个月 ~ 本月（YYYY-MM） */
function defaultMonth(offset: number): string {
  const now = new Date()
  const d = new Date(now.getFullYear(), now.getMonth() + offset, 1)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`
}

const canApply = computed(() => Boolean(applyStart.value && applyEnd.value && store.currentDimensionId))

async function runApply(): Promise<void> {
  const dimensionId = store.currentDimensionId
  if (!dimensionId || !applyStart.value || !applyEnd.value) return
  applying.value = true
  try {
    // ⚠️ scopePrefixes 在此端点是**字符串数组**，与 GET /pending 的逗号字符串不同形。
    //    留空则后端用 DEFAULT_SCOPE_PREFIXES('5','6')。
    applyResult.value = await applyRules({
      dimensionId,
      start: applyStart.value,
      end: applyEnd.value,
      basis: applyBasis.value,
    })
    message.success('规则执行完成')
  } catch (err) {
    message.error(errorText(err))
  } finally {
    applying.value = false
  }
}

/** 结果卡上的「去配置规则」：跳本页并把期间带进 query，便于直接重跑 */
function goConfigureRules(): void {
  router.push({
    path: '/aggregation/rules',
    query: { start: applyStart.value ?? '', end: applyEnd.value ?? '' },
  })
}

// ===== 规则表列 =====

const columns = computed<DataTableColumns<RuleDraft>>(() => [
  {
    title: () =>
      h(NTooltip, { trigger: 'hover' }, {
        trigger: () =>
          h('span', { class: 'col-with-hint' }, ['优先级', h('span', { class: 'hint-mark' }, '?')]),
        default: () => PRIORITY_TOOLTIP,
      }),
    key: 'priority',
    width: 92,
    sorter: 'default',
    render: (row) => h('span', { class: 'num-cell' }, String(row.priority ?? 0)),
  },
  {
    title: '归集对象',
    key: 'memberId',
    width: 180,
    ellipsis: { tooltip: true },
    render: (row) => memberName(row.memberId),
  },
  { title: '科目前缀', key: 'accountPrefix', width: 110, render: (row) => row.accountPrefix || '-' },
  {
    title: '方向',
    key: 'direction',
    width: 76,
    render: (row) => (row.direction ? directionLabel(row.direction) : '不限'),
  },
  {
    title: '关键词',
    key: 'keyword',
    width: 130,
    ellipsis: { tooltip: true },
    render: (row) => row.keyword || '-',
  },
  {
    // counterparty 是后端 _rule_matches 的第 4 条命中条件，必须可编辑
    title: '对方单位',
    key: 'counterparty',
    width: 150,
    ellipsis: { tooltip: true },
    render: (row) => row.counterparty || '-',
  },
  {
    title: '比例',
    key: 'rate',
    width: 92,
    render: (row) => {
      const rate = Number(row.rate)
      const cells = [h('span', { class: 'num-cell' }, `${Math.round(rate * 10000) / 100}%`)]
      if (rate < 1) {
        cells.push(h(NTag, { size: 'small', type: 'warning', bordered: false }, {
          default: () => '尾差不自动补齐，该行将停在「部分归集」',
        }))
      }
      return h(NSpace, { size: 4, vertical: true }, { default: () => cells })
    },
  },
  {
    title: '分摊方法',
    key: 'method',
    width: 110,
    render: (row) => store.labelOf('method', row.method),
  },
  {
    title: '操作',
    key: 'actions',
    width: 118,
    render: (_row, index) => h(NSpace, { size: 4, wrap: false }, {
      default: () => [
        h(NButton, {
          size: 'tiny',
          quaternary: true,
          'data-testid': `edit-rule-${index}`,
          onClick: () => openEdit(index),
        }, { default: () => '编辑' }),
        h(NPopconfirm, {
          onPositiveClick: () => removeRule(index),
          positiveText: '移除',
          negativeText: '取消',
        }, {
          trigger: () => h(NButton, {
            size: 'tiny',
            quaternary: true,
            type: 'error',
            'data-testid': `remove-rule-${index}`,
          }, { default: () => '移除' }),
          default: () => '仅从草稿中移除，点「保存规则」后才会写入服务端。',
        }),
      ],
    }),
  },
])
</script>

<template>
  <div class="agg-rules-page">
    <div class="page-header">
      <h2>归集规则</h2>
      <NSpace :size="8" align="center">
        <NButton
          size="small"
          type="primary"
          :disabled="!store.hasDimensions || draft.length === 0"
          data-testid="open-apply"
          @click="openApply"
        >
          执行规则
        </NButton>
      </NSpace>
    </div>

    <!-- 维度选择复用 T02 的 DimensionPicker：与 dimensions.vue / index.vue 同一份组件。
         铁律「不设 clearable、无维度时禁用并提示请先建立维度」由组件内部统一保证，
         本页不重复实现，也不自己绑 store.currentDimensionId -->
    <div class="picker-row">
      <DimensionPicker />
    </div>

    <div class="tool-row">
      <NButton size="small" :loading="loading" @click="loadRules">刷新规则</NButton>
    </div>

    <NAlert v-if="!store.hasDimensions" type="warning" class="block-alert" :show-icon="true">
      <template #header>尚未建立归集维度</template>
      请先到「维度与对象」建立维度并添加归集对象，再回来配置规则。
      <div class="alert-actions">
        <NButton size="small" type="primary" @click="router.push('/aggregation/dimensions')">去建立维度</NButton>
      </div>
    </NAlert>

    <!-- 规则数 = 0：红条 + 去配置规则 CTA（与 pending 页引导条呼应） -->
    <NAlert v-if="showEmptyGuide" type="error" class="block-alert" :show-icon="true">
      <template #header>当前维度还没有任何规则</template>
      没有规则时「执行规则」不会分配任何凭证行。请先在下方新增规则，或到「待归集清单」手工分配。
      <div class="alert-actions">
        <NButton size="small" type="primary" data-testid="empty-guide-cta" @click="addRule">
          去配置规则
        </NButton>
        <NButton size="small" quaternary @click="router.push('/aggregation/pending')">
          去待归集清单
        </NButton>
      </div>
    </NAlert>

    <NAlert type="info" class="block-alert" :show-icon="true">
      <template #header>规则怎么生效</template>
      规则只影响「执行规则」，不会改动已有归集。执行时：① 已存在人工分配的行会被整行跳过，不会被覆盖；
      ② 同一凭证行只由优先级最小（数字最小）的第一条命中规则分配；③ 比例小于 100% 产生的尾差不自动补齐，该行停在「部分归集」等人工处理。
    </NAlert>

    <!-- 保存前一次性汇总全部问题（后端整批回滚，逐个弹窗会让用户丢编辑） -->
    <NAlert v-if="validationIssues.length" type="error" class="block-alert" :show-icon="true">
      <template #header>有 {{ validationIssues.length }} 处问题需要修正，草稿未提交</template>
      <ul class="issue-list">
        <li v-for="(issue, i) in validationIssues" :key="i">{{ issue }}</li>
      </ul>
    </NAlert>

    <NSpin :show="loading">
      <NCard :bordered="false" class="table-card" title="规则列表">
        <template #header-extra>
          <NSpace :size="8">
            <NTag v-if="isDirty" type="warning" size="small" bordered :false="false" data-testid="dirty-tag">
              有未保存的修改
            </NTag>
            <NButton size="small" :disabled="!store.hasDimensions" data-testid="add-rule" @click="addRule">
              新增规则
            </NButton>
            <NButton
              size="small"
              quaternary
              :disabled="!isDirty"
              data-testid="discard-draft"
              @click="discardDraft"
            >
              放弃修改
            </NButton>
            <NButton
              size="small"
              type="primary"
              :disabled="!store.hasDimensions"
              :loading="saving"
              data-testid="save-rules"
              @click="requestSave"
            >
              保存规则
            </NButton>
          </NSpace>
        </template>

        <NDataTable
          v-if="draft.length"
          :columns="columns"
          :data="sortedDraft()"
          :bordered="false"
          striped
          size="small"
          :default-sort="{ columnKey: 'priority', order: 'ascend' }"
        />
        <SoloEmpty
          v-else
          title="还没有归集规则"
          description="告诉系统哪类支出归到哪个项目，归集就能自动跑。点右上角「新增规则」开始配置。"
          icon="ri-list-settings-line"
          class="empty-box"
        />
      </NCard>
    </NSpin>

    <!-- 规则编辑弹层 -->
    <NModal
      v-model:show="showEditModal"
      preset="card"
      :title="editingIndex < 0 ? '新增规则' : '编辑规则'"
      style="width: min(820px, 96vw)"
      :mask-closable="false"
    >
      <NForm label-placement="top">
        <NGrid :x-gap="12" :cols="2">
          <NGi>
            <NFormItem label="归集对象" required>
              <NSelect
                v-model:value="editing.memberId"
                :options="memberOptions"
                filterable
                placeholder="选择归集对象"
                data-testid="rule-member"
              />
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem :show-feedback="false">
              <template #label>
                <span class="col-with-hint">
                  优先级
                  <NTooltip trigger="hover">
                    <template #trigger>
                      <span class="hint-mark">?</span>
                    </template>
                    {{ PRIORITY_TOOLTIP }}
                  </NTooltip>
                </span>
              </template>
              <NInputNumber
                v-model:value="editing.priority"
                :min="0"
                :show-button="false"
                style="width: 100%"
                data-testid="rule-priority"
              />
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem label="科目前缀" :show-feedback="false">
              <NInput
                v-model:value="editing.accountPrefix"
                placeholder="如 4301，留空表示不限"
                data-testid="rule-prefix"
              />
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem label="方向" :show-feedback="false">
              <NSelect v-model:value="editing.direction" :options="DIRECTION_OPTIONS" data-testid="rule-direction" />
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem label="关键词" :show-feedback="false">
              <NInput
                v-model:value="editing.keyword"
                placeholder="匹配凭证摘要，留空表示不限"
                data-testid="rule-keyword"
              />
            </NFormItem>
          </NGi>
          <NGi>
            <!-- 对方单位是后端 _rule_matches 的第 4 条命中条件，必须给输入框 -->
            <NFormItem label="对方单位" :show-feedback="false">
              <NInput
                v-model:value="editing.counterparty"
                placeholder="匹配对方单位名称，留空表示不限"
                data-testid="rule-counterparty"
              />
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem label="比例" :show-feedback="false">
              <NInputNumber
                v-model:value="editing.rate"
                :min="0"
                :max="1"
                :step="0.05"
                :show-button="false"
                style="width: 100%"
                data-testid="rule-rate"
              />
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem label="分摊方法" :show-feedback="false">
              <NSelect
                v-model:value="editing.method"
                 :options="methodOptions"
                placeholder="选择分摊方法"
                data-testid="rule-method"
              />
            </NFormItem>
          </NGi>
          <NGi :span="2">
            <NFormItem label="分摊依据" :show-feedback="false">
              <NInput
                v-model:value="editing.basis"
                type="textarea"
                :rows="2"
                placeholder="说明按什么口径分摊，留空表示不记录"
                data-testid="rule-basis"
              />
            </NFormItem>
          </NGi>
        </NGrid>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showEditModal = false">取消</NButton>
          <NButton type="primary" data-testid="confirm-edit" @click="confirmEdit">确定</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 整批保存二次确认 -->
    <NModal
      v-model:show="showSaveConfirm"
      preset="card"
      title="确认整批保存"
      style="width: min(520px, 96vw)"
      :mask-closable="false"
    >
      <NAlert type="warning" :show-icon="true">
        整批保存会覆盖该维度下全部规则（当前 {{ savedRules.length }} 条 → 保存后 {{ draft.length }} 条）。本次未包含的规则将被删除，且不可撤销。
      </NAlert>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showSaveConfirm = false">取消</NButton>
          <NButton type="primary" :loading="saving" data-testid="confirm-save" @click="confirmSave">
            确认整批保存
          </NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 执行规则 -->
    <NModal
      v-model:show="showApplyModal"
      preset="card"
      title="执行规则"
      style="width: min(640px, 96vw)"
      :mask-closable="false"
    >
      <NAlert type="warning" class="block-alert" :show-icon="true">
        已存在人工分配的行会被整行跳过，不会被覆盖。比例小于 100% 的行会停在「部分归集」。
      </NAlert>

      <NForm label-placement="top">
        <NFormItem label="期间" :show-feedback="false">
          <NDatePicker
            :value="applyPeriod"
            type="monthrange"
            value-format="yyyy-MM"
            clearable
            style="width: 100%"
            data-testid="apply-period"
            @update:value="(v: unknown) => onPeriodUpdate(v as [string | null, string | null] | null)"
          />
        </NFormItem>
        <NFormItem label="归集口径" :show-feedback="false">
          <NRadioGroup v-model:value="applyBasis" data-testid="apply-basis">
            <NRadioButton value="period">按期间</NRadioButton>
            <NRadioButton value="occurDate">按业务日期</NRadioButton>
          </NRadioGroup>
        </NFormItem>
      </NForm>

      <template v-if="applyResult">
        <NDivider style="margin: 4px 0 12px" />
        <NGrid :x-gap="12" :cols="4" cols-sm="2">
          <NGi>
            <NStatistic label="已分配" :value="applyResult.allocated" data-testid="stat-allocated" />
          </NGi>
          <NGi>
            <NStatistic label="已跳过" :value="applyResult.skipped" data-testid="stat-skipped" />
          </NGi>
          <NGi>
            <NStatistic label="未匹配" :value="applyResult.unmatched" data-testid="stat-unmatched" />
          </NGi>
          <NGi>
            <NStatistic label="规则数" :value="applyResult.rules" data-testid="stat-rules" />
          </NGi>
        </NGrid>
        <NAlert v-if="applyResult.unmatched > 0" type="info" class="block-alert" :show-icon="true">
          有 {{ applyResult.unmatched }} 行没有命中任何规则。可补充关键词、对方单位或科目前缀后重跑。
          <div class="alert-actions">
            <NButton size="small" type="primary" data-testid="go-configure" @click="goConfigureRules">
              去配置规则
            </NButton>
          </div>
        </NAlert>
      </template>

      <template #footer>
        <NSpace justify="end">
          <NButton @click="showApplyModal = false">关闭</NButton>
          <NButton
            type="primary"
            :loading="applying"
            :disabled="!canApply"
            data-testid="run-apply"
            @click="runApply"
          >
            执行规则
          </NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.agg-rules-page {
  max-width: 1120px;
  margin: 0 auto;
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.picker-row {
  margin-bottom: 12px;
}
.tool-row {
  margin-bottom: 12px;
}
.block-alert {
  margin-bottom: 12px;
}
.alert-actions {
  margin-top: 8px;
}
.table-card {
  margin-top: 4px;
}
.empty-box {
  padding: 24px 0;
}
.issue-list {
  margin: 4px 0 0;
  padding-left: 20px;
  font-size: 13px;
  line-height: 1.7;
}
.num-cell {
  font-variant-numeric: tabular-nums;
}
.col-with-hint {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.hint-mark {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 14px;
  height: 14px;
  border-radius: 50%;
  border: 1px solid var(--solo-border-light);
  color: var(--solo-text-muted);
  font-size: 11px;
  cursor: help;
}
</style>
