<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NCard, NButton, NModal, NForm, NFormItem, NInput, NSelect, NTag, NPopover, NTooltip, NDatePicker, useMessage, NSpace, NGrid, NGi } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useCompanyStore } from '@/stores/company'
import { updateCompany as apiUpdateCompany, deleteCompany as apiDeleteCompany } from '@/api/auth'
import { listTaxRegistrations, type TaxRegistrationRow } from '@/api/tax-registration'
import { reportErrorToast } from '@/utils/errorPresenter'
import { restrictedIndustryFor, filterIndustryGroups } from '@/utils/industryTax'
import {
  companyCreateLandingPath,
  parseCompanyCreateQuery,
  shouldSwitchAfterCreate,
} from '@/utils/companyOnboarding'
import {
  getIndustryTemplates,
  type IndustryTemplateItem,
} from '@/api/industryTemplates'
import type { CompanyInfo } from '@/api/types'
import { SoloEmpty } from '@/components/ui'

const message = useMessage()
const route = useRoute()
const router = useRouter()
const authStore = useAuthStore()
const companyStore = useCompanyStore()

const companies = ref<CompanyInfo[]>([])
// Create dialog
function defaultCreateForm() {
  return {
    onboardingType: 'new' as 'new' | 'migration',
    name: '', type: '有限责任公司', taxpayerType: '小规模纳税人',
    // formatted-value 的「无值」哨兵是 null，空字符串会被当作待解析日期导致弹窗崩溃
    startDate: null as string | null,
    taxId: '',
    restrictedIndustry: 'no', industry: '', industryCode: '',
    industryTouched: false, legacyIndustryName: '',
  }
}
const showCreate = ref(false)
const createForm = ref(defaultCreateForm())
const creating = ref(false)
// 从建账引导（?create=new|migration）进入：创建后要切到新主体并去下一步，
// 与页面右上角「新增主体」的既有行为区分开。
const createFromOnboarding = ref(false)

interface CreateSuccessState {
  companyId: string
  name: string
  onboardingType: 'new' | 'migration'
  switched: boolean
}
const createSuccessState = ref<CreateSuccessState | null>(null)

function handleNextAction(path: string) {
  closeSuccessModal()
  router.push(path)
}

function closeSuccessModal() {
  showCreate.value = false
  createSuccessState.value = null
}

// Edit dialog
const showEdit = ref(false)
const editingId = ref('')
const editForm = ref({
  onboardingType: 'new' as 'new' | 'migration',
  name: '', type: '有限责任公司', taxpayerType: '小规模纳税人',
  startDate: null as string | null,
  taxId: '',
  restrictedIndustry: 'no', industry: '', industryCode: '', vatFilingCycle: '' as string,
  businessIncomeIitFilingCycle: '' as string,
  industryTouched: false, legacyIndustryName: '',
})
const saving = ref(false)

const companyTypes = [
  { label: '有限责任公司', value: '有限责任公司' },
  { label: '个人独资企业', value: '个人独资企业' },
  { label: '个体工商户', value: '个体工商户' },
]

/** 选定行业后自动判定「是否限制/禁止行业」（不再暴露手动选择框）。 */
function applyIndustryDefault(target: 'create' | 'edit', industryCode: string | null) {
  const code = (industryCode ?? '').trim()
  const template = industryTemplates.value.find(item => item.code === code)
  if (target === 'create') {
    createForm.value.industryCode = template?.code || ''
    createForm.value.industry = template?.name || ''
    createForm.value.legacyIndustryName = ''
    createForm.value.restrictedIndustry = template?.restrictedDefault ? 'yes' : 'no'
  } else {
    editForm.value.industryCode = template?.code || ''
    editForm.value.industry = template?.name || ''
    editForm.value.legacyIndustryName = ''
    editForm.value.restrictedIndustry = template?.restrictedDefault ? 'yes' : 'no'
  }
}

// naive-ui filterable 搜索后点选存在渲染缺陷：值已写入表单但父组件不重渲染，选择框显示空白。
// 实测点击选项后 toggle 任意父级 ref 都能让渲染恢复，故用哨兵计数器在每次选择后强制重渲染。
const industrySelectSync = ref(0)

function onIndustryChange(target: 'create' | 'edit', industryCode: string | null) {
  industrySelectSync.value++
  if (target === 'create') createForm.value.industryTouched = true
  else editForm.value.industryTouched = true
  applyIndustryDefault(target, industryCode)
}

function defaultRestrictedFor(target: 'create' | 'edit'): 'yes' | 'no' {
  const form = target === 'create' ? createForm.value : editForm.value
  const template = industryTemplates.value.find(item => item.code === form.industryCode)
  if (template) return template.restrictedDefault ? 'yes' : 'no'
  return restrictedIndustryFor(form.industry)
}

/** 恢复模板默认值（不改变用户已选择的行业本身）。 */
function restoreIndustryDefault(target: 'create' | 'edit') {
  if (target === 'create') createForm.value.restrictedIndustry = defaultRestrictedFor('create')
  else editForm.value.restrictedIndustry = defaultRestrictedFor('edit')
}

function bingSearchUrl(name: string) {
  return `https://cn.bing.com/search?q=${encodeURIComponent(name.trim())}`
}

type IndustryGroup = { label: string; children: string[]; hidden?: boolean }
const industryTemplates = ref<IndustryTemplateItem[]>([])
const industryLoading = ref(false)
const industryGroups = computed<IndustryGroup[]>(() => {
  const grouped = new Map<string, { children: IndustryTemplateItem[]; hidden: boolean }>()
  for (const item of industryTemplates.value) {
    const current = grouped.get(item.groupName) || { children: [], hidden: true }
    current.children.push(item)
    current.hidden = current.hidden && item.hidden
    grouped.set(item.groupName, current)
  }
  return [...grouped.entries()].map(([label, group]) => ({
    label,
    hidden: group.hidden,
    children: group.children
      .sort((a, b) => a.sortOrder - b.sortOrder)
      .map(item => item.name),
  }))
})

