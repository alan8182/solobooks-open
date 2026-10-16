<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { NCard, NButton, NForm, NFormItem, NInput, NSelect, NDatePicker, useMessage, NSpin } from 'naive-ui'
import { createTemplate, updateTemplate, getTemplate, type RecurringTemplate } from '@/api/recurring'

const router = useRouter()
const route = useRoute()
const message = useMessage()

const isEdit = !!route.params.id
const loading = ref(false)
const saving = ref(false)

const form = ref({
  name: '',
  frequency: 'monthly',
  next_due_date: '',
  template_data: '',
})

const frequencyOptions = [
  { label: '每周', value: 'weekly' },
  { label: '每两周', value: 'biweekly' },
  { label: '每月', value: 'monthly' },
  { label: '每季度', value: 'quarterly' },
  { label: '每年', value: 'yearly' },
]

onMounted(async () => {
  if (isEdit) {
    loading.value = true
    try {
      const tpl: RecurringTemplate = await getTemplate(route.params.id as string)
      form.value.name = tpl.name
      form.value.frequency = tpl.frequency
      form.value.next_due_date = tpl.next_due_date
      form.value.template_data = JSON.stringify(tpl.template_data, null, 2)
    } catch {
      message.error('加载模板失败')
      router.push('/recurring')
    } finally {
      loading.value = false
    }
  }
})

async function handleSave() {
  if (!form.value.name.trim()) {
    message.warning('请输入模板名称')
    return
  }
  if (!form.value.template_data.trim()) {
    message.warning('请输入模板数据 (JSON)')
    return
  }
  let parsed: Record<string, any>
  try {
    parsed = JSON.parse(form.value.template_data)
  } catch {
    message.warning('模板数据格式不正确，请输入有效 JSON')
    return
  }
  if (!form.value.next_due_date) {
    message.warning('请选择下次到期日期')
    return
  }

  saving.value = true
  try {
    const payload = {
      name: form.value.name.trim(),
      frequency: form.value.frequency,
      next_due_date: form.value.next_due_date,
      template_data: parsed,
    }
    if (isEdit) {
      await updateTemplate(route.params.id as string, payload)
      message.success('已更新')
    } else {
      await createTemplate(payload)
      message.success('已创建')
    }
    router.push('/recurring')
  } catch {
    message.error('保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="create-page">
    <div class="page-header">
      <div class="header-left">
        <NButton text class="back-btn" @click="router.push('/recurring')">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回
        </NButton>
        <div class="title-section">
          <h2>{{ isEdit ? '编辑' : '新建' }}周期性模板</h2>
          <p class="subtitle">配置并生成定期发生的周期性记账流水模板</p>
        </div>
      </div>
    </div>

    <NSpin :show="loading">
      <NCard :bordered="false" class="form-card">
        <NForm label-placement="top">
          <NFormItem label="模板名称">
            <NInput v-model:value="form.name" placeholder="如：每月房租" />
          </NFormItem>

          <NFormItem label="频率">
            <NSelect v-model:value="form.frequency" :options="frequencyOptions" />
          </NFormItem>

          <NFormItem label="下次到期日期">
            <NDatePicker
              v-model:formatted-value="form.next_due_date"
              value-format="yyyy-MM-dd"
              type="date"
              style="width: 100%"
            />
          </NFormItem>

          <NFormItem label="模板数据 (JSON)">
            <NInput
              v-model:value="form.template_data"
              type="textarea"
              :rows="10"
              placeholder='{
  "title": "房租",
  "amount": 5000,
  "direction": "expense",
  "category": "办公费用",
  "accountCode": "5603002"
}'
            />
          </NFormItem>

          <div class="form-actions">
            <NButton @click="router.push('/recurring')">取消</NButton>
            <NButton type="primary" :loading="saving" @click="handleSave">保存</NButton>
          </div>
        </NForm>
      </NCard>
    </NSpin>
  </div>
</template>

<style scoped>
.create-page {
  max-width: 640px;
  margin: 0 auto;
}
.page-header {
  margin-bottom: 16px;
}
.page-header h2 {
  margin: 8px 0 0;
  font-size: 20px;
  font-weight: 600;
}
.form-card {
  border-radius: var(--solo-radius-lg);
}
.form-actions {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
}
</style>
