<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { h, ref } from 'vue'
import {
  NButton,
  NCard,
  NForm,
  NFormItem,
  NGrid,
  NGi,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSpace,
  NSwitch,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import {
  createAdminIndustryTemplate,
  getAdminIndustryTemplates,
  updateAdminIndustryTemplate,
  type IndustryAccountPreset,
  type IndustryTemplateItem,
  type IndustryTemplateUpsertPayload,
} from '@/api/industryTemplates'
import { formatAdminDateTime, useAdminTable } from '@/composables/useAdminTable'
import { SoloButton, SoloStatusBadge, SoloTable } from '@/components/ui'

const message = useMessage()

const {
  loading,
  items,
  query,
  load,
  onSearch,
} = useAdminTable<IndustryTemplateItem>({
  loadData: async ({ query }) => getAdminIndustryTemplates({ q: query }),
})

interface TemplateDraft {
  code: string
  name: string
  groupCode: string
  groupName: string
  description: string
  restrictedDefault: boolean
  hidden: boolean
  enabled: boolean
  sortOrder: number
  accountPresets: IndustryAccountPreset[]
  modulesText: string
  notesText: string
}

function emptyPreset(): IndustryAccountPreset {
  return {
    code: '',
    name: '',
    parentCode: '',
    type: '费用',
    category: '损益',
    direction: 'debit',
    isLeaf: true,
  }
}

function emptyDraft(): TemplateDraft {
  return {
    code: '',
    name: '',
    groupCode: '',
    groupName: '',
    description: '',
    restrictedDefault: false,
    hidden: true,
    enabled: true,
    sortOrder: 0,
    accountPresets: [],
    modulesText: '',
    notesText: '',
  }
}

const showEditor = ref(false)
const editingId = ref('')
const saving = ref(false)
const draft = ref<TemplateDraft>(emptyDraft())

const directionOptions = [
  { label: '借方', value: 'debit' },
  { label: '贷方', value: 'credit' },
  { label: '自动', value: '' },
]

function splitTextList(value: string): string[] {
  return [...new Set(
    value
      .split(/\n/)
      .map(item => item.trim())
      .filter(Boolean),
  )]
}

function openCreate() {
  editingId.value = ''
  draft.value = emptyDraft()
  showEditor.value = true
}

function openEdit(template: IndustryTemplateItem) {
  editingId.value = template.id
  draft.value = {
    code: template.code,
    name: template.name,
    groupCode: template.groupCode,
    groupName: template.groupName,
    description: template.description,
    restrictedDefault: template.restrictedDefault,
    hidden: template.hidden,
    enabled: template.enabled,
    sortOrder: template.sortOrder,
    accountPresets: template.config.accountPresets.map(item => ({ ...item })),
    modulesText: template.config.modules.join('\n'),
    notesText: template.config.notes.join('\n'),
  }
  showEditor.value = true
}

function addPreset() {
  draft.value.accountPresets.push(emptyPreset())
}

function removePreset(index: number) {
  draft.value.accountPresets.splice(index, 1)
}

function validateDraft(): string {
  if (!/^[a-z0-9][a-z0-9_-]{2,95}$/.test(draft.value.code.trim())) {
    return '模板编码需为 3–96 位小写字母、数字、下划线或短横线'
  }
  if (!draft.value.name.trim()) return '请输入行业名称'
  if (!draft.value.groupName.trim()) return '请输入行业分组名称'
  for (const [index, preset] of draft.value.accountPresets.entries()) {
    const line = `第 ${index + 1} 个科目`
    if (!/^\d{7,9}$/.test(preset.code.trim())) return `${line}的科目编码需为 7–9 位数字`
    if (!preset.name.trim()) return `${line}缺少科目名称`
    if (!/^\d{4,9}$/.test(preset.parentCode.trim())) return `${line}的父级编码需为 4–9 位数字`
    if (!preset.code.trim().startsWith(preset.parentCode.trim())) {
      return `${line}的科目编码必须以父级编码开头`
    }
  }
  return ''
}

function buildPayload(): IndustryTemplateUpsertPayload {
  return {
    code: draft.value.code.trim(),
    name: draft.value.name.trim(),
    groupCode: draft.value.groupCode.trim(),
    groupName: draft.value.groupName.trim(),
    description: draft.value.description.trim(),
    restrictedDefault: draft.value.restrictedDefault,
    hidden: draft.value.hidden,
    enabled: draft.value.enabled,
    sortOrder: draft.value.sortOrder || 0,
    config: {
      accountPresets: draft.value.accountPresets.map(preset => ({
        code: preset.code.trim(),
        name: preset.name.trim(),
        parentCode: preset.parentCode.trim(),
        type: preset.type.trim(),
        category: preset.category.trim(),
        direction: preset.direction,
        isLeaf: preset.isLeaf,
      })),
      modules: splitTextList(draft.value.modulesText),
      notes: splitTextList(draft.value.notesText),
    },
  }
}

async function saveTemplate() {
  const validation = validateDraft()
  if (validation) {
    message.warning(validation)
    return
  }
  saving.value = true
  try {
    const payload = buildPayload()
    if (editingId.value) {
      await updateAdminIndustryTemplate(editingId.value, payload)
      message.success('行业模板已更新，版本号已递增')
    } else {
      await createAdminIndustryTemplate(payload)
      message.success('行业模板已创建')
    }
    showEditor.value = false
    await load()
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '模板保存失败')
  } finally {
    saving.value = false
  }
}

