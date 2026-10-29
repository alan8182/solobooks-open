<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NCard, NButton, NForm, NFormItem, NInput, NInputNumber, NSelect, NSpace, useMessage, NDatePicker, NGrid, NGridItem, NCheckbox, NModal } from 'naive-ui'
import {
  createIntangibleAsset,
  createIntangibleAssetFromEntry,
  getIntangibleAssetEntryPrefill,
  getIntangibleCategories,
  runIntangibleAmortizationCatchUp,
} from '@/api/intangible'
import { getRdProjects } from '@/api/rd'
import { formatMoney } from '@/utils/format'
import { amortizationPlan } from '@/utils/fixedAssetResidual'
import type {
  IntangibleAmortizationDuePeriod,
  IntangibleAssetCreateRequest,
  IntangibleAssetEntryPrefill,
  IntangibleAssetFromEntryResponse,
  IntangibleCategory,
} from '@/api/types'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const submitting = ref(false)
const categories = ref<IntangibleCategory[]>([])
const rdProjectOptions = ref<{ label: string; value: string }[]>([])
const residualReasonChecked = ref(false)
const sourceEntryId = computed(() => typeof route.query.sourceEntryId === 'string' ? route.query.sourceEntryId : '')
const sourcePrefill = ref<IntangibleAssetEntryPrefill | null>(null)
const showAdvanced = ref(false)
const createdAssetId = ref('')
const duePeriods = ref<IntangibleAmortizationDuePeriod[]>([])
const dueTotalAmount = ref(0)
const showDueModal = ref(false)
const runningCatchUp = ref(false)

// formatted-value 的「无值」哨兵是 null，空字符串会被当作待解析日期导致页面崩溃
const form = ref<Omit<IntangibleAssetCreateRequest, 'acquisitionDate' | 'availableForUseDate' | 'expiryDate'> & {
  acquisitionDate: string | null
  availableForUseDate: string | null
  expiryDate: string | null
}>({
  name: '',
  category: 'software',
  acquisitionType: 'purchase',
  acquisitionDate: null,
  availableForUseDate: null,
  originalValue: 0,
  residualValue: 0,
  usefulLifeMonths: 60,
  legalCertNo: '',
  grantOrg: '',
  expiryDate: null,
  paymentMethod: 'company_account',
  rdUseRate: 0,
})

const acquisitionTypeOptions = [
  { label: '外购取得', value: 'purchase' },
  { label: '自行研发资本化', value: 'self_developed' },
  { label: '投资者投入', value: 'investor_contribution' },
  { label: '接受捐赠', value: 'donation' },
]

const paymentMethodOptions = [
  { label: '公司账户支付', value: 'company_account' },
  { label: '挂应付账款', value: 'account_payable' },
  { label: '个人垫付', value: 'personal_advance' },
]

const categoryOptions = computed(() =>
  categories.value.map((c) => ({ label: `${c.name}（默认摊销 ${c.defaultLifeMonths} 个月）`, value: c.code }))
)

const currentCategory = computed(() => categories.value.find((c) => c.code === form.value.category))
const certLabel = computed(() => currentCategory.value?.certLabel ?? '权证号')
const lifeTip = computed(() => currentCategory.value?.lifeBasis ?? '')

function handleCategoryChange(code: string) {
  const cat = categories.value.find((c) => c.code === code)
  if (cat) form.value.usefulLifeMonths = cat.defaultLifeMonths
}

function handleAcquisitionDateChange(val: string | null) {
  if (val && !form.value.availableForUseDate) form.value.availableForUseDate = val
}

function setResidualZero() {
  form.value.residualValue = 0
  residualReasonChecked.value = false
}

// 无形资产：可供使用当月起摊，残值默认视为零
const calc = computed(() => {
  const plan = amortizationPlan({
    originalValue: form.value.originalValue || 0,
    residualValue: form.value.residualValue || 0,
    usefulLifeMonths: form.value.usefulLifeMonths || 0,
    availableForUseDate: form.value.availableForUseDate || form.value.acquisitionDate || null,
  })
  return {
    base: plan.base,
    monthly: plan.monthly,
    startPeriod: plan.startPeriod || '—',
    endPeriod: plan.endPeriod || '—',
  }
})

const showSoftwareTip = computed(() => form.value.category === 'software' && form.value.acquisitionType === 'purchase')

