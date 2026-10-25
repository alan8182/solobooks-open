<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
// 维度与归集对象配置（设计文档 T02）。
//
// 两个要点：
// 1. 维度与成员都是**软删**（status='archived'），UI 文案一律用「归档」不用「删除」，
//    且归档前必须二次确认——归档后已有归集数据仍然保留，只是不能再被选中。
// 2. kind / memberStatus 的中文标签全部来自 GET /meta（store.metaMap / labelOf），
//    meta 未加载时表单禁用，绝不在前端硬编码「项目/部门/客户」这类选项数组。
import { computed, h, onMounted, reactive, ref, watch } from 'vue'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NDatePicker,
  NForm,
  NFormItem,
  NInput,
  NModal,
  NSelect,
  NSpace,
  NSpin,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns, FormInst, FormRules } from 'naive-ui'
import DimensionPicker from './_components/DimensionPicker.vue'
import {
  createDimension,
  createMember,
  listMembers,
  updateDimension,
  updateMember,
} from '@/api/aggregation'
import { useAggregationStore } from '@/stores/aggregation'
import { errorText } from '@/utils/aggregation'
import { SoloEmpty } from '@/components/ui'
import type {
  AggregationDimension,
  AggregationDimensionKind,
  AggregationMember,
  AggregationMemberStatus,
} from '@/api/types'

const store = useAggregationStore()
const message = useMessage()
const dialog = useDialog()

const membersLoading = ref(false)
const members = ref<AggregationMember[]>([])

// ===== 维度表 =====

const dimensionColumns = computed<DataTableColumns<AggregationDimension>>(() => [
  { title: '编码', key: 'code', width: 150 },
  { title: '名称', key: 'name', ellipsis: { tooltip: true } },
  {
    title: '类型',
    key: 'kind',
    width: 110,
    render: (row) => store.labelOf('kind', row.kind),
  },
  {
    title: '状态',
    key: 'status',
    width: 96,
    render: (row) =>
      h(
        NTag,
        { size: 'small', bordered: false, type: row.status === 'active' ? 'success' : 'info' },
        { default: () => (row.status === 'active' ? '启用' : '已归档') },
      ),
  },
  {
    title: '操作',
    key: 'actions',
    width: 150,
    render: (row) =>
      h(NSpace, { size: 4, wrap: false }, {
        default: () => [
          h(
            NButton,
            { size: 'tiny', quaternary: true, onClick: () => store.setCurrentDimension(row.id) },
            { default: () => '查看对象' },
          ),
          row.status === 'active'
            ? h(
                NButton,
                {
                  size: 'tiny',
                  quaternary: true,
                  type: 'error',
                  onClick: () => confirmArchiveDimension(row),
                },
                { default: () => '归档' },
              )
            : null,
        ],
      }),
  },
])

/** 归档是软删：确认文案必须说清「历史归集数据仍保留」 */
function confirmArchiveDimension(row: AggregationDimension): void {
  dialog.warning({
    title: '归档维度',
    content: `归档后「${row.name}」不再出现在维度选择器中，该维度下已有的归集数据与报表历史仍会保留。确认归档？`,
    positiveText: '归档',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await updateDimension(row.id, { status: 'archived' })
        message.success(`维度「${row.name}」已归档`)
        // 归档后必须强制重拉：GET /dimensions（不带 include_archived）不再返回它
        store.invalidate()
        await store.loadDimensions(true)
      } catch (err) {
        message.error(errorText(err, '归档维度失败'))
      }
    },
  })
}

// ===== 建维度表单 =====

const showDimensionModal = ref(false)
const dimensionSubmitting = ref(false)
const dimensionFormRef = ref<FormInst | null>(null)

const dimensionForm = reactive({
  code: '',
  name: '',
  kind: '' as AggregationDimensionKind | '',
})

/** kind 选项全部来自 store.meta（GET /meta）。meta 未加载时表单一律禁用 */
const kindOptions = computed(() =>
  store.kindOptions.map((option) => ({ label: option.label, value: option.value })),
)

