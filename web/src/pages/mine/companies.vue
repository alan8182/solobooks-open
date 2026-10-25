<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref, onMounted } from 'vue'
import { useRoute } from 'vue-router'
import { NCard, NButton, NModal, NForm, NFormItem, NInput, NSelect, NTag, NPopover, NTooltip, NDatePicker, useMessage, NSpace, NGrid, NGi } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useCompanyStore } from '@/stores/company'
import { updateCompany as apiUpdateCompany, deleteCompany as apiDeleteCompany } from '@/api/auth'
import { listTaxRegistrations, type TaxRegistrationRow } from '@/api/tax-registration'
import { reportErrorToast } from '@/utils/errorPresenter'
import { restrictedIndustryFor, filterIndustryGroups, isRestrictedIndustry } from '@/utils/industryTax'
import type { CompanyInfo } from '@/api/types'
import { SoloEmpty } from '@/components/ui'

const message = useMessage()
const route = useRoute()
const authStore = useAuthStore()
const companyStore = useCompanyStore()

const companies = ref<CompanyInfo[]>([])
// Create dialog
function defaultCreateForm() {
  return {
    name: '', type: '有限责任公司', taxpayerType: '小规模纳税人',
    // formatted-value 的「无值」哨兵是 null，空字符串会被当作待解析日期导致弹窗崩溃
    startDate: null as string | null,
    taxId: '',
    restrictedIndustry: 'no', industry: '',
  }
}
const showCreate = ref(false)
const createForm = ref(defaultCreateForm())
const creating = ref(false)

// Edit dialog
const showEdit = ref(false)
const editingId = ref('')
const editForm = ref({
  name: '', type: '有限责任公司', taxpayerType: '小规模纳税人',
  startDate: null as string | null,
  taxId: '',
  restrictedIndustry: 'no', industry: '', vatFilingCycle: '' as string,
  businessIncomeIitFilingCycle: '' as string,
})
const saving = ref(false)

const companyTypes = [
  { label: '有限责任公司', value: '有限责任公司' },
  { label: '个人独资企业', value: '个人独资企业' },
  { label: '个体工商户', value: '个体工商户' },
]

/** 选定行业后自动判定「是否限制/禁止行业」（不再暴露手动选择框）。 */
function applyIndustryDefault(target: 'create' | 'edit', industry: string | null) {
  const value = industry ?? ''
  // 清空行业时不改动手动选择，避免把用户改过的「是」重置掉
  if (!value) return
  const answer = restrictedIndustryFor(value)
  if (target === 'create') {
    createForm.value.industry = value
    createForm.value.restrictedIndustry = answer
  } else {
    editForm.value.industry = value
    editForm.value.restrictedIndustry = answer
  }
}

// naive-ui filterable 搜索后点选存在渲染缺陷：值已写入表单但父组件不重渲染，选择框显示空白。
// 实测点击选项后 toggle 任意父级 ref 都能让渲染恢复，故用哨兵计数器在每次选择后强制重渲染。
const industrySelectSync = ref(0)

function onIndustryChange(target: 'create' | 'edit', industry: string | null) {
  industrySelectSync.value++
  applyIndustryDefault(target, industry)
}

function bingSearchUrl(name: string) {
  return `https://cn.bing.com/search?q=${encodeURIComponent(name.trim())}`
}

