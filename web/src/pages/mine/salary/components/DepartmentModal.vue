<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, watch } from 'vue'
import {
  NModal, NInput, NSwitch, NButton, NSpace, useMessage,
} from 'naive-ui'
import { updateDepartments, type DepartmentItem } from '@/api/salary'

const props = defineProps<{
  show: boolean
  departments: DepartmentItem[]
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'updated', depts: DepartmentItem[]): void
}>()

const message = useMessage()
const saving = ref(false)
const deptDraft = ref<Array<{ name: string; isRd: boolean }>>([])

watch(() => props.show, (visible) => {
  if (visible) {
    deptDraft.value = props.departments.map((item) => ({
      name: item.name,
      isRd: Boolean(item.isRd),
    }))
    if (!deptDraft.value.length) {
      deptDraft.value = [{ name: '行政部', isRd: false }]
    }
  }
})

function closeModal() {
  emit('update:show', false)
}

function addDeptRow() {
  deptDraft.value.push({ name: '', isRd: false })
}

function removeDeptRow(index: number) {
  deptDraft.value.splice(index, 1)
}

function deptHeadcount(name: string) {
  const hit = props.departments.find((item) => item.name === name.trim())
  if (!hit) return '新部门'
  const count = Number(hit.headcount || 0)
  return count > 0 ? `${count} 人` : '无员工'
}

async function handleSave() {
  const rows = deptDraft.value
    .map((item) => ({ name: item.name.trim(), isRd: item.isRd }))
    .filter((item) => item.name)

  if (!rows.length) {
    message.warning('至少保留一个部门')
    return
  }
  if (new Set(rows.map((item) => item.name)).size !== rows.length) {
    message.warning('部门名称不能重复')
    return
  }

  saving.value = true
  try {
    const data: any = await updateDepartments(rows)
    emit('updated', data.departments || [])
    message.success('部门配置已更新')
    closeModal()
  } catch (error: any) {
    message.error(error.response?.data?.detail || '保存部门失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    title="部门设置（本账套）"
    style="width: min(560px, 92vw); border-radius: 14px"
    @update:show="closeModal"
  >
    <p class="modal-hint">
      部门决定工资借方的归集科目：勾选「研发」的部门，工资计入研发支出（430100101），且计提时需登记研发项目工时；其余部门计入管理费用。
    </p>
    <div class="dept-rows">
      <div v-for="(row, index) in deptDraft" :key="index" class="dept-row">
        <NInput v-model:value="row.name" placeholder="部门名称（如 技术部、总经办）" size="medium" style="flex: 1" />
        <div class="dept-rd-cell" :title="row.isRd ? '工资计入研发支出' : '工资计入管理费用'">
          <NSwitch v-model:value="row.isRd" size="small" />
          <span>研发</span>
        </div>
        <span class="dept-headcount">{{ deptHeadcount(row.name) }}</span>
        <NButton
          text
          type="error"
          :disabled="deptDraft.length <= 1"
          @click="removeDeptRow(index)"
        >
          删除
        </NButton>
      </div>
    </div>
    <NButton dashed block style="margin-top: 10px" @click="addDeptRow">+ 添加部门</NButton>
    <p class="modal-hint muted" style="margin-top: 10px">
      在职员工引用的部门无法直接删除；在员工表单输入新部门会自动收录。
    </p>
    <template #footer>
      <NSpace justify="end">
        <NButton @click="closeModal">取消</NButton>
        <NButton type="primary" :loading="saving" @click="handleSave">保存</NButton>
      </NSpace>
    </template>
  </NModal>
</template>

<style scoped>
.modal-hint {
  font-size: 12.5px;
  color: var(--solo-text-secondary);
  line-height: 1.5;
  margin: 0 0 12px;
}
.modal-hint.muted {
  font-size: 11.5px;
  color: var(--solo-text-muted);
}
.dept-rows {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 360px;
  overflow-y: auto;
}
.dept-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.dept-rd-cell {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--solo-text-secondary);
}
.dept-headcount {
  width: 50px;
  text-align: right;
  font-size: 12px;
  color: var(--solo-text-muted);
}
</style>
