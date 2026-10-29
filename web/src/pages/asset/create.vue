<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NCard, NButton, NForm, NFormItem, NInput, NInputNumber, NSelect, NSpace, useMessage, NDatePicker, NGrid, NGridItem, NModal } from 'naive-ui'
import {
  createFixedAsset,
  createFixedAssetFromEntry,
  getFixedAssetCategories,
  getFixedAssetEntryPrefill,
  runFixedAssetDepreciationCatchUp,
} from '@/api/asset'
import { getRdProjects } from '@/api/rd'
import { formatMoney } from '@/utils/format'
import { depreciationPlan, residualRateFromValue, residualValueFromRate } from '@/utils/fixedAssetResidual'
import type {
  FixedAssetCategory,
  FixedAssetCreateRequest,
  FixedAssetDepreciationDuePeriod,
  FixedAssetEntryPrefill,
  FixedAssetFromEntryResponse,
} from '@/api/types'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const submitting = ref(false)
const categories = ref<FixedAssetCategory[]>([])
const rdProjects = ref<{ id: string; code: string; name: string; status: string }[]>([])
const rdProjectOptions = ref<{ label: string; value: string }[]>([])
const sourceEntryId = computed(() => typeof route.query.sourceEntryId === 'string' ? route.query.sourceEntryId : '')
const sourcePrefill = ref<FixedAssetEntryPrefill | null>(null)
const showAdvanced = ref(false)
const createdAssetId = ref('')
const duePeriods = ref<FixedAssetDepreciationDuePeriod[]>([])
const dueTotalAmount = ref(0)
const showDueModal = ref(false)
const runningCatchUp = ref(false)

// formatted-value 的「无值」哨兵是 null，空字符串会被当作待解析日期导致页面崩溃
const form = ref<Omit<FixedAssetCreateRequest, 'acquisitionDate' | 'availableForUseDate'> & {
  acquisitionDate: string | null
  availableForUseDate: string | null
}>({
  name: '',
  category: 'electronics',
  acquisitionType: 'purchase',
  acquisitionDate: null,
  availableForUseDate: null,
  originalValue: 0,
  residualValue: 0,
  residualRate: 0,
  usefulLifeMonths: 36,
  depreciationMethod: 'straight_line',
  paymentMethod: 'company_account',
  rdUseRate: 0,
})

const acquisitionTypeOptions = [
  { label: '外购取得', value: 'purchase' },
  { label: '自建', value: 'self_built' },
  { label: '投资者投入', value: 'investor_contribution' },
  { label: '接受捐赠', value: 'donation' },
]

const paymentMethodOptions = [
  { label: '公司账户支付', value: 'company_account' },
  { label: '挂应付账款', value: 'account_payable' },
  { label: '个人垫付', value: 'personal_advance' },
]

const categoryOptions = computed(() =>
  categories.value.map((c) => ({ label: `${c.name}（默认折旧 ${c.defaultLifeMonths} 个月）`, value: c.code }))
)

const currentCategory = computed(() => categories.value.find((c) => c.code === form.value.category))
const lifeTip = computed(() => currentCategory.value?.lifeBasis ?? '')

function handleCategoryChange(code: string) {
  const cat = categories.value.find((c) => c.code === code)
  if (cat) {
    form.value.usefulLifeMonths = cat.defaultLifeMonths
    if (!cat.oneTimeAllowed) {
      form.value.depreciationMethod = 'straight_line'
    }
  }
}

function handleAcquisitionDateChange(val: string | null) {
  if (val && !form.value.availableForUseDate) {
    form.value.availableForUseDate = val
  }
}

function syncResidualFromRate(value: number | null) {
  const rate = Math.min(100, Math.max(0, Number(value) || 0))
  form.value.residualRate = rate
  form.value.residualValue = residualValueFromRate(form.value.originalValue || 0, rate)
}