// 「显示全部行业」开关（新建/编辑共用）；默认只展示小微企业常见行业
const showAllIndustries = ref(false)

function buildIndustryOptions(selectedCode?: string) {
  const selectedTemplate = industryTemplates.value.find(item => item.code === selectedCode)
  const selectedName = selectedTemplate?.name || ''
  return [
    ...filterIndustryGroups(industryGroups.value, {
      showAll: showAllIndustries.value,
      selected: selectedName,
    }).map((g) => ({
      type: 'group' as const,
      label: g.label,
      key: g.label,
      children: g.children
        .map((name) => industryTemplates.value.find(candidate => candidate.name === name))
        .filter((item): item is IndustryTemplateItem => Boolean(item))
        .filter(item => showAllIndustries.value || !item.hidden || item.code === selectedCode)
        .map(item => ({ label: item.name, value: item.code })),
    })),
  ].filter(group => group.children.length > 0)
}

const createIndustryOptions = computed(() => buildIndustryOptions(createForm.value.industryCode))
const editIndustryOptions = computed(() => buildIndustryOptions(editForm.value.industryCode))

async function loadIndustryTemplates() {
  industryLoading.value = true
  try {
    industryTemplates.value = (await getIndustryTemplates()).items
    if (showEdit.value && !editForm.value.industryTouched) {
      const target = companies.value.find(co => co.company_id === editingId.value)
      if (target) syncEditIndustryFromCompany(target)
    }
  } catch {
    message.warning('行业模板加载失败，暂时保留当前已选行业')
  } finally {
    industryLoading.value = false
  }
}

// 税费种认定表同步后，申报周期由认定表接管：这两个手填字段降级为「未同步时的兜底」，
// 留着可编辑只会让用户以为改这里能改征期，实际改了不生效。
// 接管判定与后端 effective_vat_cycle / effective_iit_cycle 对齐：按申报表组、
// 只认生效行里的月/季周期，避免「只录了个税认定时增值税字段也被禁用」的错位。
function groupCycleSynced(rows: TaxRegistrationRow[], group: string): boolean {
  return rows.some(
    (row) =>
      row.status === 'active' &&
      row.returnGroup === group &&
      (row.filingCycle === 'month' || row.filingCycle === 'quarter'),
  )
}
const vatCycleSynced = ref(false)
const iitCycleSynced = ref(false)

onMounted(async () => {
  loadCompanies()
  await Promise.all([
    loadIndustryTemplates(),
    (async () => {
      try {
        const result = await listTaxRegistrations()
        vatCycleSynced.value = groupCycleSynced(result.items, 'vat')
        iitCycleSynced.value = groupCycleSynced(result.items, 'business_income_iit_prepay')
      } catch {
        // 认定表读取失败不影响主体信息编辑，仅退化为可编辑
      }
    })(),
  ])
  // 深链 ?edit=<companyId>：供启用日期校验的引导弹窗直达编辑表单
  const editId = typeof route.query.edit === 'string' ? route.query.edit : ''
  if (editId) {
    const target = companies.value.find((co) => co.company_id === editId)
    if (target) openEdit(target)
  }
  applyCreateQuery()
})

// 页面会被 KeepAlive 缓存：从对话页再次带着 ?create= 进来时不会重新 mount，需监听 query
watch(
  () => route.query.create,
  () => applyCreateQuery(),
)

// 弹窗关闭（取消/完成）后复位建账引导标记，避免下次从「新增主体」按钮进来时
// 仍被当成引导入口，创建完又把用户带去建账落点。已填内容保留，取消不丢输入。
watch(showCreate, (visible) => {
  if (visible) return
  createFromOnboarding.value = false
  createForm.value.onboardingType = 'new'
  createSuccessState.value = null
})

/** 对话页建账卡片 / 首次引导的深链：直接打开「新增主体」并预设建账场景。 */
function applyCreateQuery() {
  const intent = parseCompanyCreateQuery(route.query.create)
  if (!intent) return
  createForm.value.onboardingType = intent
  createFromOnboarding.value = true
  showCreate.value = true
  // 消费一次即清掉参数，避免刷新或返回时反复弹窗
  const restQuery = { ...route.query }
  delete restQuery.create
  router.replace({ path: route.path, query: restQuery })
}

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
  if (creating.value) return
  if (!createForm.value.name.trim()) {
    message.warning('请输入公司名称')
    return
  }
  // 税号是申报作业包和报表报送的必填项，建账时就要求填全，避免建完再补、报税时被拦。
  if (!createForm.value.taxId.trim()) {
    message.warning('请输入纳税人识别号')
    return
  }
  if (!createForm.value.startDate) {
    message.warning('请填写启用日期（账套从此日开始记账）')
    return
  }
  const rawTaxId = createForm.value.taxId.trim().toUpperCase()
  if (!/^[0-9A-Z]{15}$|^[0-9A-Z]{17}$|^[0-9A-Z]{18}$|^[0-9A-Z]{20}$/.test(rawTaxId)) {
    message.warning('纳税人识别号格式不正确（15/17/18/20 位字母数字）')
    return
  }
  await submitCreate(rawTaxId)
}

