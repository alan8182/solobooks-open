<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, watch, computed } from 'vue'
import {
  NModal, NForm, NFormItem, NInput, NSelect, NDatePicker,
  NSwitch, NInputNumber, NSpace, NButton, useMessage,
} from 'naive-ui'
import { addEmployee, updateEmployee } from '@/api/salary'
import type { Employee } from '@/api/salary'

const props = defineProps<{
  show: boolean
  employee: Employee | null
  departmentOptions: Array<{ label: string; value: string }>
}>()

const emit = defineEmits<{
  (e: 'update:show', value: boolean): void
  (e: 'saved'): void
}>()

const message = useMessage()
const saving = ref(false)

const isEditing = computed(() => Boolean(props.employee?.id))

const form = ref({
  name: '',
  department: '研发部',
  base_salary: 6000,
  social_security_base: 4000,
  is_owner: false,
  hire_date: null as string | null,
  special_additional_deduction: 0,
})

watch(() => props.show, (visible) => {
  if (visible) {
    if (props.employee) {
      form.value = {
        name: props.employee.name,
        department: props.employee.department,
        base_salary: Number(props.employee.base_salary || 0),
        social_security_base: Number(props.employee.social_security_base || 0),
        is_owner: Boolean(props.employee.is_owner),
        hire_date: props.employee.hire_date || null,
        special_additional_deduction: Number(props.employee.special_additional_deduction || 0),
      }
    } else {
      form.value = {
        name: '',
        department: '研发部',
        base_salary: 6000,
        social_security_base: 4000,
        is_owner: false,
        hire_date: null,
        special_additional_deduction: 0,
      }
    }
  }
})

function closeModal() {
  emit('update:show', false)
}

async function handleSave() {
  if (!form.value.name.trim()) {
    message.warning('请输入员工姓名')
    return
  }
  saving.value = true
  try {
    const payload = {
      ...form.value,
      hire_date: form.value.hire_date || null,
    }
    if (isEditing.value && props.employee?.id) {
      await updateEmployee(props.employee.id, payload)
      message.success('员工信息已更新')
    } else {
      await addEmployee(payload)
      message.success('新增员工成功')
    }
    emit('saved')
    closeModal()
  } catch (error: any) {
    message.error(error.response?.data?.detail || '保存员工失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    :title="isEditing ? '编辑员工信息' : '录入新员工'"
    style="width: min(440px, 94vw); border-radius: 14px"
    @update:show="closeModal"
  >
    <NForm label-placement="left" label-width="84" style="margin-top: 10px">
      <NFormItem label="员工姓名">
        <NInput v-model:value="form.name" placeholder="请输入员工姓名" />
      </NFormItem>
      <NFormItem
        label="所属部门"
        feedback="可直接输入新部门名（如 总经办）后回车，保存员工时自动加入部门列表"
        feedback-style="font-size: 12px; opacity: 0.65; line-height: 1.5;"
      >
        <NSelect
          v-model:value="form.department"
          :options="departmentOptions"
          filterable
          tag
          placeholder="选择或直接输入部门名"
          :consistent-menu-width="false"
        />
      </NFormItem>
      <NFormItem label="入职日期">
        <NDatePicker
          v-model:formatted-value="form.hire_date"
          value-format="yyyy-MM-dd"
          type="date"
          clearable
          size="medium"
          style="width: 100%"
          placeholder="不填则不限月份"
        />
      </NFormItem>
      <NFormItem label="业主标记">
        <div class="owner-flag-row">
          <NSwitch v-model:value="form.is_owner" size="small" />
          <span class="owner-flag-hint">
            个体户/个独业主本人的工资不得税前扣除，汇算清缴时按此标记自动调增
          </span>
        </div>
      </NFormItem>
      <NFormItem label="基本工资">
        <NInputNumber
          v-model:value="form.base_salary"
          :min="0"
          :precision="2"
          :step="100"
          style="width: 100%"
        >
          <template #prefix>¥</template>
        </NInputNumber>
      </NFormItem>
      <NFormItem label="社保基数">
        <NInputNumber
          v-model:value="form.social_security_base"
          :min="0"
          :precision="2"
          :step="100"
          style="width: 100%"
        >
          <template #prefix>¥</template>
        </NInputNumber>
      </NFormItem>
      <NFormItem label="专项附加">
        <NInputNumber
          v-model:value="form.special_additional_deduction"
          :min="0"
          :precision="2"
          :step="500"
          style="width: 100%"
        >
          <template #prefix>¥</template>
        </NInputNumber>
      </NFormItem>
    </NForm>
    <p class="form-hint">
      专项附加为每月扣除合计（子女教育 2000/人、房贷利息 1000、赡养老人 3000、婴幼儿照护 2000 等），
      仅用于个税试算，按实际申报口径填写。
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
.owner-flag-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.owner-flag-hint {
  font-size: 11px;
  color: var(--solo-text-muted);
  line-height: 1.35;
}
.form-hint {
  margin: 4px 0 0;
  font-size: 11.5px;
  color: var(--solo-text-muted);
  line-height: 1.45;
}
</style>
