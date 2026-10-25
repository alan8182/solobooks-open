<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, h, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NDrawer,
  NDrawerContent,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NRadioButton,
  NRadioGroup,
  NSelect,
  NSpace,
  NSpin,
  NSwitch,
  NTabPane,
  NTabs,
  NTag,
  NTooltip,
  useDialog,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import {
  applyTaxSchedule,
  buildTaxRegistrationTemplate,
  createTaxRegistration,
  deactivateTaxRegistration,
  diffTaxSchedule,
  importTaxRegistrations,
  listTaxRegistrations,
  previewTaxSchedule,
  recognizeTaxRegistration,
  updateTaxRegistration,
  type FilingCycle,
  type ScheduleDiff,
  type SchedulePreview,
  type TaxRegistrationRow,
} from '@/api/tax-registration'
import { SoloEmpty } from '@/components/ui'
import {
  TAX_REGISTRATION_COLUMNS,
  cycleLabel,
  deadlineStatusText,
  deadlineStatusType,
  formatRate,
  formatTaxRate,
  monthLabel,
  parsePastedTable,
  periodLabel,
  summarizeImport,
} from '@/utils/taxRegistration'

const router = useRouter()
const message = useMessage()
const dialog = useDialog()

const loading = ref(false)
const saving = ref(false)
const year = ref(new Date().getFullYear())
const rows = ref<TaxRegistrationRow[]>([])
const stats = ref<{ total: number; active: number; groups: Record<string, number> }>({
  total: 0,
  active: 0,
  groups: {},
})
const preview = ref<SchedulePreview | null>(null)
const diff = ref<ScheduleDiff | null>(null)
const showInactive = ref(false)
const previewMode = ref<'dueMonth' | 'period'>('dueMonth')

const yearOptions = computed(() => {
  const current = new Date().getFullYear()
  return [current - 1, current, current + 1].map((value) => ({ label: `${value} 年`, value }))
})

const cycleOptions = [
  { label: '月（按月申报）', value: 'month' },
  { label: '季（按季申报）', value: 'quarter' },
  { label: '年（按年申报）', value: 'year' },
  { label: '按次', value: 'other' },
]

const mainFlagOptions = [
  { label: '主税', value: 'main' },
  { label: '增值税附税', value: 'vat_attachment' },
]

const collectionOptions = [
  { label: '自行申报', value: 'self' },
  { label: '代扣代缴', value: 'withholding' },
]

async function load() {
  loading.value = true
  try {
    const [list, schedule, diffResult] = await Promise.all([
      listTaxRegistrations(showInactive.value),
      previewTaxSchedule(year.value),
      diffTaxSchedule(year.value),
    ])
    rows.value = list.items
    stats.value = list.stats
    preview.value = schedule
    diff.value = diffResult
  } catch (error) {
    message.error(`加载税费种认定失败：${(error as Error).message}`)
  } finally {
    loading.value = false
  }
}

watch(showInactive, () => {
  void load()
})
watch(year, () => {
  void load()
})

onMounted(() => {
  void load()
})

// ── 明细表 ────────────────────────────────────────────────────────────────
const displayRows = computed(() =>
  showInactive.value ? rows.value : rows.value.filter((row) => row.status === 'active'),
)

