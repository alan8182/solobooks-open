<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NButton, NCard, NForm, NFormItem, NInput, NModal, NSelect, NSwitch, NTabPane, NTabs, NTag, useMessage,
} from 'naive-ui'
import {
  createAuxiliary, deactivateAuxiliary, deleteAuxiliary, listAuxiliary, updateAuxiliary,
} from '@/api/auxiliary'
import type { AuxiliaryItem, AuxiliaryKind } from '@/api/auxiliary'
import { SoloEmpty } from '@/components/ui'

const router = useRouter()
const message = useMessage()

const kind = ref<AuxiliaryKind>('partner')
const keyword = ref('')
const includeInactive = ref(false)
const loading = ref(false)
const items = ref<AuxiliaryItem[]>([])
const total = ref(0)

const showModal = ref(false)
const saving = ref(false)
const editing = ref<AuxiliaryItem | null>(null)
const form = ref({
  name: '',
  code: '',
  type: 'both' as 'customer' | 'supplier' | 'both',
  taxId: '',
  contact: '',
  phone: '',
  address: '',
  mnemonic: '',
})

const typeOptions = [
  { label: '客户', value: 'customer' },
  { label: '供应商', value: 'supplier' },
  { label: '客户 + 供应商', value: 'both' },
]

const kindLabel = computed(() => ({ partner: '往来单位', department: '部门', project: '项目' })[kind.value])

async function load() {
  loading.value = true
  try {
    const data = await listAuxiliary(kind.value, {
      keyword: keyword.value.trim() || undefined,
      includeInactive: includeInactive.value,
      pageSize: 200,
    })
    items.value = data.items
    total.value = data.total
  } catch {
    message.error('加载档案失败')
  } finally {
    loading.value = false
  }
}

watch([kind, includeInactive], load)

function openCreate() {
  editing.value = null
  form.value = { name: '', code: '', type: 'both', taxId: '', contact: '', phone: '', address: '', mnemonic: '' }
  showModal.value = true
}

function openEdit(item: AuxiliaryItem) {
  editing.value = item
  form.value = {
    name: item.name,
    code: item.code,
    type: (item.type as 'customer' | 'supplier' | 'both') || 'both',
    taxId: item.taxId || '',
    contact: item.contact || '',
    phone: item.phone || '',
    address: item.address || '',
    mnemonic: item.mnemonic || '',
  }
  showModal.value = true
}

