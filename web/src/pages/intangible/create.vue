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
import type {
  AmortizationCatchUpResult,
  IntangibleAmortizationDuePeriod,
  IntangibleAssetCreateRequest,
  IntangibleAssetFromEntryResponse,
  IntangibleCategory,
} from '@/api/types'

const route = useRoute()
const router = useRouter()
const message = useMessage()

const submitting = ref(false)
const categories = ref<IntangibleCategory[]>([])
const rdProjects = ref<{ id: string; code: string; name: string; status: string }[]>([])
const rdProjectOptions = ref<{ label: string; value: string }[]>([])
const residualReasonChecked = ref(false)
const sourceEntryId = computed(() => typeof route.query.sourceEntryId === 'string' ? route.query.sourceEntryId : '')
const createdAssetId = ref('')
const duePeriods = ref<IntangibleAmortizationDuePeriod[]>([])
const dueTotalAmount = ref(0)
const showDueModal = ref(false)
const showCatchUpPreview = ref(false)
const catchUpPreview = ref<AmortizationCatchUpResult | null>(null)
const previewingCatchUp = ref(false)
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
  if (cat) {
    form.value.usefulLifeMonths = cat.defaultLifeMonths
  }
}

function handleAcquisitionDateChange(val: string | null) {
  if (val && !form.value.availableForUseDate) {
    form.value.availableForUseDate = val
  }
}

function addMonths(period: string, months: number): string {
  const y = Number(period.slice(0, 4))
  const m = Number(period.slice(5, 7))
  const total = y * 12 + (m - 1) + months
  return `${String(Math.floor(total / 12)).padStart(4, '0')}-${String((total % 12) + 1).padStart(2, '0')}`
}

const calc = computed(() => {
  const original = form.value.originalValue || 0
  const residual = form.value.residualValue || 0
  const base = Math.max(0, original - residual)
  const months = form.value.usefulLifeMonths || 0
  const monthly = months > 0 ? base / months : 0
  const start = form.value.availableForUseDate || form.value.acquisitionDate || ''
  return {
    base,
    monthly,
    startPeriod: start ? start.slice(0, 7) : '—',
    endPeriod: start && months > 0 ? addMonths(start.slice(0, 7), months - 1) : '—',
  }
})