const columns: DataTableColumns<IndustryTemplateItem> = [
  {
    title: '行业模板',
    key: 'name',
    minWidth: 250,
    render: row =>
      h('div', { class: 'template-main-cell' }, [
        h('strong', null, row.name),
        h('span', { class: 'template-code' }, row.code),
      ]),
  },
  {
    title: '分组',
    key: 'groupName',
    minWidth: 170,
    render: row =>
      h('div', { class: 'template-group-cell' }, [
        h('span', null, row.groupName || '未分组'),
        h('small', null, row.groupCode || '—'),
      ]),
  },
  {
    title: '默认策略',
    key: 'restrictedDefault',
    width: 170,
    render: row =>
      h('div', { class: 'status-stack' }, [
        h(
          SoloStatusBadge,
          { tone: row.restrictedDefault ? 'danger' : 'neutral', size: 'small', plain: true },
          { default: () => (row.restrictedDefault ? '限制行业默认是' : '限制行业默认否') },
        ),
        h(
          SoloStatusBadge,
          { tone: row.hidden ? 'neutral' : 'success', size: 'small', plain: true },
          { default: () => (row.hidden ? '默认收起' : '默认展示') },
        ),
      ]),
  },
  {
    title: '状态',
    key: 'enabled',
    width: 105,
    render: row =>
      h(
        SoloStatusBadge,
        { tone: row.enabled ? 'success' : 'danger', size: 'small' },
        { default: () => (row.enabled ? '启用' : '停用') },
      ),
  },
  {
    title: '行业科目',
    key: 'accountPresets',
    width: 110,
    align: 'right',
    render: row => `${row.config.accountPresets.length} 个`,
  },
  {
    title: '版本',
    key: 'version',
    width: 80,
    align: 'center',
    render: row => `v${row.version}`,
  },
  {
    title: '更新时间',
    key: 'updatedAt',
    width: 155,
    render: row => formatAdminDateTime(row.updatedAt),
  },
  {
    title: '操作',
    key: 'actions',
    width: 90,
    fixed: 'right',
    render: row =>
      h(
        NButton,
        { size: 'tiny', quaternary: true, type: 'primary', onClick: () => openEdit(row) },
        { default: () => '编辑' },
      ),
  },
]
</script>

