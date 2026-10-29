<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, h, ref } from 'vue'
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
  NSelect,
  NSpace,
  NSwitch,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import type { FilingCycle, TaxRegistrationInput, TaxRegistrationRow } from '@/api/tax-registration'
import { SoloEmpty } from '@/components/ui'
import {
  cycleLabel,
  formatRate,
  formatTaxRate,
} from '@/utils/taxRegistration'

const props = defineProps<{
  rows: TaxRegistrationRow[]
  loading: boolean
  showInactive: boolean
  filingEntryHint: string
  saving: boolean
}>()

const emit = defineEmits<{
  'update:showInactive': [value: boolean]
  save: [payload: { id?: string; data: TaxRegistrationInput }]
  deactivate: [row: TaxRegistrationRow]
}>()

const dialog = useDialog()
const message = useMessage()

const displayRows = computed(() =>
  props.showInactive ? props.rows : props.rows.filter((row) => row.status === 'active'),
)

// 编辑 / 新增抽屉
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
  editingId.value = row.id as string
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

function handleSave() {
  if (!form.value.levyItem.trim()) {
    message.warning('征收项目不能为空')
    return
  }
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
  emit('save', {
    id: editingId.value ?? undefined,
    data: payload,
  })
}

function closeEditor() {
  editorVisible.value = false
}

defineExpose({
  openEditor,
  closeEditor,
})

function confirmDeactivate(row: TaxRegistrationRow) {
  dialog.warning({
    title: '停用这条认定',
    content: `停用「${row.levyItem}${row.levyCategory ? ` · ${row.levyCategory}` : ''}」后，该税种不会再生成新的待办；已生成的待办会标记为「不适用」，已申报记录不受影响。`,
    positiveText: '停用',
    negativeText: '取消',
    onPositiveClick: () => {
      emit('deactivate', row)
    },
  })
}