async function loadSourcePrefill() {
  if (!sourceEntryId.value) return
  try {
    const prefill = await getIntangibleAssetEntryPrefill(sourceEntryId.value)
    if (prefill.existingAssetId) {
      message.info('这笔交易已经建立无形资产卡片')
      await router.replace(`/intangibles/${prefill.existingAssetId}`)
      return
    }
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
  message.success('无形资产卡片已建立')
  router.push(`/intangibles/${result.asset.id}`)
}

async function previewCatchUp() {
  if (!createdAssetId.value) return
  previewingCatchUp.value = true
  try {
    catchUpPreview.value = await runIntangibleAmortizationCatchUp({
      throughPeriod: currentPeriod(),
      assetIds: [createdAssetId.value],
      dryRun: true,
    })
    showDueModal.value = false
    showCatchUpPreview.value = true
  } catch (err: any) {
    message.error(err?.response?.data?.detail?.message || err?.response?.data?.detail || '预览摊销失败')
  } finally {
    previewingCatchUp.value = false
  }
}

async function confirmCatchUp() {
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
    showCatchUpPreview.value = false
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
  if (!form.value.acquisitionDate) {
    message.warning('请选择取得日期')
    return
  }
  if (!form.value.availableForUseDate) {
    message.warning('请选择可供使用日期（摊销起点）')
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
      router.push('/intangibles')
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
        <NButton text class="back-btn" @click="router.push('/intangibles')">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回
        </NButton>
        <div class="title-section">
          <h2>{{ sourceEntryId ? '确认无形资产与摊销方案' : '录入无形资产' }}</h2>
          <p class="subtitle">
            <template v-if="sourceEntryId">原交易已经入账，这里只建立资产卡片，不会重复生成取得凭证。无形资产自可供使用当月起摊销。</template>
            <template v-else>录入外购或研发资本化的无形资产。自可供使用当月起摊销（与固定资产次月起提不同），处置当月停止摊销。</template>
          </p>
        </div>
      </div>
    </div>

    <NCard :bordered="false" class="form-card">
      <NForm label-placement="top" class="intangible-form">
        <NGrid :cols="2" :x-gap="20">
          <NGridItem :span="2">
            <NFormItem label="资产名称">
              <NInput v-model:value="form.name" placeholder="如：XX 商标权、XX 管理软件、XX 实用新型专利" />
            </NFormItem>
          </NGridItem>

          <NGridItem :span="1">
            <NFormItem label="资产类别">
              <NSelect v-model:value="form.category" :options="categoryOptions" @update:value="handleCategoryChange" />
              <template #feedback>
                <span class="form-tip">{{ lifeTip }}</span>
              </template>
            </NFormItem>
          </NGridItem>

          <NGridItem :span="1">
            <NFormItem label="取得方式">
              <NSelect v-model:value="form.acquisitionType" :options="acquisitionTypeOptions" :disabled="!!sourceEntryId" />
            </NFormItem>
          </NGridItem>

          <NGridItem :span="1">
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
            <NFormItem label="可供使用日期（摊销起点）">
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

          <NGridItem :span="1">
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
            <NFormItem label="预计净残值（元）">
              <NInputNumber v-model:value="form.residualValue" :min="0" :step="100" style="width: 100%;" placeholder="默认为 0" />
              <template #feedback>
                <span class="form-tip">
                  <NCheckbox v-model:checked="residualReasonChecked" size="small">
                    存在第三方回购承诺或活跃市场定价（否则残值视为零）
                  </NCheckbox>
                </span>
              </template>
            </NFormItem>
          </NGridItem>

          <NGridItem :span="1">
            <NFormItem :label="certLabel">
              <NInput v-model:value="form.legalCertNo" placeholder="选填" />
            </NFormItem>
          </NGridItem>

          <NGridItem :span="1">
            <NFormItem label="权利到期日">
              <NDatePicker v-model:formatted-value="form.expiryDate" value-format="yyyy-MM-dd" type="date" clearable style="width: 100%;" placeholder="专利/商标/土地/合同到期日" />
            </NFormItem>
          </NGridItem>

          <NGridItem :span="2">
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
          </NGridItem>

          <NGridItem :span="1">
            <div class="calculation-preview-box">
              <div class="calc-title">预估摊销测算</div>
              <div class="calc-row">
                <span class="calc-label">应摊销总额:</span>
                <span class="calc-val">{{ formatMoney(calc.base) }}</span>
              </div>
              <div class="calc-row">
                <span class="calc-label">预计月摊销额:</span>
                <span class="calc-val highlighted-val">{{ formatMoney(calc.monthly) }}</span>
              </div>
              <div class="calc-row">
                <span class="calc-label">摊销期间:</span>
                <span class="calc-val">{{ calc.startPeriod }} ~ {{ calc.endPeriod }}</span>
              </div>
            </div>
          </NGridItem>
        </NGrid>
      </NForm>

      <div class="form-footer">
        <NSpace justify="end" size="medium">
          <NButton @click="router.push('/intangibles')">取消</NButton>
          <NButton type="primary" :loading="submitting" @click="handleSubmit">
            {{ sourceEntryId ? '建立资产卡片' : '保存草稿' }}
          </NButton>
        </NSpace>
      </div>
    </NCard>

    <NModal
      v-model:show="showDueModal"
      title="无形资产已建档"
      preset="card"
      :closable="false"
      :mask-closable="false"
      style="width: 520px; border-radius: var(--solo-radius-lg);"
    >
      <p style="margin: 0 0 10px; color: var(--solo-text-secondary);">
        还有历史摊销未计提，共 {{ duePeriods.length }} 期，合计 {{ formatMoney(dueTotalAmount) }}。
      </p>
      <div
        v-for="item in duePeriods"
        :key="item.period"
        style="display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px dashed var(--solo-border-light);"
      >
        <span>{{ item.period }} · {{ item.count }} 项资产</span>
        <strong>{{ formatMoney(item.amount) }}</strong>
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="deferCatchUp">稍后处理</NButton>
          <NButton type="primary" :loading="previewingCatchUp" @click="previewCatchUp">立即摊销</NButton>
        </NSpace>
      </template>
    </NModal>

    <NModal
      v-model:show="showCatchUpPreview"
      title="补提摊销预览"
      preset="card"
      style="width: 520px; border-radius: var(--solo-radius-lg);"
    >
      <div v-if="catchUpPreview">
        <div
          v-for="item in catchUpPreview.periods"
          :key="item.period"
          style="display: flex; justify-content: space-between; padding: 6px 0; border-bottom: 1px dashed var(--solo-border-light);"
        >
          <span>{{ item.period }} · {{ item.processedCount }} 项</span>
          <strong>{{ formatMoney(item.totalAmount) }}</strong>
        </div>
        <div style="display: flex; justify-content: space-between; padding-top: 12px;">
          <span>合计</span>
          <strong>{{ formatMoney(catchUpPreview.totalAmount) }}</strong>
        </div>
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showCatchUpPreview = false">取消</NButton>
          <NButton type="primary" :loading="runningCatchUp" @click="confirmCatchUp">确认摊销</NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.create-page {
  max-width: 640px;
  margin: 0 auto;
  padding: 16px 0 48px 0;
}
.page-header { margin-bottom: 24px; }
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
.form-card {
  background: var(--solo-bg-card);
  backdrop-filter: blur(12px);
  border: 1px solid var(--solo-border-glass);
  border-radius: var(--solo-radius-lg);
  box-shadow: var(--solo-shadow-lg);
  padding: 8px;
}
.intangible-form { margin-top: 8px; }
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
  display: block;
}
.form-tip b { color: var(--solo-primary); font-weight: 600; }
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
.calc-label { color: var(--solo-text-secondary); }
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