// 行业口径：国民经济行业分类 GB/T 4754-2017 大类，与爱企查「所属行业」逐字对齐，可搜索
// hidden: 默认折叠（非目标客户行业），通过「显示全部行业」展开；只影响选项展示，不删数据。
type IndustryGroup = { label: string; children: string[]; hidden?: boolean }
const industryGroups: IndustryGroup[] = [
  { label: '农、林、牧、渔业', hidden: true, children: ['农业', '林业', '畜牧业', '渔业', '农、林、牧、渔专业及辅助性活动'] },
  { label: '采矿业', hidden: true, children: ['煤炭开采和洗选业', '石油和天然气开采业', '黑色金属矿采选业', '有色金属矿采选业', '非金属矿采选业', '开采专业及辅助性活动', '其他采矿业'] },
  { label: '制造业', hidden: true, children: ['农副食品加工业', '食品制造业', '酒、饮料和精制茶制造业', '烟草制品业', '纺织业', '纺织服装、服饰业', '皮革、毛皮、羽毛及其制品和制鞋业', '木材加工和木、竹、藤、棕、草制品业', '家具制造业', '造纸和纸制品业', '印刷和记录媒介复制业', '文教、工美、体育和娱乐用品制造业', '石油、煤炭及其他燃料加工业', '化学原料和化学制品制造业', '医药制造业', '化学纤维制造业', '橡胶和塑料制品业', '非金属矿物制品业', '黑色金属冶炼和压延加工业', '有色金属冶炼和压延加工业', '金属制品业', '通用设备制造业', '专用设备制造业', '汽车制造业', '铁路、船舶、航空航天和其他运输设备制造业', '电气机械和器材制造业', '计算机、通信和其他电子设备制造业', '仪器仪表制造业', '其他制造业', '废弃资源综合利用业', '金属制品、机械和设备修理业'] },
  { label: '电力、热力、燃气及水生产和供应业', hidden: true, children: ['电力、热力生产和供应业', '燃气生产和供应业', '水的生产和供应业'] },
  { label: '建筑业', hidden: true, children: ['房屋建筑业', '土木工程建筑业', '建筑安装业', '建筑装饰、装修和其他建筑业'] },
  { label: '批发和零售业', children: ['批发业', '零售业'] },
  { label: '交通运输、仓储和邮政业', hidden: true, children: ['铁路运输业', '道路运输业', '水上运输业', '航空运输业', '管道运输业', '多式联运和运输代理业', '装卸搬运和仓储业', '邮政业'] },
  { label: '住宿和餐饮业', children: ['住宿业', '餐饮业'] },
  { label: '信息传输、软件和信息技术服务业', children: ['电信、广播电视和卫星传输服务', '互联网和相关服务', '软件和信息技术服务业'] },
  { label: '金融业', hidden: true, children: ['货币金融服务', '资本市场服务', '保险业', '其他金融业'] },
  { label: '房地产业', hidden: true, children: ['房地产业'] },
  { label: '租赁和商务服务业', children: ['租赁业', '商务服务业'] },
  { label: '科学研究和技术服务业', children: ['研究和试验发展', '专业技术服务业', '科技推广和应用服务业'] },
  { label: '水利、环境和公共设施管理业', hidden: true, children: ['水利管理业', '生态保护和环境治理业', '公共设施管理业', '土地管理业'] },
  { label: '居民服务、修理和其他服务业', children: ['居民服务业', '机动车、电子产品和日用产品修理业', '其他服务业'] },
  { label: '教育', children: ['教育'] },
  { label: '卫生和社会工作', hidden: true, children: ['卫生', '社会工作'] },
  { label: '文化、体育和娱乐业', children: ['新闻和出版业', '广播、电视、电影和录音制作业', '文化艺术业', '体育', '娱乐业'] },
]

// 「显示全部行业」开关（新建/编辑共用）；默认只展示小微企业常见行业
const showAllIndustries = ref(false)

function buildIndustryOptions(selected?: string) {
  return [
    ...filterIndustryGroups(industryGroups, {
      showAll: showAllIndustries.value,
      selected,
    }).map((g) => ({
      type: 'group' as const,
      label: g.label,
      key: g.label,
      children: g.children.map((name) => ({ label: name, value: name })),
    })),
    { label: '其他行业', value: '其他行业' },
  ]
}