const columns = computed<DataTableColumns<TaxRegistrationRow>>(() => [
  { title: '序号', key: 'seq', width: 56, align: 'center' },
  {
    title: '征收项目与品目',
    key: 'levyItem',
    width: 220,
    render: (row) => {
      return h('div', { class: 'levy-cell' }, [
        h('div', { class: 'levy-main-title' }, row.levyItem),
        row.levyCategory
          ? h('div', { class: 'levy-sub-title' }, [
              row.levyCategory,
              row.levySubItem ? ` · ${row.levySubItem}` : '',
            ])
          : null,
      ])
    },
  },
  {
    title: '纳税期限',
    key: 'filingCycle',
    width: 90,
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
    title: '征收率 / 税率',
    key: 'taxRate',
    width: 120,
    render: (row) => {
      const rate = formatTaxRate(row)
      const levy = formatRate(row.levyRate)
      if (row.levyRate !== null && row.taxRate !== null) {
        return `${levy} (税率: ${rate})`
      }
      return rate !== '—' ? rate : levy
    },
  },
  {
    title: '归属申报表',
    key: 'returnGroupLabel',
    width: 210,
    render: (row) =>
      h(
        NTag,
        {
          size: 'small',
          bordered: false,
          type: row.returnGroup === 'other' ? 'default' : 'info',
          class: 'return-tag',
        },
        { default: () => row.returnGroupLabel },
      ),
  },
  {
    title: '主附标志',
    key: 'mainFlag',
    width: 96,
    render: (row) =>
      h(
        'span',
        { class: row.mainFlag === 'vat_attachment' ? 'flag-attachment' : 'flag-main' },
        row.mainFlag === 'vat_attachment' ? '增值税附税' : '主税',
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
    width: 110,
    fixed: 'right',
    render: (row) =>
      h(NSpace, { size: 6 }, {
        default: () => [
          h(
            NButton,
            { size: 'tiny', text: true, type: 'primary', onClick: () => openEditor(row) },
            { default: () => '编辑' },
          ),
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
</script>

<template>
  <NCard class="table-card" :bordered="false">
    <template #header>
      <div class="card-head">
        <div class="card-head-left">
          <span class="card-title">认定明细</span>
          <span class="card-sub">{{ filingEntryHint }}</span>
        </div>
        <NSpace align="center" :size="10">
          <span class="switch-label">含已停用</span>
          <NSwitch
            :value="showInactive"
            size="small"
            @update:value="emit('update:showInactive', $event)"
          />
          <NButton size="small" @click="openEditor()">
            <template #icon><i class="ri-add-line" /></template>
            新增一行
          </NButton>
        </NSpace>
      </div>
    </template>

    <!-- 解决双空状态 Bug：只有在有数据时才渲染 NDataTable；为空时直接由 SoloEmpty 接管 -->
    <div v-if="displayRows.length > 0 || loading" class="table-wrap">
      <NDataTable
        :columns="columns"
        :data="displayRows"
        :loading="loading"
        :row-key="(row: TaxRegistrationRow) => row.id ?? `${row.levyItem}-${row.levyCategory}`"
        :scroll-x="1100"
        size="small"
        :bordered="false"
      />
    </div>

    <!-- 单一而优雅的空状态 -->
    <SoloEmpty
      v-else
      title="还没有税费种认定"
      description="照着税局的认定表抄一遍，征期提醒和申报表才会对得上。"
      icon="ri-government-line"
      class="table-empty"
    >
      <template #action>
        <NButton size="small" type="primary" secondary @click="openEditor()">
          手动添加第一行认定
        </NButton>
      </template>
    </SoloEmpty>

    <!-- 编辑抽屉 -->
    <NDrawer v-model:show="editorVisible" :width="460" placement="right">
      <NDrawerContent :title="editingId ? '编辑税费种认定' : '新增税费种认定'" closable>
        <NForm label-placement="top" size="small">
          <NFormItem label="征收项目" required>
            <NInput v-model:value="form.levyItem" placeholder="如：增值税 / 城市维护建设税 / 企业所得税" />
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
            <NInput v-model:value="form.deadlineText" placeholder="期满之日起15日内 / 次年5月31日前" />
          </NFormItem>
          <NFormItem label="征收率">
            <NInput v-model:value="form.levyRate" placeholder="0.03 或 3%；留空表示不适用" />
          </NFormItem>
          <NFormItem label="税率或单位税额">
            <NInput v-model:value="form.taxRate" placeholder="0.07 / 0.0003 / 0.25" />
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
          「归属申报表」由系统根据征收项目智能判定：城建税、教育费附加、地方教育附加会自动并入增值税申报表；印花税各品目会归并为财产和行为税申报表。
        </NAlert>
        <template #footer>
          <NSpace justify="end">
            <NButton size="small" @click="editorVisible = false">取消</NButton>
            <NButton size="small" type="primary" :loading="saving" @click="handleSave">保存</NButton>
          </NSpace>
        </template>
      </NDrawerContent>
    </NDrawer>
  </NCard>
</template>

<style scoped>
.table-card {
  margin-bottom: 16px;
  border-radius: 10px;
  border: 1px solid var(--solo-border);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.02);
}
.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.card-head-left {
  display: flex;
  align-items: baseline;
  gap: 12px;
}
.card-title {
  font-size: 15px;
  font-weight: 650;
  color: var(--solo-text-primary);
}
.card-sub {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.switch-label {
  font-size: 12px;
  color: var(--solo-text-secondary);
}
.levy-cell {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.levy-main-title {
  font-weight: 600;
  color: var(--solo-text-primary);
}
.levy-sub-title {
  font-size: 11px;
  color: var(--solo-text-muted);
}
.flag-attachment {
  color: var(--solo-warning);
  font-size: 12px;
}
.flag-main {
  color: var(--solo-text-secondary);
  font-size: 12px;
}
.table-empty {
  padding: 36px 0 28px;
}
</style>