async function loadSourcePrefill() {
  if (!sourceEntryId.value) return
  try {
    const prefill = await getIntangibleAssetEntryPrefill(sourceEntryId.value)
    if (prefill.existingAssetId) {
      message.info('这笔交易已经建立无形资产卡片')
      await router.replace(`/intangibles/${prefill.existingAssetId}`)
      return
    }
    sourcePrefill.value = prefill
    form.value.name = prefill.name
    form.value.category = prefill.suggestedCategory || 'other'
    form.value.acquisitionDate = prefill.acquisitionDate
    form.value.availableForUseDate = prefill.availableForUseDate
    form.value.originalValue = Number(prefill.originalValue) || 0
    handleCategoryChange(form.value.category)
  } catch (err: any) {
    message.error(err?.response?.data?.detail?.message || err?.response?.data?.detail || '加载来源交易失败')
  }
}

function currentPeriod(): string {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
}

function handleCreated(result: IntangibleAssetFromEntryResponse) {
  createdAssetId.value = result.asset.id
  if (result.duePeriods?.length) {
    duePeriods.value = result.duePeriods
    dueTotalAmount.value = Number(result.dueTotalAmount) || 0
    showDueModal.value = true
    return
  }
  message.success('无形资产卡片已成功建立')
  router.push(`/intangibles/${result.asset.id}`)
}

async function directCatchUp() {
  if (!createdAssetId.value) return
  runningCatchUp.value = true
  try {
    const result = await runIntangibleAmortizationCatchUp({
      throughPeriod: currentPeriod(),
      assetIds: [createdAssetId.value],
    })
    const pending = Number(result.pendingReviewCount || 0)
    message.success(
      pending
        ? `已补提 ${result.processedCount} 期摊销，其中 ${pending} 张凭证待复核`
        : `已补提 ${result.processedCount} 期摊销`,
    )
    showDueModal.value = false
    router.push(`/intangibles/${createdAssetId.value}`)
  } catch (err: any) {
    message.error(err?.response?.data?.detail?.message || err?.response?.data?.detail || '补提摊销失败')
  } finally {
    runningCatchUp.value = false
  }
}

function deferCatchUp() {
  showDueModal.value = false
  router.push(`/intangibles/${createdAssetId.value}`)
}

onMounted(async () => {
  try {
    const resp = await getIntangibleCategories()
    categories.value = resp.items ?? []
  } catch {
    categories.value = []
  }
  await loadSourcePrefill()
  try {
    const projects = await getRdProjects()
    rdProjectOptions.value = projects
      .filter((p) => p.status === 'active')
      .map((p) => ({ label: `${p.code} ${p.name}`, value: p.id }))
  } catch {
    rdProjectOptions.value = []
  }
})