const createIndustryOptions = computed(() => buildIndustryOptions(createForm.value.industry))
const editIndustryOptions = computed(() => buildIndustryOptions(editForm.value.industry))

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
  try {
    const result = await listTaxRegistrations()
    vatCycleSynced.value = groupCycleSynced(result.items, 'vat')
    iitCycleSynced.value = groupCycleSynced(result.items, 'business_income_iit_prepay')
  } catch {
    // 认定表读取失败不影响主体信息编辑，仅退化为可编辑
  }
  // 深链 ?edit=<companyId>：供启用日期校验的引导弹窗直达编辑表单
  const editId = typeof route.query.edit === 'string' ? route.query.edit : ''
  if (editId) {
    const target = companies.value.find((co) => co.company_id === editId)
    if (target) openEdit(target)
  }
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
  if (!createForm.value.taxId.trim()) {
    message.warning('请输入纳税人识别号')
    return
  }
  if (!createForm.value.startDate) {
    message.warning('请填写启用日期（账套从此日开始记账）')
    return
  }
  const rawTaxId = createForm.value.taxId.trim().toUpperCase()
  if (rawTaxId && !/^[0-9A-Z]{15}$|^[0-9A-Z]{17}$|^[0-9A-Z]{18}$|^[0-9A-Z]{20}$/.test(rawTaxId)) {
    message.warning('纳税人识别号格式不正确（15/17/18/20 位字母数字）')
    return
  }
  creating.value = true
  try {
    await companyStore.create({
      ...createForm.value,
      taxId: rawTaxId,
      restrictedIndustry: createForm.value.restrictedIndustry === 'yes',
      industry: createForm.value.industry || undefined,
      startDate: createForm.value.startDate || undefined,
    })
    loadCompanies()
    showCreate.value = false
    createForm.value = defaultCreateForm()
    message.success('创建成功')
  } catch (err) {
    reportErrorToast(message, err, '创建失败')
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
    startDate: co.startDate || null,
    taxId: co.taxId || '',
    restrictedIndustry: co.restrictedIndustry === undefined || co.restrictedIndustry === null
      ? restrictedIndustryFor(co.industry)
      : (co.restrictedIndustry ? 'yes' : 'no'),
    industry: co.industry || '',
    vatFilingCycle: co.vatFilingCycle || '',
    businessIncomeIitFilingCycle: co.businessIncomeIitFilingCycle || '',
  }
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
    await apiUpdateCompany(editingId.value, {
      ...editForm.value,
      taxId: rawTaxId,
      restrictedIndustry: editForm.value.restrictedIndustry === 'yes',
      industry: editForm.value.industry || undefined,
      startDate: editForm.value.startDate || undefined,
      vatFilingCycle: editForm.value.vatFilingCycle || undefined,
      businessIncomeIitFilingCycle: editForm.value.businessIncomeIitFilingCycle || undefined,
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
    message.success(`主体「${target.name}」已永久删除`)
    await authStore.fetchProfile()
    companyStore.setCompanies(authStore.companies as CompanyInfo[])
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
    <NModal v-model:show="showCreate" title="新增主体" preset="card" style="width: 720px; max-width: 92vw;">
      <NForm>
        <NGrid :cols="2" :x-gap="16">
          <NGi>
            <NFormItem label="公司名称" required>
              <NInput v-model:value="createForm.name" placeholder="请输入公司全称" />
            </NFormItem>
          </NGi>
          <NGi>
            <NFormItem label="纳税人识别号" required>
              <NInput v-model:value="createForm.taxId" maxlength="20" placeholder="15/17/18/20 位字母数字">
                <template #suffix>
                  <NTooltip trigger="hover">
                    <template #trigger>
                      <NButton text tag="a" class="tax-search-btn" :href="bingSearchUrl(createForm.name)" target="_blank" :disabled="!createForm.name.trim()" tabindex="-1">
                        <i class="ri-external-link-line" />
                      </NButton>
                    </template>
                    快速查询复制
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
                <NSelect :key="industrySelectSync" v-model:value="createForm.industry" :options="createIndustryOptions" placeholder="选填，可输入搜索" clearable filterable :virtual-scroll="false" @update:value="onIndustryChange('create', $event)" />
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
                  v-else-if="isRestrictedIndustry(createForm.industry)"
                  class="industry-restriction-hint"
                >
                  <div>已按实际业务标记为「非限制/禁止行业」。</div>
                  <NButton
                    text
                    type="primary"
                    size="tiny"
                    class="industry-override-link"
                    @click="createForm.restrictedIndustry = restrictedIndustryFor(createForm.industry)"
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
                    快速查询复制
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
                <NSelect :key="industrySelectSync" v-model:value="editForm.industry" :options="editIndustryOptions" placeholder="选填，可输入搜索" clearable filterable :virtual-scroll="false" @update:value="onIndustryChange('edit', $event)" />
                <NButton text type="primary" size="tiny" class="industry-more-toggle" @click="showAllIndustries = !showAllIndustries">
                  {{ showAllIndustries ? '收起全部行业' : '找不到你的行业？显示全部行业' }}
                </NButton>
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
                  v-else-if="isRestrictedIndustry(editForm.industry)"
                  class="industry-restriction-hint"
                >
                  <div>已按实际业务标记为「非限制/禁止行业」。</div>
                  <NButton
                    text
                    type="primary"
                    size="tiny"
                    class="industry-override-link"
                    @click="editForm.restrictedIndustry = restrictedIndustryFor(editForm.industry)"
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
</style>