const columns = computed<DataTableColumns<TaxRegistrationRow>>(() => [
  { title: '序号', key: 'seq', width: 58 },
  {
    title: '征收项目',
    key: 'levyItem',
    width: 150,
    ellipsis: { tooltip: true },
  },
  {
    title: '征收品目',
    key: 'levyCategory',
    width: 180,
    ellipsis: { tooltip: true },
    render: (row) => row.levyCategory || '—',
  },
  {
    title: '征收子目',
    key: 'levySubItem',
    width: 150,
    ellipsis: { tooltip: true },
    render: (row) => row.levySubItem || '—',
  },
  {
    title: '纳税期限',
    key: 'filingCycle',
    width: 88,
    render: (row) => cycleLabel(row.filingCycle),
  },
  {
    title: '申报期限',
    key: 'deadlineText',
    width: 170,
    ellipsis: { tooltip: true },
    render: (row) => row.deadlineText || row.deadlineRuleLabel,
  },
  {
    title: '征收率',
    key: 'levyRate',
    width: 84,
    render: (row) => formatRate(row.levyRate),
  },
  {
    title: '税率/单位税额',
    key: 'taxRate',
    width: 120,
    render: (row) => formatTaxRate(row),
  },
  {
    title: '主附税',
    key: 'mainFlag',
    width: 96,
    render: (row) => (row.mainFlag === 'vat_attachment' ? '增值税附税' : '主税'),
  },
  {
    title: '征收方式',
    key: 'collectionMethod',
    width: 96,
    render: (row) => (row.collectionMethod === 'withholding' ? '代扣代缴' : '自行申报'),
  },
  {
    title: '归属申报表',
    key: 'returnGroupLabel',
    width: 210,
    render: (row) =>
      h(
        NTag,
        { size: 'small', bordered: false, type: row.returnGroup === 'other' ? 'default' : 'info' },
        { default: () => row.returnGroupLabel },
      ),
  },
  {
    title: '状态',
    key: 'status',
    width: 80,
    render: (row) =>
      h(
        NTag,
        { size: 'small', bordered: false, type: row.status === 'active' ? 'success' : 'default' },
        { default: () => (row.status === 'active' ? '生效' : '已停用') },
      ),
  },
  {
    title: '操作',
    key: 'actions',
    width: 120,
    fixed: 'right',
    render: (row) =>
      h(NSpace, { size: 4 }, {
        default: () => [
          h(NButton, { size: 'tiny', text: true, type: 'primary', onClick: () => openEditor(row) }, { default: () => '编辑' }),
          row.status === 'active'
            ? h(
                NButton,
                {
                  size: 'tiny',
                  text: true,
                  type: 'error',
                  onClick: () => confirmDeactivate(row),
                },
                { default: () => '停用' },
              )
            : null,
        ],
      }),
  },
])

// ── 预览 ──────────────────────────────────────────────────────────────────
interface PreviewBucket {
  key: string
  label: string
  dueText: string
  items: Array<{ key: string; name: string; period: string; status: string }>
}

const previewBuckets = computed<PreviewBucket[]>(() => {
  if (!preview.value) return []
  if (previewMode.value === 'period') {
    return preview.value.byPeriod.map((bucket) => ({
      key: bucket.period,
      label: periodLabel(bucket.period),
      dueText: bucket.dueDate,
      items: bucket.items.map((item) => ({
        key: `${item.returnGroup}-${bucket.period}`,
        name: item.name,
        period: bucket.period,
        status: item.status,
      })),
    }))
  }
  return preview.value.byDueMonth.map((bucket) => {
    const earliest = bucket.items.reduce(
      (min, item) => (item.dueDate < min ? item.dueDate : min),
      bucket.items[0]?.dueDate ?? '',
    )
    return {
      key: bucket.month,
      label: monthLabel(bucket.month),
      dueText: earliest,
      items: bucket.items.map((item) => ({
        key: `${item.returnGroup}-${item.period}`,
        name: item.name,
        period: item.period,
        status: item.status,
      })),
    }
  })
})

function openFiling(period: string) {
  void router.push({ path: '/tax-filing', query: { period } })
}

// ── 编辑抽屉 ──────────────────────────────────────────────────────────────
const editorVisible = ref(false)
const editingId = ref<string | null>(null)
const form = ref({
  levyItem: '',
  levyCategory: '',
  levySubItem: '',
  filingCycle: 'quarter' as FilingCycle,
  deadlineText: '期满之日起15日内',
  levyRate: '',
  taxRate: '',
  mainFlag: 'main' as 'main' | 'vat_attachment',
  collectionMethod: 'self' as 'self' | 'withholding',
  note: '',
})

function resetForm() {
  form.value = {
    levyItem: '',
    levyCategory: '',
    levySubItem: '',
    filingCycle: 'quarter',
    deadlineText: '期满之日起15日内',
    levyRate: '',
    taxRate: '',
    mainFlag: 'main',
    collectionMethod: 'self',
    note: '',
  }
}

function openEditor(row?: TaxRegistrationRow) {
  if (!row) {
    editingId.value = null
    resetForm()
    editorVisible.value = true
    return
  }
  editingId.value = row.id
  form.value = {
    levyItem: row.levyItem,
    levyCategory: row.levyCategory,
    levySubItem: row.levySubItem,
    filingCycle: row.filingCycle,
    deadlineText: row.deadlineText,
    levyRate: row.levyRate === null ? '' : String(row.levyRate),
    taxRate: row.taxRate === null ? row.rateUnit : String(row.taxRate),
    mainFlag: row.mainFlag,
    collectionMethod: row.collectionMethod,
    note: row.note,
  }
  editorVisible.value = true
}