function syncResidualFromAmount(value: number | null) {
  const amount = Math.max(0, Number(value) || 0)
  form.value.residualValue = amount
  form.value.residualRate = residualRateFromValue(form.value.originalValue || 0, amount)
}

function handleDepreciationMethodChange(value: 'straight_line' | 'one_time') {
  form.value.depreciationMethod = value
  if (value === 'one_time') {
    form.value.residualRate = 0
    form.value.residualValue = 0
  }
}

watch(() => form.value.originalValue, (original) => {
  if ((form.value.residualRate || 0) > 0) {
    form.value.residualValue = residualValueFromRate(original || 0, form.value.residualRate || 0)
  } else if ((form.value.residualValue || 0) > 0) {
    form.value.residualRate = residualRateFromValue(original || 0, form.value.residualValue || 0)
  }
})

const calc = computed(() => {
  const plan = depreciationPlan({
    originalValue: form.value.originalValue || 0,
    residualValue: form.value.residualValue || 0,
    usefulLifeMonths: form.value.usefulLifeMonths || 0,
    oneTime: form.value.depreciationMethod === 'one_time',
    availableForUseDate: form.value.availableForUseDate || form.value.acquisitionDate || null,
  })
  return {
    base: plan.base,
    monthly: plan.monthly,
    usePeriod: plan.usePeriod || '—',
    // 次月起提
    startPeriod: plan.startPeriod || '—',
    endPeriod: plan.endPeriod || '—',
  }
})

const canOneTimeDeduct = computed(() => {
  const original = form.value.originalValue || 0
  return original > 0 && original <= 5000000 && !!currentCategory.value?.oneTimeAllowed
})