async function save() {
  if (!form.value.name.trim()) {
    message.warning('名称不能为空')
    return
  }
  saving.value = true
  try {
    const payload: Record<string, unknown> = {
      name: form.value.name.trim(),
      code: form.value.code.trim() || undefined,
    }
    if (kind.value === 'partner') {
      Object.assign(payload, {
        type: form.value.type,
        taxId: form.value.taxId.trim(),
        contact: form.value.contact.trim(),
        phone: form.value.phone.trim(),
        address: form.value.address.trim(),
        mnemonic: form.value.mnemonic.trim(),
      })
    }
    if (editing.value) {
      await updateAuxiliary(kind.value, editing.value.id, payload)
      message.success('已保存')
    } else {
      await createAuxiliary(kind.value, payload)
      message.success('已新增')
    }
    showModal.value = false
    await load()
  } catch (error: unknown) {
    const detail = (error as { response?: { data?: { detail?: { message?: string } | string } } })?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : detail?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

async function toggleActive(item: AuxiliaryItem) {
  try {
    if (item.isActive) {
      await deactivateAuxiliary(kind.value, item.id)
      message.success('已停用，历史凭证仍显示原名称')
    } else {
      await updateAuxiliary(kind.value, item.id, { isActive: true })
      message.success('已启用')
    }
    await load()
  } catch {
    message.error('操作失败')
  }
}

async function remove(item: AuxiliaryItem) {
  try {
    await deleteAuxiliary(kind.value, item.id)
    message.success('已删除')
    await load()
  } catch (error: unknown) {
    const detail = (error as { response?: { data?: { detail?: { message?: string } | string } } })?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : detail?.message || '该档案已被凭证引用，只能停用')
  }
}

onMounted(load)
</script>

<template>
  <div class="report-container">
    <div class="report-header">
      <div class="report-header-left">
        <NButton text class="report-back-btn" @click="router.push('/reports')">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回报表中心
        </NButton>
        <div class="report-title-section">
          <h2>辅助核算档案</h2>
          <p class="report-subtitle">往来单位 / 部门 / 项目：凭证挂上档案后，往来余额、账龄、部门项目费用才能按维度出表</p>
        </div>
      </div>
      <div class="report-header-right">
        <NButton type="primary" @click="openCreate">
          <template #icon><i class="ri-add-line" /></template>
          新增{{ kindLabel }}
        </NButton>
      </div>
    </div>

    <div class="filter-bar mb-4">
      <NInput v-model:value="keyword" placeholder="名称 / 编码 / 助记码" style="width: 260px;" clearable @keyup.enter="load" />
      <NButton @click="load">查询</NButton>
      <span class="switch-label">显示已停用</span>
      <NSwitch v-model:value="includeInactive" size="small" />
      <span class="total-hint">共 {{ total }} 个档案</span>
    </div>

    <NTabs v-model:value="kind" type="line" animated>
      <NTabPane name="partner" tab="往来单位" />
      <NTabPane name="department" tab="部门" />
      <NTabPane name="project" tab="项目" />
    </NTabs>

    <NCard :bordered="false" class="report-card">
      <div class="report-table-wrapper">
        <table class="report-table">
          <thead>
            <tr>
              <th style="width: 110px;">编码</th>
              <th>名称</th>
              <th v-if="kind === 'partner'" style="width: 130px;">类型</th>
              <th v-if="kind === 'partner'" style="width: 200px;">税号</th>
              <th v-if="kind === 'partner'" style="width: 130px;">联系人</th>
              <th v-if="kind === 'partner'" style="width: 140px;">电话</th>
              <th style="width: 90px;">状态</th>
              <th style="width: 170px; text-align: right;">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in items" :key="item.id" :class="{ inactive: !item.isActive }">
              <td class="mono">{{ item.code }}</td>
              <td>{{ item.name }}</td>
              <td v-if="kind === 'partner'">
                <NTag size="small" :bordered="false">
                  {{ item.type === 'customer' ? '客户' : item.type === 'supplier' ? '供应商' : '客户+供应商' }}
                </NTag>
              </td>
              <td v-if="kind === 'partner'" class="mono">{{ item.taxId || '—' }}</td>
              <td v-if="kind === 'partner'">{{ item.contact || '—' }}</td>
              <td v-if="kind === 'partner'">{{ item.phone || '—' }}</td>
              <td>
                <NTag size="small" :type="item.isActive ? 'success' : 'default'" :bordered="false">
                  {{ item.isActive ? '启用' : '停用' }}
                </NTag>
              </td>
              <td class="actions">
                <NButton size="tiny" text type="primary" @click="openEdit(item)">编辑</NButton>
                <NButton size="tiny" text @click="toggleActive(item)">{{ item.isActive ? '停用' : '启用' }}</NButton>
                <NButton size="tiny" text type="error" @click="remove(item)">删除</NButton>
              </td>
            </tr>
            <tr v-if="items.length === 0">
              <td :colspan="kind === 'partner' ? 8 : 4" class="report-empty-cell is-component">
                <SoloEmpty
                  variant="hint"
                  dense
                  title="还没有{{ kindLabel }}档案"
                  description="新增后即可在凭证与导入流程中挂接。"
                />
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </NCard>

    <NModal v-model:show="showModal" preset="card" :title="`${editing ? '编辑' : '新增'}${kindLabel}`" style="max-width: 520px;">
      <NForm label-placement="top">
        <NFormItem label="名称" required>
          <NInput v-model:value="form.name" placeholder="如：杭州甲科技有限公司" maxlength="120" />
        </NFormItem>
        <NFormItem label="编码">
          <NInput v-model:value="form.code" placeholder="留空自动生成" maxlength="40" />
        </NFormItem>
        <template v-if="kind === 'partner'">
          <NFormItem label="类型">
            <NSelect v-model:value="form.type" :options="typeOptions" />
          </NFormItem>
          <NFormItem label="纳税人识别号">
            <NInput v-model:value="form.taxId" maxlength="32" />
          </NFormItem>
          <div class="form-grid">
            <NFormItem label="联系人"><NInput v-model:value="form.contact" maxlength="32" /></NFormItem>
            <NFormItem label="电话"><NInput v-model:value="form.phone" maxlength="32" /></NFormItem>
          </div>
          <NFormItem label="地址"><NInput v-model:value="form.address" maxlength="120" /></NFormItem>
          <NFormItem label="助记码">
            <NInput v-model:value="form.mnemonic" placeholder="留空按名称自动生成" maxlength="32" />
          </NFormItem>
        </template>
      </NForm>
      <template #footer>
        <div class="modal-footer">
          <NButton @click="showModal = false">取消</NButton>
          <NButton type="primary" :loading="saving" @click="save">保存</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.report-header { align-items: flex-start; }
.report-header-right { display: flex; gap: 8px; }
.filter-bar {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 16px;
  background: #ffffff;
  border: 1px solid #e5e9ef;
  border-radius: 8px;
}
.switch-label { font-size: 12px; color: var(--solo-text-muted); }
.total-hint { margin-left: auto; font-size: 12px; color: var(--solo-text-muted); }
.report-table-wrapper { overflow-x: auto; }
.mono { font-variant-numeric: tabular-nums; }
.actions { text-align: right; white-space: nowrap; }
.actions :deep(.n-button) { margin-left: 10px; }
.inactive td { color: var(--solo-text-muted); }
.form-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.modal-footer { display: flex; justify-content: flex-end; gap: 8px; }
</style>