async function submitCreate(rawTaxId: string) {
  const wasDemo = authStore.companies.find((co) => co.company_id === authStore.currentCompanyId)?.isDemo === true
  const fromOnboarding = createFromOnboarding.value
  const onboardingType = createForm.value.onboardingType
  // 落点要带启用月，重置表单前先留住
  const startDate = createForm.value.startDate
  const needSwitch = shouldSwitchAfterCreate(wasDemo, fromOnboarding)
  creating.value = true
  try {
    const company = await companyStore.create({
      onboardingType: createForm.value.onboardingType,
      name: createForm.value.name,
      type: createForm.value.type,
      taxpayerType: createForm.value.taxpayerType,
      taxId: rawTaxId,
      restrictedIndustry: createForm.value.restrictedIndustry === 'yes',
      startDate: createForm.value.startDate || undefined,
      industryCode: createForm.value.industryCode || undefined,
    })
    let switched = false
    if (needSwitch) {
      try {
        await authStore.switchCompanyContext(company.company_id)
        switched = true
      } catch {
        // 主体已经建好了，切换失败不能再报「创建失败」；提示用户手动切换
        message.warning('主体已创建，但自动切换失败，请在主体列表里手动切换')
      }
    }
    if (!needSwitch || switched) loadCompanies()
    message.success(wasDemo && switched ? '已创建正式账套并切换生效' : '创建成功')
    if (fromOnboarding && (!needSwitch || switched)) {
      showCreate.value = false
      createForm.value = defaultCreateForm()
      createFromOnboarding.value = false
      await router.push(companyCreateLandingPath(onboardingType, startDate))
    } else {
      createSuccessState.value = {
        companyId: company.company_id,
        name: company.name,
        onboardingType,
        switched,
      }
      createForm.value = defaultCreateForm()
    }
  } catch (err) {
    reportErrorToast(message, err, '创建失败')
  } finally {
    creating.value = false
  }
}

function syncEditIndustryFromCompany(co: CompanyInfo) {
  const template = industryTemplates.value.find(item =>
    (co.industryCode && item.code === co.industryCode)
    || (!co.industryCode && co.industry && item.name === co.industry),
  )
  editForm.value.industryCode = template?.code || ''
  editForm.value.industry = template?.name || co.industry || ''
  editForm.value.legacyIndustryName = !template && co.industry ? co.industry : ''
  if (co.restrictedIndustry === undefined || co.restrictedIndustry === null) {
    editForm.value.restrictedIndustry = template
      ? (template.restrictedDefault ? 'yes' : 'no')
      : restrictedIndustryFor(co.industry)
  }
}

function openEdit(co: CompanyInfo) {
  editingId.value = co.company_id
  editForm.value = {
    onboardingType: ((co as any).onboardingType === 'migration' ? 'migration' : 'new'),
    name: co.name || '',
    type: co.type || '有限责任公司',
    taxpayerType: co.taxpayerType || '小规模纳税人',
    startDate: co.startDate || null,
    taxId: co.taxId || '',
    restrictedIndustry: co.restrictedIndustry === undefined || co.restrictedIndustry === null
      ? restrictedIndustryFor(co.industry)
      : (co.restrictedIndustry ? 'yes' : 'no'),
    industry: co.industry || '',
    industryCode: '',
    vatFilingCycle: co.vatFilingCycle || '',
    businessIncomeIitFilingCycle: co.businessIncomeIitFilingCycle || '',
    industryTouched: false,
    legacyIndustryName: '',
  }
  syncEditIndustryFromCompany(co)
  showEdit.value = true
}

async function handleSaveEdit() {
  if (!editForm.value.name.trim()) {
    message.warning('请输入公司名称')
    return
  }
  if (!editForm.value.taxId.trim()) {
    message.warning('请输入纳税人识别号')
    return
  }
  if (!editForm.value.startDate) {
    message.warning('请填写启用日期（账套从此日开始记账）')
    return
  }
  const rawTaxId = editForm.value.taxId.trim().toUpperCase()
  if (rawTaxId && !/^[0-9A-Z]{15}$|^[0-9A-Z]{17}$|^[0-9A-Z]{18}$|^[0-9A-Z]{20}$/.test(rawTaxId)) {
    message.warning('纳税人识别号格式不正确（15/17/18/20 位字母数字）')
    return
  }
  saving.value = true
  try {
    const industryChange = editForm.value.industryTouched
      ? { industryCode: editForm.value.industryCode || '' }
      : {}
    await apiUpdateCompany(editingId.value, {
      name: editForm.value.name,
      type: editForm.value.type,
      taxpayerType: editForm.value.taxpayerType,
      taxId: rawTaxId,
      restrictedIndustry: editForm.value.restrictedIndustry === 'yes',
      startDate: editForm.value.startDate || undefined,
      vatFilingCycle: editForm.value.vatFilingCycle || undefined,
      businessIncomeIitFilingCycle: editForm.value.businessIncomeIitFilingCycle || undefined,
      ...industryChange,
    })
    // Refresh profile to get updated company list
    await authStore.fetchProfile()
    loadCompanies()
    showEdit.value = false
    message.success('已保存')
  } catch (err) {
    reportErrorToast(message, err, '保存失败')
  } finally {
    saving.value = false
  }
}

// ---- 删除主体：三步确认（风险提示 → 输入名称 → 最终确认） ----
const showDelete = ref(false)
const deleteTarget = ref<CompanyInfo | null>(null)
const deleteStep = ref(1)
const deleteNameInput = ref('')
const deleting = ref(false)

const deleteNameMatches = computed(
  () => (deleteTarget.value?.name || '').trim() === deleteNameInput.value.trim(),
)

function openDelete(co: CompanyInfo) {
  if (isCurrent(co.company_id)) {
    message.warning('请先切换到其他主体，再删除当前主体')
    return
  }
  deleteTarget.value = co
  deleteStep.value = 1
  deleteNameInput.value = ''
  showDelete.value = true
}

