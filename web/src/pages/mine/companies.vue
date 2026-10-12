<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { NCard, NButton, NModal, NForm, NFormItem, NInput, NInputNumber, NSelect, NTag, useMessage, NSpace, NEmpty, NGrid, NGi } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useCompanyStore } from '@/stores/company'
import { updateCompany as apiUpdateCompany } from '@/api/auth'
import { formatDate } from '@/utils/format'
import type { CompanyInfo } from '@/api/types'

const message = useMessage()
const authStore = useAuthStore()
const companyStore = useCompanyStore()

const companies = ref<CompanyInfo[]>([])
// Create dialog
const showCreate = ref(false)
const createForm = ref({ name: '', type: '有限责任公司', taxpayerType: '小规模纳税人' })
const creating = ref(false)

// Edit dialog
const showEdit = ref(false)
const editingId = ref('')
const editForm = ref({
  name: '', type: '有限责任公司', taxpayerType: '小规模纳税人', startDate: '', taxId: '',
  restrictedIndustry: 'no', employeeCount: 1,
})
const saving = ref(false)

const companyTypes = [
  { label: '有限责任公司', value: '有限责任公司' },
  { label: '个人独资企业', value: '个人独资企业' },
  { label: '个体工商户', value: '个体工商户' },
]

const taxpayerTypes = [
  { label: '小规模纳税人', value: '小规模纳税人' },
  { label: '一般纳税人', value: '一般纳税人' },
]

const yesNoOptions = [{ label: '是', value: 'yes' }, { label: '否', value: 'no' }]

onMounted(() => {
  loadCompanies()
})

function loadCompanies() {
  if (authStore.user) {
    companies.value = authStore.user.companies || []
    companyStore.setCompanies(companies.value)
  }
}

function isCurrent(id: string) {
  return id === authStore.currentCompanyId
}

async function handleSelect(co: CompanyInfo) {
  if (isCurrent(co.company_id)) return
  try {
    await authStore.switchCompanyContext(co.company_id)
    message.success(`已切换到 ${co.name}`)
  } catch {
    message.error('切换失败')
  }
}

async function handleCreate() {
  if (!createForm.value.name.trim()) {
    message.warning('请输入公司名称')
    return
  }
  creating.value = true
  try {
    await companyStore.create({
      ...createForm.value,
      startDate: new Date().toISOString().split('T')[0],
    })
    loadCompanies()
    showCreate.value = false
    createForm.value = { name: '', type: '有限责任公司', taxpayerType: '小规模纳税人' }
    message.success('创建成功')
  } catch {
    message.error('创建失败')
  } finally {
    creating.value = false
  }
}

function openEdit(co: CompanyInfo) {
  editingId.value = co.company_id
  editForm.value = {
    name: co.name || '',
    type: co.type || '有限责任公司',
    taxpayerType: co.taxpayerType || '小规模纳税人',
    startDate: co.startDate || formatDate(new Date(), 'YYYY-MM-DD'),
    taxId: co.taxId || '',
    restrictedIndustry: co.restrictedIndustry === true ? 'yes' : 'no',
    employeeCount: co.employeeCount ?? 1,
  }
  showEdit.value = true
}

