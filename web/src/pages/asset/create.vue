<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NButton, NForm, NFormItem, NInput, NInputNumber, NSelect, NSpace, useMessage, NDatePicker, NGrid, NGridItem } from 'naive-ui'
import { createFixedAsset } from '@/api/asset'
import { formatMoney } from '@/utils/format'
import type { FixedAssetCreateRequest } from '@/api/types'
import type { RdProject } from '@/api/types'
import { getRdProjects } from '@/api/rd'

const router = useRouter()
const message = useMessage()

const submitting = ref(false)
const rdProjects = ref<RdProject[]>([])
const form = ref<FixedAssetCreateRequest>({
  name: '',
  originalValue: 0,
  purchaseDate: '',
  category: '电子设备',
  usefulLife: 3,
  residualValueRate: 0.05,
  oneTimeDepreciation: false,
  rdUseRate: 0,
})

const categoryOptions = [
  { label: '房屋建筑物 (折旧20年)', value: '房屋建筑物' },
  { label: '机器设备 (折旧10年)', value: '机器设备' },
  { label: '运输工具 (折旧4年)', value: '运输工具' },
  { label: '电子设备 (折旧3年)', value: '电子设备' },
  { label: '办公家具/器具 (折旧5年)', value: '办公家具' },
  { label: '其他固定资产', value: '其他' },
]

const rdProjectOptions = ref<{ label: string; value: string }[]>([])

onMounted(async () => {
  try {
    rdProjects.value = await getRdProjects()
    rdProjectOptions.value = rdProjects.value
      .filter((project) => project.status === 'active')
      .map((project) => ({ label: `${project.code} ${project.name}`, value: project.id }))
  } catch {
    rdProjects.value = []
  }
})

function handleCategoryChange(val: string) {
  const map: Record<string, number> = {
    '房屋建筑物': 20,
    '机器设备': 10,
    '运输工具': 4,
    '电子设备': 3,
    '办公家具': 5,
    '其他': 5,
  }
  if (val in map) {
    form.value.usefulLife = map[val]
  }
}