async function handleSubmit() {
  if (!form.value.name.trim()) {
    message.warning('请输入资产名称')
    return
  }
  if (!form.value.category) {
    message.warning('请选择资产类别')
    return
  }
  if ((form.value.originalValue || 0) <= 0) {
    message.warning('请输入大于零的取得成本')
    return
  }
  if ((form.value.residualValue || 0) >= (form.value.originalValue || 0)) {
    message.warning('预计净残值必须小于取得成本')
    return
  }
  if (!form.value.acquisitionDate) {
    message.warning('请选择取得日期')
    return
  }
  if (!form.value.availableForUseDate) {
    message.warning('请选择可供使用日期（当月起摊销）')
    return
  }
  const cat = currentCategory.value
  if (cat && form.value.usefulLifeMonths < cat.lifeMinMonths) {
    message.warning(`${cat.name}摊销年限不得少于 ${cat.lifeMinMonths} 个月`)
    return
  }
  if ((form.value.residualValue || 0) > 0 && !residualReasonChecked.value) {
    message.warning('残值默认为零；确认存在第三方回购承诺或活跃市场定价后勾选说明')
    return
  }
  if (form.value.acquisitionType === 'self_developed' && !form.value.rdProjectId) {
    message.warning('自行研发资本化必须关联研发项目')
    return
  }
  if ((form.value.rdUseRate || 0) > 0 && (!form.value.rdProjectId || !form.value.rdAllocationBasis?.trim())) {
    message.warning('研发使用资产必须选择研发项目并填写分摊依据')
    return
  }
  submitting.value = true
  try {
    const payload: IntangibleAssetCreateRequest = {
      ...form.value,
      acquisitionDate: form.value.acquisitionDate,
      availableForUseDate: form.value.availableForUseDate,
      expiryDate: form.value.expiryDate || undefined,
    }
    if (!payload.residualValue) delete payload.residualValue
    if (sourceEntryId.value) {
      const result = await createIntangibleAssetFromEntry({
        ...payload,
        sourceEntryId: sourceEntryId.value,
      })
      handleCreated(result)
    } else {
      await createIntangibleAsset(payload)
      message.success('资产已保存为草稿，提交审核后将生成取得凭证并开始按月摊销')
      router.push({ path: '/assets', query: { tab: 'intangible' } })
    }
  } catch (err: any) {
    message.error(err?.response?.data?.detail?.message || err?.response?.data?.detail || '录入失败，请检查输入项')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="create-page">
    <div class="page-header">
      <div class="header-left">
        <NButton text class="back-btn" @click="router.push({ path: '/assets', query: { tab: 'intangible' } })">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回
        </NButton>
        <div class="title-section">
          <h2>{{ sourceEntryId ? '确认无形资产与摊销方案' : '录入无形资产' }}</h2>
          <p class="subtitle">
            <template v-if="sourceEntryId">原交易已经入账，这里只建立资产卡片，不会重复生成取得凭证。无形资产自可供使用<b>当月</b>起摊销。</template>
            <template v-else>录入外购或研发资本化的无形资产。自可供使用<b>当月</b>起摊销（与固定资产次月起提不同），处置当月停止摊销。</template>
          </p>
        </div>
      </div>
    </div>

    <div v-if="sourceEntryId" class="source-anchor">
      <div class="anchor-head">
        <div class="anchor-badge">来源交易核对</div>
        <span class="anchor-note">原支出交易已入账，本次仅确立摊销规则，不会重复生成资金凭证。</span>
      </div>
      <div class="anchor-facts">
        <div class="fact-item">
          <span class="fact-label">记账摘要</span>
          <strong class="fact-val">{{ sourcePrefill?.summary || sourcePrefill?.name || form.name || '—' }}</strong>
        </div>
        <div class="fact-item">
          <span class="fact-label">交易日期</span>
          <strong class="fact-val tabular">{{ sourcePrefill?.acquisitionDate || form.acquisitionDate || '—' }}</strong>
        </div>
        <div class="fact-item">
          <span class="fact-label">支出账户</span>
          <strong class="fact-val">{{ sourcePrefill?.sourceAccount || '公司账户（外购取得）' }}</strong>
        </div>
        <div class="fact-item">
          <span class="fact-label">原账目凭证号</span>
          <strong class="fact-val tabular">{{ sourcePrefill?.voucherNo || sourceEntryId.slice(0, 8) }}</strong>
        </div>
        <div class="fact-item">
          <span class="fact-label">价税合计金额</span>
          <strong class="fact-val tabular amount-highlight">{{ formatMoney(sourcePrefill?.originalValue ?? form.originalValue ?? 0) }}</strong>
        </div>
      </div>
    </div>

    <div class="workspace-grid">
      <NCard :bordered="false" class="form-card">
        <NForm label-placement="top">
          <div class="section-heading">1. 核心摊销方案</div>
          <NGrid :cols="2" :x-gap="20">
            <NGridItem :span="2">
              <NFormItem label="资产名称">
                <NInput v-model:value="form.name" placeholder="如：XX 商标权、XX 管理软件、XX 实用新型专利" />
              </NFormItem>
            </NGridItem>

            <NGridItem :span="sourceEntryId ? 2 : 1">
              <NFormItem label="资产类别">
                <NSelect v-model:value="form.category" :options="categoryOptions" @update:value="handleCategoryChange" />
                <template #feedback>
                  <span class="form-tip">{{ lifeTip }}</span>
                </template>
              </NFormItem>
            </NGridItem>

            <NGridItem v-if="!sourceEntryId" :span="1">
              <NFormItem label="取得方式">
                <NSelect v-model:value="form.acquisitionType" :options="acquisitionTypeOptions" />
              </NFormItem>
            </NGridItem>

            <NGridItem v-if="!sourceEntryId" :span="1">
              <NFormItem label="取得日期">
                <NDatePicker
                  v-model:formatted-value="form.acquisitionDate"
                  value-format="yyyy-MM-dd"
                  type="date"
                  style="width: 100%;"
                  placeholder="成本发生日/合同生效日"
                  @update:formatted-value="handleAcquisitionDateChange"
                />
              </NFormItem>
            </NGridItem>

            <NGridItem :span="1">
              <NFormItem label="可供使用日期（当月起摊销）">
                <NDatePicker
                  v-model:formatted-value="form.availableForUseDate"
                  value-format="yyyy-MM-dd"
                  type="date"
                  style="width: 100%;"
                  placeholder="默认与取得日期一致"
                />
                <template #feedback>
                  <span class="form-tip">无形资产自<b>可供使用当月</b>起摊销。</span>
                </template>
              </NFormItem>
            </NGridItem>

            <NGridItem v-if="!sourceEntryId" :span="1">
              <NFormItem label="取得成本（元）">
                <NInputNumber v-model:value="form.originalValue" :min="0.01" :step="1000" style="width: 100%;" placeholder="0.00" />
                <template #feedback>
                  <span class="form-tip">小规模纳税人不抵扣进项税额，按价税合计金额填入。</span>
                </template>
              </NFormItem>
            </NGridItem>

            <NGridItem v-if="form.acquisitionType === 'purchase' && !sourceEntryId" :span="1">
              <NFormItem label="支付方式">
                <NSelect v-model:value="form.paymentMethod" :options="paymentMethodOptions" />
              </NFormItem>
            </NGridItem>

            <NGridItem :span="1">
              <NFormItem label="摊销年限（月）">
                <NInputNumber
                  v-model:value="form.usefulLifeMonths"
                  :min="currentCategory?.lifeMinMonths ?? 12"
                  :max="currentCategory?.lifeMaxMonths ?? 840"
                  style="width: 100%;"
                />
                <template #feedback>
                  <span class="form-tip">小企业会计准则采用年限平均法；外购软件最短 24 个月。</span>
                </template>
              </NFormItem>
            </NGridItem>

            <NGridItem :span="1">
              <NFormItem>
                <template #label>
                  <div class="label-with-pills">
                    <span>预计净残值（元）</span>
                    <button type="button" class="rate-pill" :class="{ active: !form.residualValue }" @click="setResidualZero">0 无残值</button>
                  </div>
                </template>
                <NInputNumber v-model:value="form.residualValue" :min="0" :step="100" :precision="2" style="width: 100%;" placeholder="默认为 0" />
                <template #feedback>
                  <span class="form-tip">准则规定残值视为零；仅第三方回购承诺或活跃市场定价时可非零。</span>
                </template>
              </NFormItem>
            </NGridItem>

            <NGridItem v-if="(form.residualValue || 0) > 0" :span="2">
              <NCheckbox v-model:checked="residualReasonChecked" size="small">
                存在第三方回购承诺或活跃市场定价（否则残值视为零）
              </NCheckbox>
            </NGridItem>
          </NGrid>

          <div class="advanced-section">
            <button type="button" class="advanced-toggle" @click="showAdvanced = !showAdvanced">
              <span>2. 权属与归集信息（权证号 / 到期日 / 保管人 / 研发分摊）</span>
              <span class="toggle-hint">{{ showAdvanced ? '收起' : '展开选填项' }}</span>
            </button>

            <div v-if="showAdvanced" class="advanced-body">
              <NGrid :cols="2" :x-gap="16">
                <NGridItem>
                  <NFormItem :label="certLabel">
                    <NInput v-model:value="form.legalCertNo" placeholder="选填" />
                  </NFormItem>
                </NGridItem>
                <NGridItem>
                  <NFormItem label="登记/授权机关">
                    <NInput v-model:value="form.grantOrg" placeholder="选填" />
                  </NFormItem>
                </NGridItem>
                <NGridItem>
                  <NFormItem label="权利到期日">
                    <NDatePicker v-model:formatted-value="form.expiryDate" value-format="yyyy-MM-dd" type="date" clearable style="width: 100%;" placeholder="专利/商标/土地/合同到期日" />
                  </NFormItem>
                </NGridItem>
                <NGridItem>
                  <NFormItem label="保管人">
                    <NInput v-model:value="form.custodian" placeholder="选填" />
                  </NFormItem>
                </NGridItem>
              </NGrid>

              <div class="rd-allocation-box">
                <div class="calc-title">研发使用归集（选填）</div>
                <NGrid :cols="2" :x-gap="16">
                  <NGridItem>
                    <NFormItem label="研发项目">
                      <NSelect v-model:value="form.rdProjectId" :options="rdProjectOptions" clearable filterable placeholder="未用于研发可不选" />
                    </NFormItem>
                  </NGridItem>
                  <NGridItem>
                    <NFormItem label="研发使用比例（0-1）">
                      <NInputNumber v-model:value="form.rdUseRate" :min="0" :max="1" :step="0.1" style="width:100%" placeholder="例如 0.6 表示60%" />
                    </NFormItem>
                  </NGridItem>
                  <NGridItem :span="2">
                    <NFormItem label="分摊依据">
                      <NInput v-model:value="form.rdAllocationBasis" placeholder="例如：研发项目使用时长占60%" />
                    </NFormItem>
                  </NGridItem>
                </NGrid>
              </div>
            </div>
          </div>
        </NForm>

        <div class="form-footer">
          <NSpace justify="end" size="medium">
            <NButton @click="router.push({ path: '/assets', query: { tab: 'intangible' } })">取消</NButton>
            <NButton type="primary" :loading="submitting" @click="handleSubmit">
              {{ sourceEntryId ? '确认建立摊销方案' : '保存草稿' }}
            </NButton>
          </NSpace>
        </div>
      </NCard>

      <aside class="preview-sidebar">
        <div class="preview-board">
          <div class="preview-metric">
            <span class="metric-label">预计月摊销额</span>
            <div class="metric-value tabular">
              {{ formatMoney(calc.monthly) }}<span class="metric-unit">/月</span>
            </div>
            <div class="metric-sub tabular">
              应摊销总额 {{ formatMoney(calc.base) }}（成本 {{ formatMoney(form.originalValue || 0) }} - 净残值 {{ formatMoney(form.residualValue || 0) }}）
            </div>
          </div>

          <div class="preview-block">
            <div class="block-title">摊销时间轴</div>
            <div class="timeline-row">
              <span class="calc-label">首期摊销（可供使用当月）</span>
              <span class="calc-val tabular">{{ calc.startPeriod }}</span>
            </div>
            <div class="timeline-row">
              <span class="calc-label">预计摊销完毕</span>
              <span class="calc-val tabular">{{ calc.endPeriod }}</span>
            </div>
          </div>

          <div class="preview-block">
            <div class="block-title">财税合规提示</div>
            <p class="compliance-text">
              {{ lifeTip || '无形资产自可供使用当月起按月摊销（年限平均法），处置当月停止摊销。' }}
            </p>
            <p v-if="showSoftwareTip" class="compliance-text">
              外购软件凭财税政策可缩短至 24 个月摊销，请保留合同与发票留档。
            </p>
          </div>
        </div>
      </aside>
    </div>

    <NModal
      v-model:show="showDueModal"
      title="跨期历史摊销一键补提"
      preset="card"
      :closable="false"
      :mask-closable="false"
      style="width: 520px; border-radius: var(--solo-radius-lg);"
    >
      <p class="modal-lead">
        无形资产卡片已建立。检测到共有 <b>{{ duePeriods.length }}</b> 期历史月份需补提摊销，确认后将生成各月摊销凭证：
      </p>
      <div v-for="item in duePeriods" :key="item.period" class="catchup-row">
        <span class="tabular">{{ item.period }} 摊销凭证</span>
        <strong class="tabular">{{ formatMoney(item.amount) }}</strong>
      </div>
      <div class="catchup-total">
        <span>补提合计</span>
        <strong class="tabular">{{ formatMoney(dueTotalAmount) }}</strong>
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="deferCatchUp">稍后在月结向导中处理</NButton>
          <NButton type="primary" :loading="runningCatchUp" @click="directCatchUp">一键补提摊销入账</NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.create-page {
  max-width: 1080px;
  margin: 0 auto;
  padding: 16px 0 48px 0;
}
.tabular { font-variant-numeric: tabular-nums; }
.page-header { margin-bottom: 20px; }
.back-btn {
  font-weight: 500;
  color: var(--solo-text-secondary);
  margin-bottom: 8px;
}
.back-btn:hover { color: var(--solo-primary); }
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
.subtitle b, .form-tip b { color: var(--solo-primary); font-weight: 600; }

.source-anchor {
  background: var(--solo-bg-blue-soft);
  border: 1px solid var(--solo-border);
  border-left: 3px solid var(--solo-primary);
  border-radius: var(--solo-radius-lg);
  padding: 14px 18px;
  margin-bottom: 20px;
}
.anchor-head {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin-bottom: 12px;
}
.anchor-badge {
  font-size: 12px;
  font-weight: 700;
  color: var(--solo-primary);
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  padding: 2px 8px;
  border-radius: var(--solo-radius-sm, 4px);
}
.anchor-note { font-size: 12.5px; color: var(--solo-text-secondary); }
.anchor-facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--solo-border);
}
.fact-item { display: flex; flex-direction: column; gap: 2px; }
.fact-label { font-size: 11.5px; color: var(--solo-text-muted); }
.fact-val { font-size: 13.5px; font-weight: 600; color: var(--solo-text-primary); }
.amount-highlight { font-size: 16px; font-weight: 700; color: var(--solo-primary); }