async function saveRow() {
  if (!form.value.levyItem.trim()) {
    message.warning('征收项目不能为空')
    return
  }
  saving.value = true
  try {
    const payload = {
      levyItem: form.value.levyItem,
      levyCategory: form.value.levyCategory,
      levySubItem: form.value.levySubItem,
      filingCycle: form.value.filingCycle,
      deadlineText: form.value.deadlineText,
      levyRate: form.value.levyRate || null,
      taxRate: form.value.taxRate || null,
      mainFlag: form.value.mainFlag,
      collectionMethod: form.value.collectionMethod,
      note: form.value.note,
    }
    if (editingId.value) {
      await updateTaxRegistration(editingId.value, payload)
    } else {
      await createTaxRegistration(payload)
    }
    editorVisible.value = false
    message.success('已保存，征期已按新认定重算')
    await load()
  } catch (error) {
    message.error(`保存失败：${(error as Error).message}`)
  } finally {
    saving.value = false
  }
}

function confirmDeactivate(row: TaxRegistrationRow) {
  dialog.warning({
    title: '停用这条认定',
    content: `停用「${row.levyItem}${row.levyCategory ? ` · ${row.levyCategory}` : ''}」后，该税种不会再生成新的待办；已生成的待办会标记为「不适用」，已申报记录不受影响。`,
    positiveText: '停用',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await deactivateTaxRegistration(row.id as string)
        message.success('已停用')
        await load()
      } catch (error) {
        message.error(`停用失败：${(error as Error).message}`)
      }
    },
  })
}

// ── 导入 ──────────────────────────────────────────────────────────────────
const importVisible = ref(false)
const importTab = ref<'paste' | 'ocr'>('paste')
const importMode = ref<'merge' | 'replace'>('merge')
const pasteText = ref('')
const recognizedRows = ref<TaxRegistrationRow[]>([])
const ocrLoading = ref(false)
const importing = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const parsedPaste = computed(() => parsePastedTable(pasteText.value))

const importPreviewRows = computed<Array<Record<string, string>>>(() => {
  if (importTab.value === 'paste') return parsedPaste.value.rows
  return recognizedRows.value.map((row) => ({
    征收项目: row.levyItem,
    征收品目: row.levyCategory,
    征收子目: row.levySubItem,
    纳税期限: cycleLabel(row.filingCycle),
    申报期限: row.deadlineText || row.deadlineRuleLabel,
    征收率: row.levyRate === null ? '—' : formatRate(row.levyRate),
    '税率或单位税额': formatTaxRate(row),
    主附税标志: row.mainFlag === 'vat_attachment' ? '增值税附税' : '主税',
    征收代理方式: row.collectionMethod === 'withholding' ? '代扣代缴' : '自行申报',
    归属申报表: row.returnGroupLabel,
  }))
})

function openImport() {
  importVisible.value = true
  importTab.value = 'paste'
  importMode.value = 'merge'
}

async function handleFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  ocrLoading.value = true
  try {
    const result = await recognizeTaxRegistration(file)
    recognizedRows.value = result.items
    importTab.value = 'ocr'
    if (result.errors.length) {
      message.warning(`识别到 ${result.items.length} 行，${result.errors.length} 行需手工补全`)
    } else {
      message.success(`识别到 ${result.items.length} 行，请核对小数位后导入`)
    }
  } catch (error) {
    message.error(`识别失败，请改用复制粘贴：${(error as Error).message}`)
  } finally {
    ocrLoading.value = false
    input.value = ''
  }
}

async function confirmImport() {
  const payload =
    importTab.value === 'paste' ? parsedPaste.value.rows : (recognizedRows.value as unknown as Array<Record<string, unknown>>)
  if (!payload.length) {
    message.warning('没有可导入的行')
    return
  }
  importing.value = true
  try {
    const result = await importTaxRegistrations(
      payload,
      importMode.value,
      importTab.value === 'paste' ? 'paste' : 'ocr',
    )
    importVisible.value = false
    pasteText.value = ''
    recognizedRows.value = []
    message.success(
      `${summarizeImport(result)}；已按新认定重算 ${year.value} 年征期（新增 ${result.refresh?.created ?? 0} 条待办）`,
    )
    await load()
  } catch (error) {
    message.error(`导入失败：${(error as Error).message}`)
  } finally {
    importing.value = false
  }
}