const dimensionRules: FormRules = {
  code: [{ required: true, message: '请填写维度编码', trigger: ['input', 'blur'] }],
  name: [{ required: true, message: '请填写维度名称', trigger: ['input', 'blur'] }],
  kind: [{ required: true, message: '请选择维度类型', trigger: ['change', 'blur'] }],
}

function openDimensionModal(): void {
  dimensionForm.code = ''
  dimensionForm.name = ''
  dimensionForm.kind = ''
  showDimensionModal.value = true
}

async function submitDimension(): Promise<void> {
  try {
    await dimensionFormRef.value?.validate()
  } catch {
    return
  }
  dimensionSubmitting.value = true
  try {
    const created = await createDimension({
      code: dimensionForm.code.trim(),
      name: dimensionForm.name.trim(),
      // 表单已校验非空，此处兜底为 'custom'（后端 schema 的默认值）以满足类型
      kind: (dimensionForm.kind || 'custom') as AggregationDimensionKind,
    })
    message.success(`维度「${created.name}」已创建`)
    showDimensionModal.value = false
    store.invalidate()
    await store.loadDimensions(true)
    store.setCurrentDimension(created.id)
  } catch (err) {
    message.error(errorText(err, '创建维度失败'))
  } finally {
    dimensionSubmitting.value = false
  }
}

// ===== 成员表 =====

const memberColumns = computed<DataTableColumns<AggregationMember>>(() => [
  { title: '编码', key: 'code', width: 150 },
  { title: '名称', key: 'name', ellipsis: { tooltip: true } },
  { title: '生效日', key: 'startDate', width: 120 },
  { title: '失效日', key: 'endDate', width: 120, render: (row) => row.endDate || '—' },
  {
    title: '状态',
    key: 'status',
    width: 100,
    render: (row) =>
      h(
        NTag,
        { size: 'small', bordered: false, type: statusTagType(row.status) },
        { default: () => store.labelOf('memberStatus', row.status) },
      ),
  },
  {
    title: '操作',
    key: 'actions',
    width: 150,
    render: (row) =>
      h(NSpace, { size: 4, wrap: false }, {
        default: () => [
          h(
            NButton,
            { size: 'tiny', quaternary: true, onClick: () => openMemberModal(row) },
            { default: () => '编辑' },
          ),
          row.status !== 'archived'
            ? h(
                NButton,
                {
                  size: 'tiny',
                  quaternary: true,
                  type: 'error',
                  onClick: () => confirmArchiveMember(row),
                },
                { default: () => '归档' },
              )
            : null,
        ],
      }),
  },
])

function statusTagType(status: string): 'default' | 'success' | 'warning' | 'info' {
  if (status === 'active') return 'success'
  if (status === 'draft') return 'warning'
  if (status === 'archived') return 'info'
  return 'default'
}

async function loadMembers(): Promise<void> {
  const dimensionId = store.currentDimensionId
  if (!dimensionId) {
    members.value = []
    return
  }
  membersLoading.value = true
  try {
    members.value = await listMembers(dimensionId, false)
  } catch (err) {
    message.error(errorText(err, '加载归集对象失败'))
  } finally {
    membersLoading.value = false
  }
}

watch(() => store.currentDimensionId, () => {
  void loadMembers()
})

// ===== 成员表单（新建 / 编辑共用）=====

const showMemberModal = ref(false)
const memberSubmitting = ref(false)
const memberFormRef = ref<FormInst | null>(null)
/** 编辑中的成员 id；为空表示新建 */
const editingMemberId = ref<string>('')

const memberForm = reactive({
  code: '',
  name: '',
  // NDatePicker 交出毫秒时间戳（value-format 只在运行期生效，TS 侧仍按 number 校验），
  // 因此这里用 number 接收，提交前由 toDateString 转成后端要的 YYYY-MM-DD。
  startDate: null as number | null,
  endDate: null as number | null,
  status: 'active' as AggregationMemberStatus,
})