<template>
  <div class="admin-industry-templates">
    <NCard :bordered="false" class="filter-card">
      <div class="toolbar">
        <NInput
          v-model:value="query"
          placeholder="搜索行业名称、编码或分组"
          size="small"
          clearable
          style="width: 300px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        >
          <template #prefix><i class="ri-search-line" /></template>
        </NInput>
        <SoloButton size="small" variant="primary" @click="onSearch">
          <template #icon><i class="ri-search-line" /></template>
          搜索
        </SoloButton>
        <span class="toolbar-note">
          <i class="ri-information-line" />
          模板由后端持久化；更新只影响之后初始化的账套，不会回写存量科目。
        </span>
        <SoloButton size="small" variant="primary" @click="openCreate">
          <template #icon><i class="ri-add-line" /></template>
          新建行业模板
        </SoloButton>
      </div>
    </NCard>

    <NCard :bordered="false" class="table-card">
      <SoloTable
        :columns="columns"
        :data="items"
        :loading="loading"
        :bordered="false"
        size="small"
        :pagination="false"
        :scroll-x="1180"
        empty-title="未找到行业模板"
        empty-description="可以清空搜索条件，或新建一个行业模板"
      />
    </NCard>

    <NModal
      v-model:show="showEditor"
      preset="card"
      :title="editingId ? `编辑行业模板 · ${draft.name}` : '新建行业模板'"
      class="template-editor-modal"
      style="width: min(1120px, 96vw)"
      :mask-closable="false"
    >
      <NForm label-placement="top">
        <NGrid :cols="3" :x-gap="16">
          <NGi>
            <NFormItem label="模板编码" required>
              <NInput
                v-model:value="draft.code"
                :disabled="Boolean(editingId)"
                placeholder="如 ind_software_service"
              />
              <span class="field-hint">创建后不可修改，企业账套用它关联模板。</span>
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem label="行业名称" required>
              <NInput v-model:value="draft.name" placeholder="如 软件和信息技术服务业" />
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem label="排序">
              <NInputNumber v-model:value="draft.sortOrder" :min="0" style="width: 100%" />
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem label="分组编码">
              <NInput v-model:value="draft.groupCode" placeholder="如 grp_09" />
            </NFormItem>
          </NGi>
          <NGi :span="2">
            <NFormItem label="分组名称" required>
              <NInput v-model:value="draft.groupName" placeholder="如 信息传输、软件和信息技术服务业" />
            </NFormItem>
          </NGi>
          <NGi :span="3">
            <NFormItem label="模板说明">
              <NInput
                v-model:value="draft.description"
                type="textarea"
                :autosize="{ minRows: 2, maxRows: 4 }"
                placeholder="说明该行业适合哪些企业，以及会预置哪些会计能力"
              />
            </NFormItem>
          </NGi>
        </NGrid>

        <div class="switch-row">
          <label>
            <NSwitch v-model:value="draft.enabled" />
            <span>启用模板</span>
          </label>
          <label>
            <NSwitch v-model:value="draft.hidden" />
            <span>在常见行业列表中默认收起</span>
          </label>
          <label>
            <NSwitch v-model:value="draft.restrictedDefault" />
            <span>默认标记为国家限制或禁止行业</span>
          </label>
        </div>

        <div class="editor-section">
          <div class="section-header">
            <div>
              <h3>行业专用科目</h3>
              <p>新账套初始化时追加这些明细科目；存量账套已有科目可以继续自行调整。</p>
            </div>
            <SoloButton size="small" variant="secondary" @click="addPreset">
              <template #icon><i class="ri-add-line" /></template>
              添加科目
            </SoloButton>
          </div>

          <div v-if="draft.accountPresets.length" class="preset-editor-wrap">
            <table class="preset-editor">
              <thead>
                <tr>
                  <th>科目编码</th>
                  <th>科目名称</th>
                  <th>父级编码</th>
                  <th>类型</th>
                  <th>分类</th>
                  <th>方向</th>
                  <th>末级</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                <tr v-for="(preset, index) in draft.accountPresets" :key="index">
                  <td><NInput v-model:value="preset.code" size="tiny" placeholder="5602201" /></td>
                  <td><NInput v-model:value="preset.name" size="tiny" placeholder="云服务与接口费" /></td>
                  <td><NInput v-model:value="preset.parentCode" size="tiny" placeholder="5602" /></td>
                  <td><NInput v-model:value="preset.type" size="tiny" placeholder="费用" /></td>
                  <td><NInput v-model:value="preset.category" size="tiny" placeholder="损益" /></td>
                  <td>
                    <NSelect
                      v-model:value="preset.direction"
                      size="tiny"
                      :options="directionOptions"
                    />
                  </td>
                  <td class="center-cell"><NSwitch v-model:value="preset.isLeaf" size="small" /></td>
                  <td class="center-cell">
                    <NButton
                      size="tiny"
                      quaternary
                      type="error"
                      title="删除科目"
                      @click="removePreset(index)"
                    >
                      <template #icon><i class="ri-delete-bin-line" /></template>
                    </NButton>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
          <div v-else class="preset-empty">
            该模板暂不追加行业专用科目，将使用系统统一科目表。
          </div>
        </div>

        <NGrid :cols="2" :x-gap="16">
          <NGi>
            <NFormItem label="启用模块">
              <NInput
                v-model:value="draft.modulesText"
                type="textarea"
                :autosize="{ minRows: 3, maxRows: 6 }"
                placeholder="每行一个，如：研发费用归集"
              />
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem label="建账提示">
              <NInput
                v-model:value="draft.notesText"
                type="textarea"
                :autosize="{ minRows: 3, maxRows: 6 }"
                placeholder="每行一条，将作为该行业模板的说明"
              />
            </NFormItem>
          </NGi>
        </NGrid>
      </NForm>

      <template #footer>
        <NSpace justify="end">
          <NButton @click="showEditor = false">取消</NButton>
          <NButton type="primary" :loading="saving" @click="saveTemplate">
            {{ editingId ? '保存并生成新版本' : '创建模板' }}
          </NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.admin-industry-templates {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
}