async function useTemplate() {
  try {
    const result = await buildTaxRegistrationTemplate()
    dialog.info({
      title: '按主体类型带出默认认定',
      content: `${result.notice}\n将带出 ${result.items.length} 行（${result.entityType === 'ltd' ? '有限责任公司' : '个体户 / 个人独资'}口径）。`,
      positiveText: '带出并保存',
      negativeText: '取消',
      onPositiveClick: async () => {
        const imported = await importTaxRegistrations(result.rows, 'merge', 'template')
        message.success(`已带出 ${imported.created} 行默认认定，请按税局认定表核对`)
        await load()
      },
    })
  } catch (error) {
    message.error(`带出模板失败：${(error as Error).message}`)
  }
}

async function applyNow() {
  try {
    const result = await applyTaxSchedule(year.value)
    message.success(`已按认定表重算征期：新增 ${result.created ?? 0} 条，豁免 ${result.autoWaived ?? 0} 条`)
    await load()
  } catch (error) {
    message.error(`重算失败：${(error as Error).message}`)
  }
}

// 税报页顶部那条入口用同一份数据（companies 接口不返回认定信息，这里不重复请求）
const filingEntryHint = computed(() =>
  preview.value?.source === 'registration'
    ? `已按税费种认定表生成征期：${stats.value.active} 项认定 · ${preview.value.summary.itemCount} 项应报事项`
    : '尚未同步税费种认定表，当前按内置税种清单生成征期',
)
</script>