async function loadSourcePrefill() {
  if (!sourceEntryId.value) return
  try {
    const prefill = await getFixedAssetEntryPrefill(sourceEntryId.value)
    if (prefill.existingAssetId) {
      message.info('这笔交易已经建立固定资产卡片')
      await router.replace(`/assets/${prefill.existingAssetId}`)
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

function handleCreated(result: FixedAssetFromEntryResponse) {
  createdAssetId.value = result.asset.id
  if (result.duePeriods?.length) {
    duePeriods.value = result.duePeriods
    dueTotalAmount.value = Number(result.dueTotalAmount) || 0
    showDueModal.value = true
    return
  }
  message.success('固定资产卡片已成功建立')
  router.push(`/assets/${result.asset.id}`)
}

async function directCatchUp() {
  if (!createdAssetId.value) return
  runningCatchUp.value = true
  try {
    const result = await runFixedAssetDepreciationCatchUp({
      throughPeriod: currentPeriod(),
      assetIds: [createdAssetId.value],
    })
    const pending = Number(result.pendingReviewCount || 0)
    message.success(
      pending
        ? `已补提 ${result.processedCount} 期折旧，其中 ${pending} 张凭证待复核`
        : `已补提 ${result.processedCount} 期折旧`,
    )
    showDueModal.value = false
    router.push(`/assets/${createdAssetId.value}`)
  } catch (err: any) {
    message.error(err?.response?.data?.detail?.message || err?.response?.data?.detail || '补提折旧失败')
  } finally {
    runningCatchUp.value = false
  }
}

function deferCatchUp() {
  showDueModal.value = false
  router.push(`/assets/${createdAssetId.value}`)
}

onMounted(async () => {
  try {
    const resp = await getFixedAssetCategories()
    categories.value = resp.items ?? []
  } catch {
    categories.value = []
  }
  await loadSourcePrefill()
  try {
    rdProjects.value = await getRdProjects()
    rdProjectOptions.value = rdProjects.value
      .filter((p) => p.status === 'active')
      .map((p) => ({ label: `${p.code} ${p.name}`, value: p.id }))
  } catch {
    rdProjects.value = []
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
    message.warning('请输入大于零的资产原值')
    return
  }
  if ((form.value.residualValue || 0) >= (form.value.originalValue || 0)) {
    message.warning('预计净残值必须小于资产原值')
    return
  }
  if (!form.value.acquisitionDate) {
    message.warning('请选择取得日期')
    return
  }
  if (!form.value.availableForUseDate) {
    message.warning('请选择可供使用日期（次月起计提折旧）')
    return
  }
  const cat = currentCategory.value
  if (cat && (form.value.usefulLifeMonths || 0) < cat.lifeMinMonths) {
    message.warning(`${cat.name}折旧年限不得少于 ${cat.lifeMinMonths} 个月`)
    return
  }
  if (form.value.depreciationMethod === 'one_time') {
    if (cat && !cat.oneTimeAllowed) {
      message.warning(`${cat.name}不适用一次性折旧政策`)
      return
    }
    if ((form.value.originalValue || 0) > 5000000) {
      message.warning('单位价值超过 500 万元的资产不适用一次性折旧政策')
      return
    }
    if ((form.value.residualValue || 0) > 0) {
      message.warning('一次性折旧要求预计净残值为零')
      return
    }
  }
  if ((form.value.rdUseRate || 0) > 0 && (!form.value.rdProjectId || !form.value.rdAllocationBasis?.trim())) {
    message.warning('研发使用资产必须选择研发项目并填写分摊依据')
    return
  }
  submitting.value = true
  try {
    const payload: FixedAssetCreateRequest = {
      ...form.value,
      acquisitionDate: form.value.acquisitionDate,
      availableForUseDate: form.value.availableForUseDate,
    }
    if (!payload.residualValue) delete payload.residualValue
    if (sourceEntryId.value) {
      const result = await createFixedAssetFromEntry({
        ...payload,
        sourceEntryId: sourceEntryId.value,
      })
      handleCreated(result)
    } else {
      await createFixedAsset(payload)
      message.success('资产已保存为草稿，提交审核后将生成取得凭证（借固定资产，贷银行存款等）')
      router.push('/assets')
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
        <NButton text class="back-btn" @click="router.push('/assets')">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回
        </NButton>
        <div class="title-section">
          <h2>{{ sourceEntryId ? '确认固定资产与折旧方案' : '录入固定资产' }}</h2>
          <p class="subtitle">
            <template v-if="sourceEntryId">原交易已经入账，这里只建立资产卡片，不会重复生成取得凭证。固定资产自可供使用<b>次月</b>起计提折旧。</template>
            <template v-else>录入外购或自建的固定资产。自可供使用<b>次月</b>起计提折旧（与无形资产当月摊销不同），处置当月停止计提。</template>
          </p>
        </div>
      </div>
    </div>

    <div v-if="sourceEntryId" class="source-anchor">
      <div class="anchor-head">
        <div class="anchor-badge">来源交易核对</div>
        <span class="anchor-note">原支出交易已入账，本次仅确立折旧摊销规则，不会重复生成资金凭证。</span>
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
        <div class="fact-item fact-amount">
          <span class="fact-label">价税合计金额</span>
          <strong class="fact-val tabular amount-highlight">{{ formatMoney(sourcePrefill?.originalValue ?? form.originalValue ?? 0) }}</strong>
        </div>
      </div>
    </div>

    <div class="workspace-grid">
      <NCard :bordered="false" class="form-card">
        <NForm label-placement="top" class="fixed-asset-form">
          <div class="section-heading">1. 核心方案配置</div>
          <NGrid :cols="2" :x-gap="20">
            <NGridItem :span="2">
              <NFormItem label="资产名称">
                <NInput v-model:value="form.name" placeholder="如：XX 台式电脑、XX 乘用车、XX 办公家具" />
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
                  placeholder="购置日/竣工日/投入使用日"
                  @update:formatted-value="handleAcquisitionDateChange"
                />
              </NFormItem>
            </NGridItem>

            <NGridItem :span="1">
              <NFormItem label="可供使用日期（次月起计提）">
                <NDatePicker
                  v-model:formatted-value="form.availableForUseDate"
                  value-format="yyyy-MM-dd"
                  type="date"
                  style="width: 100%;"
                  placeholder="默认与取得日期一致"
                />
                <template #feedback>
                  <span class="form-tip">固定资产自<b>可供使用次月</b>起计提折旧。</span>
                </template>
              </NFormItem>
            </NGridItem>

            <NGridItem v-if="!sourceEntryId" :span="1">
              <NFormItem label="资产原值（元）">
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
              <NFormItem label="折旧方法">
                <NSelect
                  :value="form.depreciationMethod"
                  :options="[
                    { label: '年限平均法（直线法）', value: 'straight_line' },
                    { label: '一次性折旧（≤500 万设备器具）', value: 'one_time', disabled: !currentCategory?.oneTimeAllowed },
                  ]"
                  @update:value="handleDepreciationMethodChange"
                />
                <template #feedback>
                  <span class="form-tip">一次性折旧：单位价值不超过 500 万元的设备、器具可一次性计入当期成本费用（残值须为零）。</span>
                </template>
              </NFormItem>
            </NGridItem>

            <NGridItem v-if="form.depreciationMethod === 'straight_line'" :span="1">
              <NFormItem label="折旧年限（月）">
                <NInputNumber
                  v-model:value="form.usefulLifeMonths"
                  :min="currentCategory?.lifeMinMonths ?? 12"
                  :max="currentCategory?.lifeMaxMonths ?? 600"
                  style="width: 100%;"
                />
              </NFormItem>
            </NGridItem>

            <NGridItem v-if="form.depreciationMethod === 'straight_line'" :span="1">
              <NFormItem>
                <template #label>
                  <div class="label-with-pills">
                    <span>预计净残值率（%）</span>
                    <div class="rate-pills">
                      <button type="button" class="rate-pill" :class="{ active: (form.residualRate || 0) === 0 }" @click="syncResidualFromRate(0)">0% 无残值</button>
                      <button type="button" class="rate-pill" :class="{ active: form.residualRate === 5 }" @click="syncResidualFromRate(5)">5% 常用残值</button>
                    </div>
                  </div>
                </template>
                <NInputNumber
                  :value="form.residualRate"
                  :min="0"
                  :max="100"
                  :step="0.5"
                  :precision="2"
                  style="width: 100%;"
                  placeholder="常用 5%"
                  @update:value="syncResidualFromRate"
                />
                <template #feedback>
                  <span class="form-tip">与净残值金额自动换算；常用 5%，默认 0%。</span>
                </template>
              </NFormItem>
            </NGridItem>

            <NGridItem v-if="form.depreciationMethod === 'straight_line'" :span="1">
              <NFormItem label="预计净残值（元，选填）">
                <NInputNumber
                  :value="form.residualValue"
                  :min="0"
                  :step="100"
                  :precision="2"
                  style="width: 100%;"
                  placeholder="默认为 0"
                  @update:value="syncResidualFromAmount"
                />
                <template #feedback>
                  <span class="form-tip">修改比例或金额，另一项自动更新。</span>
                </template>
              </NFormItem>
            </NGridItem>
          </NGrid>

          <div class="advanced-section">
            <button type="button" class="advanced-toggle" @click="showAdvanced = !showAdvanced">
              <span>2. 辅助与归集信息（存放地点 / 保管人 / 研发加计扣除分摊）</span>
              <span class="toggle-hint">{{ showAdvanced ? '收起' : '展开选填项' }}</span>
            </button>

            <div v-if="showAdvanced" class="advanced-body">
              <NGrid :cols="2" :x-gap="16">
                <NGridItem>
                  <NFormItem label="存放地点">
                    <NInput v-model:value="form.location" placeholder="选填，如：办公室 A302" />
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
                      <NInput v-model:value="form.rdAllocationBasis" placeholder="例如：设备使用日志，研发使用时长占60%" />
                    </NFormItem>
                  </NGridItem>
                </NGrid>
              </div>
            </div>
          </div>
        </NForm>

        <div class="form-footer">
          <NSpace justify="end" size="medium">
            <NButton @click="router.push('/assets')">取消</NButton>
            <NButton type="primary" :loading="submitting" @click="handleSubmit">
              {{ sourceEntryId ? '确认建立折旧方案' : '保存草稿' }}
            </NButton>
          </NSpace>
        </div>
      </NCard>

      <aside class="preview-sidebar">
        <div class="depreciation-preview-board">
          <div class="preview-metric">
            <span class="metric-label">{{ form.depreciationMethod === 'one_time' ? '预计一次性计提额' : '预计月折旧额' }}</span>
            <div class="metric-value tabular">
              {{ formatMoney(calc.monthly) }}
              <span v-if="form.depreciationMethod !== 'one_time'" class="metric-unit">/月</span>
            </div>
            <div class="metric-sub tabular">
              应折旧总额 {{ formatMoney(calc.base) }}（原值 {{ formatMoney(form.originalValue || 0) }} - 净残值 {{ formatMoney(form.residualValue || 0) }}）
            </div>
          </div>

          <div class="preview-block">
            <div class="block-title">计提时间轴</div>
            <div class="timeline-list">
              <div class="timeline-row">
                <span class="calc-label">可供使用月份</span>
                <span class="calc-val tabular">{{ calc.usePeriod }}</span>
              </div>
              <div class="timeline-row">
                <span class="calc-label">首期计提（次月）</span>
                <span class="calc-val tabular">{{ calc.startPeriod }}</span>
              </div>
              <div class="timeline-row">
                <span class="calc-label">预计计提完毕</span>
                <span class="calc-val tabular">{{ calc.endPeriod }}</span>
              </div>
            </div>
          </div>

          <div class="preview-block compliance-box">
            <div class="block-title">财税合规与税优提示</div>
            <p class="compliance-text">
              {{ lifeTip || '固定资产自可供使用次月起按月计提折旧，处置当月停止计提。' }}
            </p>
            <div v-if="canOneTimeDeduct" class="tax-perk-tip">
              <span>当前资产金额不超过 500 万元且类别符合，可适用一次性税前扣除政策，享受所得税延缓优惠。</span>
              <button
                v-if="form.depreciationMethod !== 'one_time'"
                type="button"
                class="perk-switch-btn"
                @click="handleDepreciationMethodChange('one_time')"
              >
                切换为一次性折旧
              </button>
            </div>
          </div>
        </div>
      </aside>
    </div>

    <NModal
      v-model:show="showDueModal"
      title="跨期历史折旧一键补提"
      preset="card"
      :closable="false"
      :mask-closable="false"
      style="width: 520px; border-radius: var(--solo-radius-lg);"
    >
      <p style="margin: 0 0 12px; color: var(--solo-text-secondary);">
        固定资产卡片已建立。检测到共有 <b>{{ duePeriods.length }}</b> 期历史月份需补提折旧，确认后将直接生成各月折旧凭证：
      </p>
      <div
        v-for="item in duePeriods"
        :key="item.period"
        class="catchup-row"
      >
        <span class="tabular">{{ item.period }} 折旧凭证</span>
        <strong class="tabular">{{ formatMoney(item.amount) }}</strong>
      </div>
      <div class="catchup-total">
        <span>补提合计</span>
        <strong class="tabular">{{ formatMoney(dueTotalAmount) }}</strong>
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="deferCatchUp">稍后在月结向导中处理</NButton>
          <NButton type="primary" :loading="runningCatchUp" @click="directCatchUp">一键补提折旧入账</NButton>
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
.tabular {
  font-variant-numeric: tabular-nums;
}
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
.subtitle b { color: var(--solo-primary); font-weight: 600; }

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
.anchor-note {
  font-size: 12.5px;
  color: var(--solo-text-secondary);
}
.anchor-facts {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 12px;
  padding-top: 10px;
  border-top: 1px dashed var(--solo-border);
}
.fact-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.fact-label {
  font-size: 11.5px;
  color: var(--solo-text-muted);
}
.fact-val {
  font-size: 13.5px;
  font-weight: 600;
  color: var(--solo-text-primary);
}
.amount-highlight {
  font-size: 16px;
  font-weight: 700;
  color: var(--solo-primary);
}

.workspace-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.85fr) minmax(300px, 1fr);
  gap: 20px;
  align-items: start;
}
@media (max-width: 900px) {
  .workspace-grid {
    grid-template-columns: 1fr;
  }
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
.fixed-asset-form { margin-top: 4px; }

.label-with-pills {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  gap: 8px;
}
.rate-pills {
  display: inline-flex;
  gap: 6px;
}
.rate-pill {
  padding: 2px 8px;
  font-size: 11.5px;
  border-radius: 4px;
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-soft);
  color: var(--solo-text-secondary);
  cursor: pointer;
}
.rate-pill.active {
  background: var(--solo-bg-blue-soft);
  border-color: var(--solo-primary);
  color: var(--solo-primary);
  font-weight: 600;
}

.advanced-section {
  margin-top: 8px;
  border-top: 1px dashed var(--solo-border-light);
  padding-top: 12px;
}
.advanced-toggle {
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 12px;
  background: var(--solo-bg-soft);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius);
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary);
  cursor: pointer;
}
.toggle-hint {
  font-size: 12px;
  font-weight: 500;
  color: var(--solo-primary);
}
.advanced-body {
  margin-top: 14px;
}
.rd-allocation-box {
  margin: 4px 0 8px;
  padding: 14px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius);
  background: var(--solo-bg-soft);
}
.form-tip {
  font-size: 11px;
  color: var(--solo-text-muted);
  line-height: 1.3;
  display: block;
}
.form-tip b { color: var(--solo-primary); font-weight: 600; }

.preview-sidebar {
  position: sticky;
  top: 20px;
}
.depreciation-preview-board {
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  box-shadow: var(--solo-shadow-sm);
  padding: 18px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.preview-metric {
  padding-bottom: 14px;
  border-bottom: 1px solid var(--solo-border-light);
}
.metric-label {
  font-size: 12px;
  font-weight: 600;
  color: var(--solo-text-secondary);
}
.metric-value {
  margin-top: 4px;
  font-size: 26px;
  font-weight: 800;
  color: var(--solo-income);
  line-height: 1.2;
}
.metric-unit {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-secondary);
  margin-left: 2px;
}
.metric-sub {
  margin-top: 6px;
  font-size: 12px;
  color: var(--solo-text-muted);
}
.preview-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.block-title, .calc-title {
  font-size: 12px;
  font-weight: 700;
  color: var(--solo-primary);
}
.timeline-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.timeline-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-size: 12.5px;
}
.calc-label { color: var(--solo-text-secondary); }
.calc-val {
  font-weight: 600;
  color: var(--solo-text-primary);
}
.compliance-box {
  background: var(--solo-bg-blue-soft);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius);
  padding: 12px;
}
.compliance-text {
  margin: 0;
  font-size: 12px;
  color: var(--solo-text-secondary);
  line-height: 1.45;
}
.tax-perk-tip {
  margin-top: 6px;
  padding-top: 8px;
  border-top: 1px dashed var(--solo-border);
  font-size: 12px;
  color: var(--solo-income);
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.perk-switch-btn {
  align-self: flex-start;
  padding: 3px 10px;
  font-size: 12px;
  font-weight: 600;
  color: var(--solo-primary);
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-primary);
  border-radius: 4px;
  cursor: pointer;
}

.catchup-row {
  display: flex;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: 1px dashed var(--solo-border-light);
  font-size: 13px;
}
.catchup-total {
  display: flex;
  justify-content: space-between;
  padding-top: 12px;
  font-size: 14px;
  color: var(--solo-text-primary);
}
.form-footer {
  margin-top: 24px;
  padding-top: 16px;
  border-top: 1px dashed var(--solo-border-light);
}
</style>