async function handleSubmit() {
  if (!form.value.name.trim()) {
    message.warning('请输入资产名称')
    return
  }
  if (!form.value.category) {
    message.warning('请选择资产类别')
    return
  }
  if (form.value.originalValue <= 0) {
    message.warning('请输入大于零的资产原值')
    return
  }
  if (!form.value.purchaseDate) {
    message.warning('请选择购置日期')
    return
  }
  if ((form.value.rdUseRate || 0) > 0 && (!form.value.rdProjectId || !form.value.rdAllocationBasis?.trim())) {
    message.warning('研发使用资产必须选择研发项目并填写分摊依据')
    return
  }
  submitting.value = true
  try {
    await createFixedAsset(form.value)
    message.success('资产录入成功，已开始追踪折旧')
    router.push('/assets')
  } catch {
    message.error('录入失败，请检查输入项')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="create-page">
    <div class="page-header">
      <div class="header-left">
        <NButton text class="back-btn" @click="router.push('/assets')">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回
        </NButton>
        <div class="title-section">
          <h2>录入固定资产</h2>
          <p class="subtitle">录入新购置的固定资产，设定折旧方案。购置后次月起自动按月计提折旧。</p>
        </div>
      </div>
    </div>

    <NCard :bordered="false" class="form-card">
      <NForm label-placement="top" class="asset-form">
        <NGrid :cols="2" :x-gap="20">
          <NGridItem :span="2">
            <NFormItem label="资产名称">
              <NInput v-model:value="form.name" placeholder="如：MacBook Pro 电脑、行政部空调、外勤货车" />
            </NFormItem>
          </NGridItem>

          <NGridItem :span="1">
            <NFormItem label="资产类别">
              <NSelect 
                v-model:value="form.category" 
                :options="categoryOptions" 
                @update:value="handleCategoryChange"
              />
              <template #feedback>
                <span class="form-tip">自动根据类别关联资产折旧科目与预设年限。</span>
              </template>
            </NFormItem>
          </NGridItem>

          <NGridItem :span="2">
            <div class="rd-allocation-box">
              <div class="calc-title">研发使用归集（选填）</div>
              <NGrid :cols="2" :x-gap="16">
                <NGridItem>
                  <NFormItem label="研发项目">
                    <NSelect v-model:value="form.rdProjectId" :options="rdProjectOptions" clearable filterable placeholder="资产未用于研发可不选" />
                  </NFormItem>
                </NGridItem>
                <NGridItem>
                  <NFormItem label="研发使用比例（0-1）">
                    <NInputNumber v-model:value="form.rdUseRate" :min="0" :max="1" :step="0.1" style="width:100%" placeholder="例如 0.6 表示60%" />
                  </NFormItem>
                </NGridItem>
                <NGridItem :span="2">
                  <NFormItem label="分摊依据">
                    <NInput v-model:value="form.rdAllocationBasis" placeholder="例如：设备使用日志统计，研发项目使用时长占60%" />
                  </NFormItem>
                </NGridItem>
              </NGrid>
            </div>
          </NGridItem>

          <NGridItem :span="1">
            <NFormItem label="购置日期">
              <NDatePicker
                v-model:formatted-value="form.purchaseDate"
                value-format="yyyy-MM-dd"
                type="date"
                style="width: 100%;"
                placeholder="选择购置入账日期"
              />
              <template #feedback>
                <span class="form-tip">资产自购置日期的<b>次月</b>起开始自动计提折旧。</span>
              </template>
            </NFormItem>
          </NGridItem>

          <NGridItem :span="1">
            <NFormItem label="资产原值（元）">
              <NInputNumber 
                v-model:value="form.originalValue" 
                :min="0.01" 
                :step="1000" 
                style="width: 100%;" 
                placeholder="0.00"
              />
              <template #feedback>
                <span class="form-tip">固定资产的实际采购/购置含税成本。</span>
              </template>
            </NFormItem>
          </NGridItem>

          <NGridItem :span="1">
            <NFormItem label="使用年限（年）">
              <NInputNumber v-model:value="form.usefulLife" :min="1" :max="50" style="width: 100%;" />
              <template #feedback>
                <span class="form-tip">税法规定：电子设备3年，工具运输4-5年，房屋20年。</span>
              </template>
            </NFormItem>
          </NGridItem>

          <NGridItem :span="1">
            <NFormItem label="净残值率">
              <NInputNumber 
                v-model:value="form.residualValueRate" 
                :min="0" 
                :max="0.5" 
                :step="0.01" 
                style="width: 100%;" 
              />
              <template #feedback>
                <span class="form-tip">预计报废时的残余价值比率，通常默认为 <b>5%</b>。</span>
              </template>
            </NFormItem>
          </NGridItem>

          <NGridItem :span="1">
            <div class="calculation-preview-box">
              <div class="calc-title">预估折旧测算</div>
              <div class="calc-row">
                <span class="calc-label">应折旧总额:</span>
                <span class="calc-val">{{ formatMoney(form.originalValue * (1 - (form.residualValueRate ?? 0.05))) }}</span>
              </div>
              <div class="calc-row">
                <span class="calc-label">预计月折旧额:</span>
                <span class="calc-val highlighted-val">
                  {{ form.usefulLife > 0 ? formatMoney((form.originalValue * (1 - (form.residualValueRate ?? 0.05))) / (form.usefulLife * 12)) : '¥0.00' }}
                </span>
              </div>
            </div>
          </NGridItem>
        </NGrid>
      </NForm>

      <div class="form-footer">
        <NSpace justify="end" size="medium">
          <NButton @click="router.push('/assets')">取消</NButton>
          <NButton type="primary" :loading="submitting" @click="handleSubmit">
            确认录入资产
          </NButton>
        </NSpace>
      </div>
    </NCard>
  </div>
</template>

<style scoped>
.create-page {
  max-width: 640px;
  margin: 0 auto;
  padding: 16px 0 48px 0;
}
.page-header {
  margin-bottom: 24px;
}
.back-btn {
  font-weight: 500;
  color: var(--solo-text-secondary);
  margin-bottom: 8px;
}
.back-btn:hover {
  color: var(--solo-primary);
}
.page-header h2 {
  margin: 4px 0 0 0;
  font-size: 24px;
  font-weight: 700;
  color: var(--solo-text-primary);
  letter-spacing: -0.5px;
}
.subtitle {
  margin: 6px 0 0 0;
  font-size: 13.5px;
  color: var(--solo-text-secondary);
  line-height: 1.4;
}
.form-card {
  background: var(--solo-bg-card);
  backdrop-filter: blur(12px);
  border: 1px solid var(--solo-border-glass);
  border-radius: var(--solo-radius-lg);
  box-shadow: var(--solo-shadow-lg);
  padding: 8px;
}
.asset-form {
  margin-top: 8px;
}
.rd-allocation-box {
  margin: 4px 0 16px;
  padding: 16px;
  border: 1px solid rgba(99, 102, 241, 0.14);
  border-radius: var(--solo-radius);
  background: rgba(99, 102, 241, 0.035);
}
.form-tip {
  font-size: 11px;
  color: var(--solo-text-muted);
  line-height: 1.3;
}
.form-tip b {
  color: var(--solo-primary);
  font-weight: 600;
}

/* Calculation Preview Box */
.calculation-preview-box {
  background: rgba(99, 102, 241, 0.03);
  border: 1px solid rgba(99, 102, 241, 0.08);
  border-radius: var(--solo-radius);
  padding: 12px 16px;
  height: calc(100% - 24px);
  display: flex;
  flex-direction: column;
  justify-content: center;
  gap: 8px;
}
.calc-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--solo-primary);
  margin-bottom: 2px;
}
.calc-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12.5px;
}
.calc-label {
  color: var(--solo-text-secondary);
}
.calc-val {
  font-weight: 600;
  color: var(--solo-text-primary);
  font-family: 'Outfit', 'Inter', sans-serif;
}
.highlighted-val {
  color: var(--solo-income);
  font-size: 14.5px;
  font-weight: 800;
}

.form-footer {
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px dashed var(--solo-border-light);
}
</style>