<template>
  <div class="tax-registration-page">
    <div class="page-header">
      <div class="header-left">
        <div class="title-row">
          <h2>税费种认定</h2>
          <NTag size="small" type="primary" round :bordered="false">
            {{ preview?.source === 'registration' ? '已同步' : '未同步' }}
          </NTag>
        </div>
        <p class="header-sub">
          按电子税务局「税费种认定信息」维护；征期日历与申报清单都按这张表生成，做到「本月 / 本季该报哪些税」一目了然。
        </p>
      </div>
      <NSpace :size="8">
        <NButton size="small" secondary :loading="loading" @click="load">刷新</NButton>
        <NButton size="small" @click="useTemplate">按主体类型带出</NButton>
        <NButton size="small" type="primary" @click="openImport">从电子税务局同步</NButton>
      </NSpace>
    </div>

    <div class="stat-row">
      <div class="stat-card">
        <span class="stat-label">认定项数</span>
        <strong class="stat-value">{{ stats.active }}</strong>
        <span class="stat-hint">共 {{ stats.total }} 行（含已停用）</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">覆盖申报表</span>
        <strong class="stat-value">{{ Object.keys(stats.groups).length }}</strong>
        <span class="stat-hint">按申报表归并，不是按税种堆</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">{{ year }} 年应报事项</span>
        <strong class="stat-value">{{ preview?.summary.itemCount ?? 0 }}</strong>
        <span class="stat-hint">{{ preview?.summary.periodCount ?? 0 }} 个属期</span>
      </div>
      <div class="stat-card">
        <span class="stat-label">征期口径</span>
        <strong class="stat-value stat-value--text">
          {{ preview?.cycles?.vat === 'month' ? '月报' : '季报' }}
        </strong>
        <span class="stat-hint">增值税申报周期</span>
      </div>
    </div>

    <NAlert v-if="!stats.active" type="info" :bordered="false" class="block-alert" :show-icon="true">
      <template #header>还没有同步税局认定表</template>
      <div>
        当前征期按内置税种清单生成。建议从电子税务局「税费种认定信息」页复制整张表格粘进来，或先按主体类型带出一份默认值再逐行核对。
      </div>
      <div class="alert-actions">
        <NButton size="small" type="primary" @click="openImport">去同步</NButton>
        <NButton size="small" @click="useTemplate">按主体类型带出</NButton>
      </div>
    </NAlert>

    <NAlert
      v-if="preview?.conflicts?.length"
      type="warning"
      :bordered="false"
      class="block-alert"
      :show-icon="true"
    >
      <template #header>认定表与主体类型不一致</template>
      <div>
        <span v-for="item in preview.conflicts" :key="`${item.levyItem}-${item.levyCategory}`" class="conflict-item">
          {{ item.levyItem }}{{ item.levyCategory ? ` · ${item.levyCategory}` : '' }}
        </span>
        与当前主体类型不匹配，已跳过、不会生成待办。请核对主体类型或税局认定。
      </div>
    </NAlert>

    <NAlert v-if="diff && (diff.willCreateCount || diff.willWaiveCount || diff.willRestoreCount)" type="info" :bordered="false" class="block-alert">
      认定表与当前征期存在差异：将新增 <strong>{{ diff.willCreateCount }}</strong> 条、豁免
      <strong>{{ diff.willWaiveCount }}</strong> 条、恢复 <strong>{{ diff.willRestoreCount }}</strong> 条。
      <NButton size="tiny" type="primary" text style="margin-left: 8px" @click="applyNow">应用到征期</NButton>
    </NAlert>

    <NCard class="table-card" :bordered="false">
      <template #header>
        <div class="card-head">
          <div>
            <span class="card-title">认定明细</span>
            <span class="card-sub">{{ filingEntryHint }}</span>
          </div>
          <NSpace align="center" :size="8">
            <span class="switch-label">含已停用</span>
            <NSwitch v-model:value="showInactive" size="small" />
            <NButton size="small" @click="openEditor()">新增一行</NButton>
          </NSpace>
        </div>
      </template>
      <NDataTable
        :columns="columns"
        :data="displayRows"
        :loading="loading"
        :row-key="(row: TaxRegistrationRow) => row.id ?? `${row.levyItem}-${row.levyCategory}`"
        :scroll-x="1500"
        size="small"
        :bordered="false"
      />
      <SoloEmpty
        v-if="!loading && !displayRows.length"
        title="还没有税费种认定"
        description="照着税局的认定表抄一遍，征期提醒和申报表才会对得上。"
        icon="ri-government-line"
        class="table-empty"
      />
    </NCard>

    <NCard class="preview-card" :bordered="false">
      <template #header>
        <div class="card-head">
          <div>
            <span class="card-title">申报节奏预览</span>
            <span class="card-sub">按认定表铺出的 {{ year }} 年应报清单，点税种可跳到税务申报</span>
          </div>
          <NSpace align="center" :size="8">
            <NSelect v-model:value="year" :options="yearOptions" size="small" style="width: 110px" />
            <NRadioGroup v-model:value="previewMode" size="small">
              <NRadioButton value="dueMonth">按截止月份</NRadioButton>
              <NRadioButton value="period">按属期</NRadioButton>
            </NRadioGroup>
          </NSpace>
        </div>
      </template>

      <NSpin :show="loading">
        <div v-if="previewBuckets.length" class="timeline">
          <div v-for="bucket in previewBuckets" :key="bucket.key" class="timeline-item">
            <div class="timeline-head">
              <span class="timeline-label">{{ bucket.label }}</span>
              <span class="timeline-due">截止 {{ bucket.dueText }}</span>
            </div>
            <div class="chip-row">
              <NTooltip v-for="item in bucket.items" :key="item.key" placement="top">
                <template #trigger>
                  <button type="button" class="chip" :class="`chip--${deadlineStatusType(item.status)}`" @click="openFiling(item.period)">
                    {{ item.name }}
                  </button>
                </template>
                {{ periodLabel(item.period) }} · {{ deadlineStatusText(item.status) }}
              </NTooltip>
            </div>
          </div>
        </div>
        <SoloEmpty
          v-else
          title="还没有可预览的申报清单"
          description="同步认定表后，这里会出现每个税种的应报清单。"
          icon="ri-calendar-check-line"
        />
      </NSpin>
    </NCard>

    <!-- 编辑抽屉 -->
    <NDrawer v-model:show="editorVisible" :width="460" placement="right">
      <NDrawerContent :title="editingId ? '编辑认定行' : '新增认定行'" closable>
        <NForm label-placement="top" size="small">
          <NFormItem label="征收项目" required>
            <NInput v-model:value="form.levyItem" placeholder="如：增值税 / 城市维护建设税 / 残疾人就业保障金" />
          </NFormItem>
          <NFormItem label="征收品目">
            <NInput v-model:value="form.levyCategory" placeholder="如：商业(3%) / 买卖合同 / 工资薪金所得" />
          </NFormItem>
          <NFormItem label="征收子目">
            <NInput v-model:value="form.levySubItem" placeholder="可留空" />
          </NFormItem>
          <NFormItem label="纳税期限">
            <NSelect v-model:value="form.filingCycle" :options="cycleOptions" />
          </NFormItem>
          <NFormItem label="申报期限">
            <NInput v-model:value="form.deadlineText" placeholder="期满之日起15日内 / 次年9月30号" />
          </NFormItem>
          <NFormItem label="征收率">
            <NInput v-model:value="form.levyRate" placeholder="0.03 或 3%；留空表示不适用" />
          </NFormItem>
          <NFormItem label="税率或单位税额">
            <NInput v-model:value="form.taxRate" placeholder="0.07 / 0.0003 / 8元/平方米" />
          </NFormItem>
          <NFormItem label="主附税标志">
            <NSelect v-model:value="form.mainFlag" :options="mainFlagOptions" />
          </NFormItem>
          <NFormItem label="征收代理方式">
            <NSelect v-model:value="form.collectionMethod" :options="collectionOptions" />
          </NFormItem>
          <NFormItem label="备注">
            <NInput v-model:value="form.note" placeholder="可留空" />
          </NFormItem>
        </NForm>
        <NAlert type="info" :bordered="false" :show-icon="false">
          「归属申报表」由征收项目自动判定：城建税 / 教育费附加 / 地方教育附加会并入增值税申报表，印花税多品目合并为财产和行为税申报表。
        </NAlert>
        <template #footer>
          <NSpace justify="end">
            <NButton size="small" @click="editorVisible = false">取消</NButton>
            <NButton size="small" type="primary" :loading="saving" @click="saveRow">保存</NButton>
          </NSpace>
        </template>
      </NDrawerContent>
    </NDrawer>

    <!-- 导入弹窗 -->
    <NModal v-model:show="importVisible" preset="card" style="width: 900px" title="从电子税务局同步认定信息">
      <p class="hint">
        电子税务局路径：登录后在【热门服务】➔【税务数字账户】➔【账户查询】➔【税费种认定信息】。
      </p>
      <NTabs v-model:value="importTab" type="line" animated>
        <NTabPane name="paste" tab="粘贴表格">
          <p class="hint">
            在电子税务局「税费种认定信息」页面框选整张表格复制（Ctrl+C），粘贴到下面。列序与页面一致即可，表头行会被自动跳过。
          </p>
          <NInput
            v-model:value="pasteText"
            type="textarea"
            :rows="7"
            placeholder="征收项目&#9;征收品目&#9;征收子目&#9;纳税期限&#9;申报期限&#9;征收率&#9;税率或单位税额&#9;主附税标志&#9;征收代理方式"
          />
        </NTabPane>
        <NTabPane name="ocr" tab="上传截图">
          <p class="hint">支持 PNG / JPG 截图。模型识别后务必核对小数位（0.03 与 3% 不是一回事），确认无误再导入。</p>
          <input ref="fileInput" type="file" accept="image/*" class="file-input" @change="handleFile" />
          <NButton size="small" :loading="ocrLoading" @click="fileInput?.click()">选择截图识别</NButton>
          <span v-if="recognizedRows.length" class="hint-inline">已识别 {{ recognizedRows.length }} 行</span>
        </NTabPane>
      </NTabs>

      <div class="import-preview">
        <div class="import-preview-head">
          <span>
            解析到 <strong>{{ importPreviewRows.length }}</strong> 行
            <template v-if="importTab === 'paste' && parsedPaste.skipped">
              （跳过 {{ parsedPaste.skipped }} 行表头 / 合计 / 不完整行）
            </template>
          </span>
        </div>
        <div v-if="importPreviewRows.length" class="mini-table-wrap">
          <table class="mini-table">
            <thead>
              <tr>
                <th v-for="column in [...TAX_REGISTRATION_COLUMNS, '归属申报表']" :key="column">{{ column }}</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, index) in importPreviewRows.slice(0, 12)" :key="index">
                <td v-for="column in [...TAX_REGISTRATION_COLUMNS, '归属申报表']" :key="column">
                  {{ row[column] || '—' }}
                </td>
              </tr>
            </tbody>
          </table>
          <p v-if="importPreviewRows.length > 12" class="hint">仅预览前 12 行，共 {{ importPreviewRows.length }} 行。</p>
        </div>
        <SoloEmpty
          v-else
          variant="hint"
          title="还没有可导入的行"
          description="粘贴或截图识别的内容会先出现在这里。"
        />
      </div>

      <template #footer>
        <div class="import-footer">
          <NRadioGroup v-model:value="importMode" size="small">
            <NRadioButton value="merge">合并（只更新本次出现的行）</NRadioButton>
            <NRadioButton value="replace">整表替换（本次没出现的行停用）</NRadioButton>
          </NRadioGroup>
          <NSpace :size="8">
            <NButton size="small" @click="importVisible = false">取消</NButton>
            <NButton
              size="small"
              type="primary"
              :loading="importing"
              :disabled="!importPreviewRows.length"
              @click="confirmImport"
            >
              确认导入
            </NButton>
          </NSpace>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.tax-registration-page {
  padding: 20px 24px 40px;
  max-width: 1440px;
  margin: 0 auto;
}
.page-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
}
.title-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 650;
  color: var(--solo-text-primary);
}
.header-sub {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--solo-text-secondary);
  max-width: 720px;
}
.stat-row {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}
.stat-card {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 14px 16px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: 12px;
}
.stat-label {
  font-size: 12px;
  color: var(--solo-text-secondary);
}
.stat-value {
  font-family: Inter, var(--solo-font-mono, monospace);
  font-size: 26px;
  font-weight: 680;
  color: var(--solo-text-primary);
  font-variant-numeric: tabular-nums;
}
.stat-value--text {
  font-size: 20px;
}
.stat-hint {
  font-size: 11px;
  color: var(--solo-text-muted);
}
.block-alert {
  margin-bottom: 12px;
  border-radius: 8px;
}
.alert-actions {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}
.conflict-item {
  display: inline-block;
  margin-right: 8px;
  padding: 1px 8px;
  border-radius: 999px;
  background: var(--solo-bg-soft);
  font-size: 12px;
}
.table-card,
.preview-card {
  margin-bottom: 14px;
  border-radius: 12px;
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.card-title {
  font-size: 15px;
  font-weight: 650;
  color: var(--solo-text-primary);
}
.card-sub {
  margin-left: 10px;
  font-size: 12px;
  color: var(--solo-text-muted);
}
.switch-label {
  font-size: 12px;
  color: var(--solo-text-secondary);
}
.table-empty {
  padding: 24px 0;
}
.timeline {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.timeline-item {
  padding: 12px 0;
  border-bottom: 1px solid var(--solo-border);
}
.timeline-item:last-child {
  border-bottom: none;
}
.timeline-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  margin-bottom: 8px;
}
.timeline-label {
  font-size: 13px;
  font-weight: 650;
  color: var(--solo-text-primary);
}
.timeline-due {
  font-size: 11px;
  color: var(--solo-text-muted);
  font-variant-numeric: tabular-nums;
}
.chip-row {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.chip {
  padding: 4px 10px;
  border-radius: 999px;
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-soft);
  font-size: 12px;
  color: var(--solo-text-primary);
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease;
}
.chip:hover {
  border-color: var(--solo-primary-light);
  background: var(--solo-bg-blue-soft);
}
.chip--success {
  color: var(--solo-income);
  border-color: color-mix(in srgb, var(--solo-income) 35%, transparent);
}
.chip--warning {
  color: var(--solo-warning);
  border-color: color-mix(in srgb, var(--solo-warning) 35%, transparent);
}
.chip--default {
  color: var(--solo-text-muted);
}
.hint {
  margin: 0 0 10px;
  font-size: 12px;
  line-height: 1.7;
  color: var(--solo-text-secondary);
}
.hint-inline {
  margin-left: 10px;
  font-size: 12px;
  color: var(--solo-text-muted);
}
.file-input {
  display: none;
}
.import-preview {
  margin-top: 14px;
  border-top: 1px solid var(--solo-border);
  padding-top: 12px;
}
.import-preview-head {
  font-size: 12px;
  color: var(--solo-text-secondary);
  margin-bottom: 8px;
}
.mini-table-wrap {
  max-height: 320px;
  overflow: auto;
  border: 1px solid var(--solo-border);
  border-radius: 8px;
}
.mini-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.mini-table th,
.mini-table td {
  padding: 6px 8px;
  border-bottom: 1px solid var(--solo-border);
  text-align: left;
  white-space: nowrap;
  color: var(--solo-text-primary);
}
.mini-table th {
  position: sticky;
  top: 0;
  background: var(--solo-bg-soft);
  color: var(--solo-text-secondary);
  font-weight: 600;
}
.import-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
@media (max-width: 900px) {
  .stat-row {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
  .page-header {
    flex-direction: column;
  }
}
</style>