.toolbar-note {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  font-size: 12.5px;
  color: var(--solo-text-muted);
}

.template-main-cell,
.template-group-cell,
.status-stack {
  display: flex;
  flex-direction: column;
  gap: 4px;
  line-height: 1.4;
}

.template-main-cell strong {
  color: var(--solo-text-primary);
}

.template-code,
.template-group-cell small,
.field-hint {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 11.5px;
  color: var(--solo-text-muted);
}

.status-stack {
  align-items: flex-start;
}

.switch-row {
  display: flex;
  flex-wrap: wrap;
  gap: 20px;
  padding: 14px 16px;
  margin-bottom: 18px;
  background: var(--solo-bg-soft);
  border-radius: 10px;
}

.switch-row label {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  color: var(--solo-text-secondary);
  font-size: 13px;
}

.editor-section {
  padding: 18px;
  margin-bottom: 18px;
  border: 1px solid var(--solo-border);
  border-radius: 10px;
}

.section-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 14px;
}

.section-header h3 {
  margin: 0 0 4px;
  font-size: 15px;
  color: var(--solo-text-primary);
}

.section-header p {
  margin: 0;
  font-size: 12.5px;
  color: var(--solo-text-muted);
}

.preset-editor-wrap {
  overflow-x: auto;
}

.preset-editor {
  width: 100%;
  min-width: 940px;
  border-collapse: collapse;
}

.preset-editor th {
  padding: 7px 5px;
  font-size: 11.5px;
  font-weight: 600;
  text-align: left;
  color: var(--solo-text-muted);
  background: var(--solo-bg-soft);
}

.preset-editor td {
  padding: 6px 5px;
  border-bottom: 1px solid var(--solo-border);
}

.preset-editor th:nth-child(1),
.preset-editor th:nth-child(3) {
  width: 115px;
}

.preset-editor th:nth-child(2) {
  width: 210px;
}

.preset-editor th:nth-child(4),
.preset-editor th:nth-child(5) {
  width: 100px;
}

.preset-editor th:nth-child(6) {
  width: 105px;
}

.preset-editor th:nth-child(7),
.preset-editor th:nth-child(8) {
  width: 58px;
}

.center-cell {
  text-align: center;
}

.preset-empty {
  padding: 22px;
  text-align: center;
  font-size: 12.5px;
  color: var(--solo-text-muted);
  border: 1px dashed var(--solo-border);
  border-radius: 8px;
}

@media (max-width: 900px) {
  .toolbar {
    align-items: stretch;
    flex-wrap: wrap;
  }

  .toolbar-note {
    order: 4;
    width: 100%;
    margin-left: 0;
  }

  .switch-row {
    flex-direction: column;
    gap: 12px;
  }
}
</style>