.workspace-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.85fr) minmax(300px, 1fr);
  gap: 20px;
  align-items: start;
}
@media (max-width: 900px) {
  .workspace-grid { grid-template-columns: 1fr; }
}

.form-card {
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  box-shadow: var(--solo-shadow-sm);
  padding: 8px;
}
.section-heading {
  font-size: 13px;
  font-weight: 700;
  color: var(--solo-text-primary);
  margin-bottom: 14px;
}
.form-tip {
  font-size: 11px;
  color: var(--solo-text-muted);
  line-height: 1.3;
  display: block;
}

.label-with-pills {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}
.rate-pill {
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-card);
  color: var(--solo-text-secondary);
  border-radius: 999px;
  padding: 1px 10px;
  font-size: 11.5px;
  cursor: pointer;
}
.rate-pill.active {
  color: var(--solo-primary);
  border-color: var(--solo-primary);
  background: var(--solo-bg-blue-soft);
}

.advanced-section { margin-top: 8px; border-top: 1px dashed var(--solo-border); padding-top: 12px; }
.advanced-toggle {
  display: flex;
  justify-content: space-between;
  align-items: center;
  width: 100%;
  background: none;
  border: none;
  padding: 4px 0;
  font-size: 13px;
  font-weight: 700;
  color: var(--solo-text-primary);
  cursor: pointer;
}
.toggle-hint { font-size: 12px; font-weight: 500; color: var(--solo-primary); }
.advanced-body { margin-top: 12px; }
.rd-allocation-box {
  margin: 4px 0 8px;
  padding: 16px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius);
  background: var(--solo-bg-blue-soft);
}
.calc-title { font-size: 12px; font-weight: 700; color: var(--solo-primary); margin-bottom: 6px; }