const memberRules: FormRules = {
  code: [{ required: true, message: '请填写对象编码', trigger: ['input', 'blur'] }],
  name: [{ required: true, message: '请填写对象名称', trigger: ['input', 'blur'] }],
  startDate: [{ required: true, message: '请选择生效日期', trigger: ['change', 'blur'] }],
}

const memberStatusOptions = computed(() =>
  store.memberStatusOptions.map((option) => ({ label: option.label, value: option.value })),
)

function openMemberModal(row?: AggregationMember): void {
  editingMemberId.value = row?.id ?? ''
  memberForm.code = row?.code ?? ''
  memberForm.name = row?.name ?? ''
  memberForm.startDate = row?.startDate ? new Date(row.startDate).getTime() : null
  memberForm.endDate = row?.endDate ? new Date(row.endDate).getTime() : null
  memberForm.status = (row?.status ?? 'active') as AggregationMemberStatus
  showMemberModal.value = true
}

/** NDatePicker 交出毫秒时间戳，后端要 YYYY-MM-DD 字符串 */
function toDateString(timestamp: number | null): string | null {
  if (!timestamp) return null
  const date = new Date(timestamp)
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${date.getFullYear()}-${month}-${day}`
}

async function submitMember(): Promise<void> {
  try {
    await memberFormRef.value?.validate()
  } catch {
    return
  }
  const dimensionId = store.currentDimensionId
  if (!dimensionId) {
    message.error('请先选择维度')
    return
  }
  const startDate = toDateString(memberForm.startDate)
  if (!startDate) return
  const endDate = toDateString(memberForm.endDate)

  memberSubmitting.value = true
  try {
    if (editingMemberId.value) {
      await updateMember(editingMemberId.value, {
        name: memberForm.name.trim(),
        endDate,
        status: memberForm.status,
      })
      message.success('归集对象已更新')
    } else {
      await createMember(dimensionId, {
        code: memberForm.code.trim(),
        name: memberForm.name.trim(),
        startDate,
        endDate,
        status: memberForm.status,
      })
      message.success('归集对象已创建')
    }
    showMemberModal.value = false
    await loadMembers()
  } catch (err) {
    message.error(errorText(err, editingMemberId.value ? '更新归集对象失败' : '创建归集对象失败'))
  } finally {
    memberSubmitting.value = false
  }
}

function confirmArchiveMember(row: AggregationMember): void {
  dialog.warning({
    title: '归档归集对象',
    content: `归档后「${row.name}」不再参与新的规则匹配与手工归集，已产生的归集明细仍会保留。确认归档？`,
    positiveText: '归档',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await updateMember(row.id, { status: 'archived' })
        message.success(`归集对象「${row.name}」已归档`)
        await loadMembers()
      } catch (err) {
        message.error(errorText(err, '归档归集对象失败'))
      }
    },
  })
}

// ===== 生命周期 =====

/** meta 是否就绪：未就绪时所有表单禁用（kind/status 选项都来自 meta） */
const metaReady = computed(() => store.meta !== null)

onMounted(async () => {
  try {
    await store.bootstrap()
    await loadMembers()
  } catch (err) {
    message.error(errorText(err, '加载归集维度失败'))
  }
})
</script>

<template>
  <div class="agg-dim-page">
    <div class="page-header">
      <h2>维度与归集对象</h2>
      <NSpace :size="8">
        <NButton size="small" @click="store.loadDimensions(true)">刷新维度</NButton>
        <NButton size="small" type="primary" :disabled="!metaReady" @click="openDimensionModal">
          新建维度
        </NButton>
      </NSpace>
    </div>

    <NAlert v-if="!metaReady" type="warning" :bordered="false" class="meta-alert">
      维度类型与对象状态的选项来自服务端配置，正在加载，请稍候再操作。
    </NAlert>

    <div class="picker-row">
      <DimensionPicker />
    </div>

    <NCard title="归集维度" :bordered="false" class="block-card">
      <NDataTable
        :columns="dimensionColumns"
        :data="store.dimensions"
        :row-key="(row: AggregationDimension) => row.id"
        :row-props="(row: AggregationDimension) => ({
          style: row.id === store.currentDimensionId ? 'font-weight:600' : '',
        })"
        :bordered="false"
        striped
        size="small"
      >
        <template #empty>
          <SoloEmpty
            variant="hint"
            title="还没有归集维度"
            description="先建一个维度（项目 / 部门 / 客户），归集才有去处。"
          />
        </template>
      </NDataTable>
    </NCard>

    <NCard title="归集对象" :bordered="false" class="block-card">
      <template #header-extra>
        <NButton
          size="small"
          type="primary"
          :disabled="!store.currentDimensionId || !metaReady"
          @click="openMemberModal()"
        >
          新建对象
        </NButton>
      </template>
      <NAlert v-if="!store.currentDimensionId" type="info" :bordered="false" class="meta-alert">
        请先在上方选择一个维度，再维护它下面的归集对象。
      </NAlert>
      <NSpin v-else :show="membersLoading">
        <NDataTable
          :columns="memberColumns"
          :data="members"
          :row-key="(row: AggregationMember) => row.id"
          :bordered="false"
          striped
          size="small"
        />
      </NSpin>
    </NCard>

    <!-- 建维度 -->
    <NModal
      v-model:show="showDimensionModal"
      preset="card"
      title="新建归集维度"
      style="width: min(520px, 96vw)"
    >
      <NForm ref="dimensionFormRef" :model="dimensionForm" :rules="dimensionRules">
        <NFormItem label="维度编码" path="code">
          <NInput v-model:value="dimensionForm.code" placeholder="如 RD / DEPT" />
        </NFormItem>
        <NFormItem label="维度名称" path="name">
          <NInput v-model:value="dimensionForm.name" placeholder="如 研发项目" />
        </NFormItem>
        <NFormItem label="维度类型" path="kind">
          <NSelect
            v-model:value="dimensionForm.kind"
            :options="kindOptions"
            :disabled="!metaReady"
            placeholder="选择维度类型"
          />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showDimensionModal = false">取消</NButton>
          <NButton type="primary" :loading="dimensionSubmitting" @click="submitDimension">
            创建
          </NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- 新建 / 编辑归集对象 -->
    <NModal
      v-model:show="showMemberModal"
      preset="card"
      :title="editingMemberId ? '编辑归集对象' : '新建归集对象'"
      style="width: min(520px, 96vw)"
    >
      <NForm ref="memberFormRef" :model="memberForm" :rules="memberRules">
        <NFormItem label="对象编码" path="code">
          <NInput
            v-model:value="memberForm.code"
            placeholder="如 RD-2026-01"
            :disabled="Boolean(editingMemberId)"
          />
        </NFormItem>
        <NFormItem label="对象名称" path="name">
          <NInput v-model:value="memberForm.name" placeholder="如 研发项目A" />
        </NFormItem>
        <NFormItem label="生效日期" path="startDate">
          <NDatePicker
            v-model:value="memberForm.startDate"
            type="date"
            :disabled="Boolean(editingMemberId)"
            style="width: 100%"
          />
        </NFormItem>
        <NFormItem label="失效日期">
          <NDatePicker
            v-model:value="memberForm.endDate"
            type="date"
            clearable
            style="width: 100%"
          />
        </NFormItem>
        <NFormItem label="状态">
          <NSelect
            v-model:value="memberForm.status"
            :options="memberStatusOptions"
            :disabled="!metaReady"
          />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showMemberModal = false">取消</NButton>
          <NButton type="primary" :loading="memberSubmitting" @click="submitMember">
            保存
          </NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.agg-dim-page {
  max-width: 1080px;
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
.meta-alert {
  margin-bottom: 12px;
}
.block-card {
  border-radius: var(--solo-radius-lg);
  margin-bottom: 16px;
}
</style>