async function confirmDelete() {
  const target = deleteTarget.value
  if (!target) return
  deleting.value = true
  try {
    await apiDeleteCompany(target.company_id, deleteNameInput.value.trim())
    showDelete.value = false
    deleteTarget.value = null
    message.success(`主体「${target.name}」已永久删除`)
    // 重新拉档案后必须回填页面本地列表：这里的 companies 是 fetchProfile 之前
    // 拷来的旧数组，不重取就还指着已删除的卡片，要整页刷新才消失。
    await authStore.fetchProfile()
    loadCompanies()
    if (authStore.companies.length === 0) {
      message.info('当前已无主体，请新建一个主体后继续使用')
    }
  } catch (err) {
    reportErrorToast(message, err, '删除失败')
  } finally {
    deleting.value = false
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
      <NSpace>
        <NButton type="primary" @click="showCreate = true">新增主体</NButton>
      </NSpace>
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
          <div class="card-actions" @click.stop>
            <NTooltip v-if="!co.isDemo" trigger="hover">
              <template #trigger>
                <span class="icon-wrap">
                  <NButton
                    quaternary
                    circle
                    size="small"
                    class="icon-action"
                    aria-label="编辑主体"
                    @click.stop="openEdit(co)"
                  >
                    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                      <path d="M12 20h9" />
                      <path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
                    </svg>
                  </NButton>
                </span>
              </template>
              编辑主体
            </NTooltip>

            <NTooltip trigger="hover">
              <template #trigger>
                <span class="icon-wrap">
                  <NButton
                    quaternary
                    circle
                    size="small"
                    class="icon-action is-danger"
                    :disabled="isCurrent(co.company_id)"
                    aria-label="删除主体"
                    @click.stop="openDelete(co)"
                  >
                    <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
                      <path d="M3 6h18" />
                      <path d="M8 6V4h8v2" />
                      <path d="M19 6l-1 14H6L5 6" />
                      <path d="M10 11v5M14 11v5" />
                    </svg>
                  </NButton>
                </span>
              </template>
              {{ isCurrent(co.company_id) ? '当前主体需先切换' : '删除主体' }}
            </NTooltip>
          </div>

          <div class="card-main">
            <div class="info">
              <h3 class="company-name">{{ co.name }}</h3>
              <div class="tags">
                <NTag size="small" :bordered="false">{{ co.type || '有限责任公司' }}</NTag>
                <NTag v-if="co.industry" size="small" :bordered="false">{{ co.industry }}</NTag>
                <NTag size="small" type="info" :bordered="false">{{ co.taxpayerType || '小规模纳税人' }}</NTag>
                <NTag v-if="co.isDemo" size="small" type="warning" :bordered="false">演示账套</NTag>
                <NTag v-if="isCurrent(co.company_id)" size="small" type="success" :bordered="false">当前使用</NTag>
              </div>
            </div>
          </div>
          <div class="card-footer">
            <span class="date">启用日期：{{ co.startDate || '—' }}</span>
            <span v-if="co.isDemo" class="date">演示账套仅供体验，不可修改主体信息</span>
          </div>
        </div>
      </NCard>
    </NSpace>

    <SoloEmpty
      v-if="companies.length === 0"
      title="还没有主体"
      description="先建一个主体（公司 / 个体户），所有账都记在它名下。点右上角「新增主体」开始。"
      icon="ri-building-line"
      class="empty"
    />

    <!-- Delete Modal：三步确认 -->
    <NModal
      v-model:show="showDelete"
      preset="card"
      title="删除主体"
      style="width: 560px; max-width: 92vw;"
      :mask-closable="false"
    >
      <div v-if="deleteTarget" class="delete-body">
        <div class="delete-steps">
          <span :class="{ active: deleteStep >= 1 }">1 风险确认</span>
          <span :class="{ active: deleteStep >= 2 }">2 输入名称</span>
          <span :class="{ active: deleteStep >= 3 }">3 最终确认</span>
        </div>

        <template v-if="deleteStep === 1">
          <p class="delete-warning">
            即将永久删除主体「{{ deleteTarget.name }}」，以下数据会一并删除，且<strong>无法恢复</strong>：
          </p>
          <ul class="delete-list">
            <li>全部账目、凭证、期初余额与财务报表</li>
            <li>银行流水、发票、工资社保、固定资产与折旧</li>
            <li>税务作业包、征期记录、待办与 AI 草稿</li>
            <li>公司画像、成员关系、MCP 访问令牌与调用日志</li>
          </ul>
        </template>

        <template v-else-if="deleteStep === 2">
          <p class="delete-warning">请输入主体名称以确认删除：</p>
          <p class="delete-name-hint">{{ deleteTarget.name }}</p>
          <NInput v-model:value="deleteNameInput" placeholder="请输入完整主体名称" />
        </template>

        <template v-else>
          <p class="delete-final">
            最后确认：主体「{{ deleteTarget.name }}」删除后不可恢复，确定继续吗？
          </p>
        </template>
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showDelete = false">取消</NButton>
          <NButton v-if="deleteStep > 1" @click="deleteStep -= 1">上一步</NButton>
          <NButton v-if="deleteStep === 1" type="error" @click="deleteStep = 2">继续</NButton>
          <NButton
            v-else-if="deleteStep === 2"
            type="error"
            :disabled="!deleteNameMatches"
            @click="deleteStep = 3"
          >
            下一步
          </NButton>
          <NButton v-else type="error" :loading="deleting" @click="confirmDelete">永久删除</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- Create Modal -->
    <NModal
      v-model:show="showCreate"
      :title="createSuccessState ? '主体创建成功' : '新增主体'"
      preset="card"
      style="width: 720px; max-width: 92vw;"
      :mask-closable="!creating"
    >
      <!-- 若刚刚创建成功，展示清晰的下一步行动卡 -->
      <div v-if="createSuccessState" class="create-success-panel">
        <div class="create-success-header">
          <i class="ri-checkbox-circle-fill success-icon" aria-hidden="true" />
          <div class="success-header-text">
            <h3>「{{ createSuccessState.name }}」已创建完成</h3>
            <p>
              {{ createSuccessState.switched ? '当前账套已自动切换至该主体。' : '' }}
              每一步明确推进，为你规划接下来的推荐操作：
            </p>
          </div>
        </div>

        <div class="next-step-cards">
          <!-- 新成立公司：主推税费种认定，备选工作台记账 -->
          <template v-if="createSuccessState.onboardingType === 'new'">
            <div class="next-step-card primary-card" role="button" tabindex="0" @click="handleNextAction('/tax-registration')">
              <div class="step-card-badge">推荐第一步</div>
              <div class="step-card-content">
                <i class="ri-government-line step-icon" aria-hidden="true" />
                <div class="step-text">
                  <strong>同步电子税务局税费种认定</strong>
                  <span>对接税局认定表，自动生成精准的申报日历与税种清单（支持复制粘贴或截图识别）</span>
                </div>
              </div>
              <NButton type="primary" size="small">立即前往同步</NButton>
            </div>

            <div class="next-step-card secondary-card" role="button" tabindex="0" @click="handleNextAction('/')">
              <div class="step-card-content">
                <i class="ri-dashboard-line step-icon" aria-hidden="true" />
                <div class="step-text">
                  <strong>先去工作台记账 / 上传流水</strong>
                  <span>系统已按小规模纳税人标准税种自动兜底，可随时开始日常记账或对账</span>
                </div>
              </div>
              <NButton secondary size="small">进入工作台</NButton>
            </div>
          </template>

          <!-- 已记账公司（代账转入）：主推导入期初，备选税费种认定 -->
          <template v-else>
            <div class="next-step-card primary-card" role="button" tabindex="0" @click="handleNextAction('/mine/opening')">
              <div class="step-card-badge">推荐第一步</div>
              <div class="step-card-content">
                <i class="ri-exchange-box-line step-icon" aria-hidden="true" />
                <div class="step-text">
                  <strong>导入期初科目余额表</strong>
                  <span>上传上一家代账软件导出的科目余额表 Excel，保证新老账目无缝衔接</span>
                </div>
              </div>
              <NButton type="primary" size="small">去导入期初余额</NButton>
            </div>

            <div class="next-step-card secondary-card" role="button" tabindex="0" @click="handleNextAction('/tax-registration')">
              <div class="step-card-content">
                <i class="ri-government-line step-icon" aria-hidden="true" />
                <div class="step-text">
                  <strong>同步税费种认定</strong>
                  <span>核对税局认定的税种与申报周期</span>
                </div>
              </div>
              <NButton secondary size="small">去税费种认定</NButton>
            </div>
          </template>
        </div>
      </div>

      <template v-else>
        <NForm>
          <NGrid :cols="2" :x-gap="16">
            <NGi :span="2">
              <NFormItem label="第一步：选择建账情况">
                <!-- 只有两个场景，直接平铺成卡片：少一次展开，也把两条路的差别写在同一屏 -->
                <div class="onboarding-choice" role="radiogroup" aria-label="建账情况">
                  <button
                    type="button"
                    role="radio"
                    class="onboarding-option"
                    :class="{ active: createForm.onboardingType === 'new' }"
                    :aria-checked="createForm.onboardingType === 'new'"
                    @click="createForm.onboardingType = 'new'"
                  >
                    <i class="ri-seedling-line" aria-hidden="true" />
                    <span class="onboarding-copy">
                      <strong>新成立公司</strong>
                      <span>从零开始记账，无历史期初余额</span>
                    </span>
                    <i class="ri-checkbox-circle-fill onboarding-check" aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    role="radio"
                    class="onboarding-option"
                    :class="{ active: createForm.onboardingType === 'migration' }"
                    :aria-checked="createForm.onboardingType === 'migration'"
                    @click="createForm.onboardingType = 'migration'"
                  >
                    <i class="ri-exchange-box-line" aria-hidden="true" />
                    <span class="onboarding-copy">
                      <strong>已记账公司</strong>
                      <span>从其他代账或财务软件转入，需导入期初余额</span>
                    </span>
                    <i class="ri-checkbox-circle-fill onboarding-check" aria-hidden="true" />
                  </button>
                </div>
              </NFormItem>
            </NGi>
          <NGi>
            <NFormItem label="公司名称" required>
              <NInput v-model:value="createForm.name" placeholder="请输入公司全称" />
            </NFormItem>
          </NGi>
          <NGi>
            <!-- 税号是申报作业包和报表报送的必填项，建账时即要求填写 -->
            <NFormItem label="纳税人识别号" required>
              <NInput
                v-model:value="createForm.taxId"
                maxlength="20"
                placeholder="15/17/18/20 位字母数字"
              >
                <template #suffix>
                  <NTooltip trigger="hover">
                    <template #trigger>
                      <NButton text tag="a" class="tax-search-btn" :href="bingSearchUrl(createForm.name)" target="_blank" :disabled="!createForm.name.trim()" tabindex="-1">
                        <i class="ri-external-link-line" />
                      </NButton>
                    </template>
                    {{ createForm.name.trim() ? '去搜索并复制统一社会信用代码' : '先填写公司名称，再去搜索税号' }}
                  </NTooltip>
                </template>
              </NInput>
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem label="主体类型">
              <div class="entity-type-stack">
                <NSelect v-model:value="createForm.type" :options="companyTypes" />
                <div v-if="createForm.type !== '有限责任公司'" class="entity-type-hint">
                  该主体不缴企业所得税，按经营所得缴纳个人所得税（5%–35%）；税务申报模块的企业所得税作业包不适用。
                </div>
              </div>
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem required>
              <template #label>
                启用日期
                <NPopover trigger="click" :style="{ maxWidth: '320px' }">
                  <template #trigger>
                    <i class="ri-question-line form-help-icon" />
                  </template>
                  账套从此日开始记账。新开业公司填成立（开业）日期；从代账或手工账转来的，填接账后账套启用的起始日即可，更早的业务请在「我的 → 期初设置」中以期初余额带入
                </NPopover>
              </template>
              <NDatePicker
                v-model:formatted-value="createForm.startDate"
                value-format="yyyy-MM-dd"
                type="date"
                clearable
                style="width: 100%"
                placeholder="选择账套启用日期"
                :is-date-disabled="(ts: number) => ts > Date.now()"
              />
            </NFormItem>
          </NGi>
          <NGi :span="2">
            <div class="form-section-label">税务信息</div>
          </NGi>
          <NGi :span="2">
            <NFormItem>
              <template #label>
                所属行业
                <NPopover trigger="click" :style="{ maxWidth: '320px' }">
                  <template #trigger>
                    <i class="ri-question-line form-help-icon" />
                  </template>
                  <div>
                    可到爱企查、企查查等平台查询贵公司的所属行业（按国民经济行业分类的大类显示），照抄选择即可。
                    <NButton
                      size="tiny"
                      type="primary"
                      secondary
                      tag="a"
                      :href="bingSearchUrl(createForm.name)"
                      target="_blank"
                      :disabled="!createForm.name.trim()"
                      style="margin-top: 8px"
                    >
                      <i class="ri-external-link-line" />&nbsp;去搜索公司行业
                    </NButton>
                  </div>
                </NPopover>
              </template>
              <div class="industry-field">
                <!-- :key 哨兵计数器强制重渲染：见 onIndustryChange 注释 -->
                <NSelect
                  :key="`create-${industrySelectSync}`"
                  v-model:value="createForm.industryCode"
                  :options="createIndustryOptions"
                  :loading="industryLoading"
                  :disabled="industryLoading"
                  placeholder="选填；可输入搜索"
                  clearable
                  filterable
                  :virtual-scroll="false"
                  @update:value="onIndustryChange('create', $event)"
                />
                <NButton text type="primary" size="tiny" class="industry-more-toggle" @click="showAllIndustries = !showAllIndustries">
                  {{ showAllIndustries ? '收起全部行业' : '找不到你的行业？显示全部行业' }}
                </NButton>
                <div
                  v-if="createForm.restrictedIndustry === 'yes'"
                  class="industry-restriction-hint is-restricted"
                >
                  <div>该行业默认按「属于国家限制或禁止行业」处理，不享受小型微利企业优惠。</div>
                  <NButton
                    text
                    type="primary"
                    size="tiny"
                    class="industry-override-link"
                    @click="createForm.restrictedIndustry = 'no'"
                  >
                    实际不涉及限制类业务？改为「否」
                  </NButton>
                </div>
                <div
                  v-else-if="createForm.industryCode && defaultRestrictedFor('create') === 'yes'"
                  class="industry-restriction-hint"
                >
                  <div>已按实际业务标记为「非限制/禁止行业」。</div>
                  <NButton
                    text
                    type="primary"
                    size="tiny"
                    class="industry-override-link"
                    @click="restoreIndustryDefault('create')"
                  >
                    恢复默认判断
                  </NButton>
                </div>
              </div>
            </NFormItem>
          </NGi>
        </NGrid>
      </NForm>
      <div class="form-tip"><i class="ri-information-line" />目前仅支持小规模纳税人，一般纳税人暂不支持</div>
      </template>
      <template #footer>
        <div v-if="createSuccessState" class="success-modal-footer">
          <NButton @click="closeSuccessModal">稍后处理，留在本页</NButton>
        </div>
        <NSpace v-else justify="end">
          <NButton @click="showCreate = false">取消</NButton>
          <NButton type="primary" :loading="creating" @click="handleCreate">确认创建</NButton>
        </NSpace>
      </template>
    </NModal>

    <!-- Edit Modal -->
    <NModal v-model:show="showEdit" title="编辑主体" preset="card" style="width: 720px; max-width: 92vw;">
      <NForm>
        <NGrid :cols="2" :x-gap="16">
          <NGi>
            <NFormItem label="名称" required>
              <NInput v-model:value="editForm.name" placeholder="账本或公司全称" />
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem label="纳税人识别号" required>
              <NInput v-model:value="editForm.taxId" maxlength="20" placeholder="15/17/18/20 位字母数字">
                <template #suffix>
                  <NTooltip trigger="hover">
                    <template #trigger>
                      <NButton text tag="a" class="tax-search-btn" :href="bingSearchUrl(editForm.name)" target="_blank" :disabled="!editForm.name.trim()" tabindex="-1">
                        <i class="ri-external-link-line" />
                      </NButton>
                    </template>
                    {{ editForm.name.trim() ? '去搜索并复制统一社会信用代码' : '先填写公司名称，再去搜索税号' }}
                  </NTooltip>
                </template>
              </NInput>
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem label="主体类型">
              <div class="entity-type-stack">
                <NSelect v-model:value="editForm.type" :options="companyTypes" />
                <div v-if="editForm.type !== '有限责任公司'" class="entity-type-hint">
                  该主体不缴企业所得税，按经营所得缴纳个人所得税（5%–35%）；企业所得税作业包不适用，
                  经营所得 A 表预缴与 B 表汇算作业包见税务申报页。
                </div>
                <div v-if="editForm.type !== '有限责任公司'" class="entity-type-hint iit-cycle-field">
                  <span>经营所得税预缴周期（按税局核定，缺省季报）</span>
                  <NSelect
                    v-model:value="editForm.businessIncomeIitFilingCycle"
                    size="small"
                    clearable
                    style="max-width: 160px"
                    :disabled="iitCycleSynced"
                    :options="[
                      { label: '季报', value: 'quarter' },
                      { label: '月报', value: 'month' },
                    ]"
                    placeholder="季报（缺省）"
                  />
                  <span v-if="iitCycleSynced" class="entity-type-hint">
                    已由「<router-link to="/tax-registration">税费种认定</router-link>」接管，此处只在未同步认定表时兜底。
                  </span>
                </div>
              </div>
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem label="增值税申报周期">
              <div class="entity-type-stack">
                <NSelect
                  v-model:value="editForm.vatFilingCycle"
                  :disabled="vatCycleSynced"
                  :options="[
                    { label: '季报（季销售额 30 万以下免征）', value: 'quarter' },
                    { label: '月报（月销售额 10 万以下免征）', value: 'month' },
                  ]"
                  placeholder="季报（缺省）"
                />
                <div v-if="vatCycleSynced" class="entity-type-hint">
                  该主体的申报周期由「<router-link to="/tax-registration">税费种认定</router-link>」按税局认定表决定，
                  这里不再生效。要改周期请先更新认定表。
                </div>
                <div v-else class="entity-type-hint">
                  按主管税务机关核定的纳税期限填写。小规模纳税人可选月报或季报，
                  <strong>一经选择，一个会计年度内不得变更</strong>；免税额度随周期变化，
                  选错会直接影响增值税计算与申报。
                </div>
              </div>
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem required>
              <template #label>
                启用日期
                <NPopover trigger="click" :style="{ maxWidth: '320px' }">
                  <template #trigger>
                    <i class="ri-question-line form-help-icon" />
                  </template>
                  账套从此日开始记账。新开业公司填成立（开业）日期；从代账或手工账转来的，填接账后账套启用的起始日即可，更早的业务请在「我的 → 期初设置」中以期初余额带入
                </NPopover>
              </template>
              <NDatePicker
                v-model:formatted-value="editForm.startDate"
                value-format="yyyy-MM-dd"
                type="date"
                clearable
                style="width: 100%"
                placeholder="选择账套启用日期"
                :is-date-disabled="(ts: number) => ts > Date.now()"
              />
            </NFormItem>
          </NGi>
          <NGi :span="2">
            <div class="form-section-label">税务信息</div>
          </NGi>
          <NGi :span="2">
            <NFormItem>
              <template #label>
                所属行业
                <NPopover trigger="click" :style="{ maxWidth: '320px' }">
                  <template #trigger>
                    <i class="ri-question-line form-help-icon" />
                  </template>
                  <div>
                    可到爱企查、企查查等平台查询贵公司的所属行业（按国民经济行业分类的大类显示），照抄选择即可。
                    <NButton
                      size="tiny"
                      type="primary"
                      secondary
                      tag="a"
                      :href="bingSearchUrl(editForm.name)"
                      target="_blank"
                      :disabled="!editForm.name.trim()"
                      style="margin-top: 8px"
                    >
                      <i class="ri-external-link-line" />&nbsp;去搜索公司行业
                    </NButton>
                  </div>
                </NPopover>
              </template>
              <div class="industry-field">
                <!-- :key 哨兵计数器强制重渲染：见 onIndustryChange 注释 -->
                <NSelect
                  :key="`edit-${industrySelectSync}`"
                  v-model:value="editForm.industryCode"
                  :options="editIndustryOptions"
                  :loading="industryLoading"
                  :disabled="industryLoading"
                  placeholder="选填；可输入搜索"
                  clearable
                  filterable
                  :virtual-scroll="false"
                  @update:value="onIndustryChange('edit', $event)"
                />
                <NButton text type="primary" size="tiny" class="industry-more-toggle" @click="showAllIndustries = !showAllIndustries">
                  {{ showAllIndustries ? '收起全部行业' : '找不到你的行业？显示全部行业' }}
                </NButton>
                <div
                  v-if="editForm.legacyIndustryName && !editForm.industryCode"
                  class="industry-restriction-hint"
                >
                  当前主体沿用历史行业「{{ editForm.legacyIndustryName }}」。不改行业时会原样保留；重新选择后将关联后台模板。
                </div>
                <div
                  v-if="editForm.restrictedIndustry === 'yes'"
                  class="industry-restriction-hint is-restricted"
                >
                  <div>该行业默认按「属于国家限制或禁止行业」处理，不享受小型微利企业优惠。</div>
                  <NButton
                    text
                    type="primary"
                    size="tiny"
                    class="industry-override-link"
                    @click="editForm.restrictedIndustry = 'no'"
                  >
                    实际不涉及限制类业务？改为「否」
                  </NButton>
                </div>
                <div
                  v-else-if="editForm.industryCode && defaultRestrictedFor('edit') === 'yes'"
                  class="industry-restriction-hint"
                >
                  <div>已按实际业务标记为「非限制/禁止行业」。</div>
                  <NButton
                    text
                    type="primary"
                    size="tiny"
                    class="industry-override-link"
                    @click="restoreIndustryDefault('edit')"
                  >
                    恢复默认判断
                  </NButton>
                </div>
              </div>
            </NFormItem>
          </NGi>
        </NGrid>
      </NForm>
      <div class="form-tip"><i class="ri-information-line" />目前仅支持小规模纳税人，一般纳税人暂不支持</div>
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
  max-width: var(--page-width-narrow);
  margin: 0 auto;
}
.form-section-label {
  font-size: 12.5px;
  font-weight: 600;
  color: var(--solo-text-muted);
  letter-spacing: 0.5px;
  padding: 2px 0 0 2px;
}
.form-tip {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 12.5px;
  color: var(--solo-text-muted);
  margin-top: 4px;
}
.form-help-icon {
  cursor: pointer;
  margin-left: 4px;
  font-size: 14px;
  color: var(--solo-text-muted);
}
.tax-search-btn {
  font-size: 15px;
  color: var(--solo-text-muted);
}
.onboarding-choice {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  width: 100%;
}
.onboarding-option {
  position: relative;
  display: flex;
  align-items: flex-start;
  gap: 10px;
  width: 100%;
  padding: 12px 34px 12px 14px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-control);
  background: var(--solo-bg-card);
  color: var(--solo-text-primary);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color 160ms ease-out, background-color 160ms ease-out;
}
.onboarding-option:hover {
  border-color: var(--solo-primary);
  background: var(--solo-bg-blue-soft);
}
.onboarding-option:focus-visible {
  outline: 2px solid var(--solo-primary);
  outline-offset: 1px;
}
.onboarding-option.active {
  border-color: var(--solo-primary);
  background: var(--solo-bg-blue-soft);
}
.onboarding-option > i:first-child {
  color: var(--solo-primary);
  font-size: 18px;
  line-height: 1.4;
}
.onboarding-copy {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.onboarding-copy strong {
  font-size: 13px;
  font-weight: 600;
}
.onboarding-copy span {
  font-size: 12px;
  line-height: 1.4;
  color: var(--solo-text-secondary);
}
.onboarding-check {
  position: absolute;
  top: 8px;
  right: 8px;
  font-size: 16px;
  color: var(--solo-primary);
  opacity: 0;
  transition: opacity 160ms ease-out;
}
.onboarding-option.active .onboarding-check {
  opacity: 1;
}
@media (max-width: 560px) {
  .onboarding-choice {
    grid-template-columns: 1fr;
  }
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
  position: relative;
}
.company-card .info {
  padding-right: 68px;
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
.entity-type-stack {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 6px;
  width: 100%;
}
.entity-type-hint {
  margin-top: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--solo-text-muted);
}
.iit-cycle-field {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-top: 2px;
}
.industry-restriction-hint {
  margin-top: 6px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--solo-text-muted);
}
.industry-restriction-hint.is-restricted {
  color: var(--solo-expense, #bd3d3d);
}
.industry-more-toggle {
  padding-left: 0;
  font-size: 12px;
  align-self: flex-start;
}
.card-actions {
  position: absolute;
  top: -2px;
  right: -2px;
  display: flex;
  align-items: center;
  gap: 2px;
  z-index: 1;
}
.icon-wrap {
  display: inline-flex;
}
.icon-action {
  color: var(--solo-text-muted);
  transition: color 0.15s ease, background-color 0.15s ease;
}
.icon-action:hover {
  color: var(--solo-primary);
}
.icon-action.is-danger:hover {
  color: var(--solo-expense, #bd3d3d);
}
.delete-steps {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
  font-size: 12px;
  color: var(--solo-text-muted);
}
.delete-steps span {
  padding: 2px 8px;
  border-radius: 8px;
  background: var(--solo-bg-muted, #edf0f4);
}
.delete-steps span.active {
  color: #fff;
  background: var(--solo-expense, #bd3d3d);
}
.delete-warning {
  margin: 0 0 8px;
  font-size: 14px;
  color: var(--solo-text-primary);
}
.delete-list {
  margin: 0;
  padding-left: 18px;
  font-size: 13px;
  line-height: 1.9;
  color: var(--solo-text-secondary, #586474);
}
.delete-name-hint {
  margin: 4px 0 10px;
  font-weight: 600;
}
.delete-final {
  margin: 8px 0;
  color: var(--solo-expense, #bd3d3d);
  font-weight: 600;
}
.industry-override-link {
  padding-left: 0;
  font-size: 12px;
  margin-top: 2px;
  align-self: flex-start;
}
.industry-field {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 2px;
  width: 100%;
}

.create-success-panel {
  display: flex;
  flex-direction: column;
  gap: 18px;
  padding: 4px 0 10px;
}

.create-success-header {
  display: flex;
  align-items: flex-start;
  gap: 14px;
  padding: 16px;
  background: var(--solo-bg-blue-soft);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-control);
}

.create-success-header .success-icon {
  font-size: 26px;
  color: var(--solo-primary);
  flex-shrink: 0;
  margin-top: 1px;
}

.success-header-text h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 650;
  color: var(--solo-text-primary);
}

.success-header-text p {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--solo-text-secondary);
  line-height: 1.5;
}

.next-step-cards {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.next-step-card {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding: 16px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-control);
  background: var(--solo-bg-card);
  cursor: pointer;
  transition: all 160ms ease-out;
}

.next-step-card:hover {
  border-color: var(--solo-primary);
  background: var(--solo-bg-blue-soft);
}

.next-step-card.primary-card {
  border-color: var(--solo-primary);
}

.step-card-badge {
  position: absolute;
  top: -10px;
  left: 14px;
  background: var(--solo-primary);
  color: var(--solo-bg-card);
  font-size: 11px;
  font-weight: 600;
  padding: 1px 8px;
  border-radius: 9999px;
}

.step-card-content {
  display: flex;
  align-items: center;
  gap: 14px;
  flex: 1;
}

.step-card-content .step-icon {
  font-size: 24px;
  color: var(--solo-primary);
  flex-shrink: 0;
}

.step-text {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.step-text strong {
  font-size: 14px;
  font-weight: 650;
  color: var(--solo-text-primary);
}

.step-text span {
  font-size: 12px;
  color: var(--solo-text-secondary);
  line-height: 1.4;
}

.success-modal-footer {
  display: flex;
  justify-content: flex-end;
}
</style>