.form-footer {
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px dashed var(--solo-border);
}

.preview-sidebar { position: sticky; top: 16px; }
.preview-board {
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  box-shadow: var(--solo-shadow-sm);
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.metric-label { font-size: 12px; color: var(--solo-text-muted); }
.metric-value { font-size: 30px; font-weight: 800; color: var(--solo-primary); margin-top: 4px; }
.metric-unit { font-size: 13px; font-weight: 500; color: var(--solo-text-secondary); margin-left: 2px; }
.metric-sub { font-size: 12px; color: var(--solo-text-secondary); margin-top: 6px; line-height: 1.5; }
.preview-block { padding-top: 14px; border-top: 1px dashed var(--solo-border); }
.block-title { font-size: 12px; font-weight: 700; color: var(--solo-text-primary); margin-bottom: 8px; }
.timeline-row { display: flex; justify-content: space-between; font-size: 12.5px; padding: 3px 0; }
.calc-label { color: var(--solo-text-secondary); }
.calc-val { font-weight: 600; color: var(--solo-text-primary); }
.compliance-text { margin: 0 0 6px; font-size: 12.5px; color: var(--solo-text-secondary); line-height: 1.5; }

.modal-lead { margin: 0 0 12px; color: var(--solo-text-secondary); }
.catchup-row {
  display: flex;
  justify-content: space-between;
  padding: 6px 0;
  border-bottom: 1px dashed var(--solo-border-light);
}
.catchup-total { display: flex; justify-content: space-between; padding-top: 12px; }
</style>