async function handleSaveEdit() {
  if (!editForm.value.name.trim()) {
    message.warning('请输入公司名称')
    return
  }
  const rawTaxId = editForm.value.taxId.trim().toUpperCase()
  if (rawTaxId && !/^[0-9A-Z]{15}$|^[0-9A-Z]{17}$|^[0-9A-Z]{18}$|^[0-9A-Z]{20}$/.test(rawTaxId)) {
    message.warning('纳税人识别号格式不正确（15/17/18/20 位字母数字）')
    return
  }
  saving.value = true
  try {
    await apiUpdateCompany(editingId.value, {
      ...editForm.value,
      taxId: rawTaxId,
      restrictedIndustry: editForm.value.restrictedIndustry === 'yes',
    })
    // Refresh profile to get updated company list
    await authStore.fetchProfile()
    loadCompanies()
    showEdit.value = false
    message.success('已保存')
  } catch {
    message.error('保存失败')
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="companies-page">
    <div class="page-header">
      <div>
        <h2>主体管理</h2>
        <p class="text-muted">管理您的所有公司及纳税人身份</p>
      </div>
      <NButton type="primary" @click="showCreate = true">新增主体</NButton>
    </div>

    <NSpace vertical :size="16">
      <NCard
        v-for="co in companies"
        :key="co.company_id"
        :bordered="true"
        :class="{ 'active-card': isCurrent(co.company_id) }"
        hoverable
        @click="handleSelect(co)"
      >
        <div class="company-card">
          <div class="card-main">
            <div class="info">
              <h3 class="company-name">{{ co.name }}</h3>
              <div class="tags">
                <NTag size="small" :bordered="false">{{ co.type || '有限责任公司' }}</NTag>
                <NTag size="small" type="info" :bordered="false">{{ co.taxpayerType || '小规模纳税人' }}</NTag>
                <NTag v-if="isCurrent(co.company_id)" size="small" type="success" :bordered="false">当前使用</NTag>
              </div>
            </div>
          </div>
          <div class="card-footer">
            <span class="date">启用日期：{{ co.startDate || '—' }}</span>
            <NButton text type="primary" @click.stop="openEdit(co)">编辑</NButton>
          </div>
        </div>
      </NCard>
    </NSpace>

    <NEmpty v-if="companies.length === 0" description="暂无公司，点击右上角新增" class="empty" />

    <!-- Create Modal -->
    <NModal v-model:show="showCreate" title="新增主体" preset="card" style="width: 480px; max-width: 90vw;">
      <NForm>
        <NFormItem label="公司名称" required>
          <NInput v-model:value="createForm.name" placeholder="请输入公司全称" />
        </NFormItem>
        <NFormItem label="主体类型">
          <NSelect v-model:value="createForm.type" :options="companyTypes" />
        </NFormItem>
        <NFormItem label="纳税人类型">
          <NSelect v-model:value="createForm.taxpayerType" :options="taxpayerTypes" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showCreate = false">取消</NButton>
          <NButton type="primary" :loading="creating" @click="handleCreate">确认创建</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- Edit Modal -->
    <NModal v-model:show="showEdit" title="编辑主体" preset="card" style="width: 720px; max-width: 92vw;">
      <NForm>
        <NGrid :cols="2" :x-gap="16">
          <NGi :span="2">
            <NFormItem label="名称" required>
              <NInput v-model:value="editForm.name" placeholder="账本或公司全称" />
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem label="主体类型">
              <NSelect v-model:value="editForm.type" :options="companyTypes" />
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem label="纳税人类型">
              <NSelect v-model:value="editForm.taxpayerType" :options="taxpayerTypes" />
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem label="启用日期">
              <NInput v-model:value="editForm.startDate" placeholder="YYYY-MM-DD" />
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem label="纳税人识别号">
              <NInput v-model:value="editForm.taxId" maxlength="20" placeholder="选填，15/17/18/20 位字母数字" />
            </NFormItem>
          </NGi>
          <NGi :span="2">
            <div class="form-section-label">税务信息</div>
          </NGi>
          <NGi>
            <NFormItem label="当前从业人数">
              <NInputNumber v-model:value="editForm.employeeCount" :min="0" :precision="0" style="width: 100%" />
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem label="属于国家限制或禁止行业">
              <NSelect v-model:value="editForm.restrictedIndustry" :options="yesNoOptions" />
            </NFormItem>
          </NGi>
        </NGrid>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showEdit = false">取消</NButton>
          <NButton type="primary" :loading="saving" @click="handleSaveEdit">保存</NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.companies-page {
  max-width: 720px;
  margin: 0 auto;
}
.form-section-label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--solo-text-muted);
  letter-spacing: 0.5px;
  padding: 2px 0 0 2px;
}
.page-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  margin-bottom: 24px;
}
.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.text-muted {
  color: var(--solo-text-secondary);
  font-size: 13px;
  margin-top: 4px;
}
.company-card {
  cursor: pointer;
}
.active-card :deep(.n-card) {
  border-color: var(--solo-primary);
  background: #f0f7ff;
}
.card-main {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
}
.company-name {
  margin: 0 0 8px;
  font-size: 16px;
  font-weight: 600;
}
.tags {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.card-footer {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 16px;
  padding-top: 12px;
  border-top: 1px solid var(--solo-border-light);
}
.date {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.empty {
  margin-top: 48px;
}
</style>
