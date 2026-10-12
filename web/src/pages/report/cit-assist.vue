<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NButton, NCard, NCheckbox, NCollapse, NCollapseItem, NSelect, NSpin, useMessage } from 'naive-ui'
import { getCitAssist } from '@/api/report'
import { formatMoney } from '@/utils/format'
import type { CitAssistResponse } from '@/api/types'

const router = useRouter()
const message = useMessage()
const loading = ref(false)
const result = ref<CitAssistResponse | null>(null)
const costConfirmed = ref(false)
const profitConfirmed = ref(false)
const assetLossChoice = ref<'yes' | 'no' | null>(null)

function previousCompletedQuarter() {
  const now = new Date()
  const q = Math.floor(now.getMonth() / 3) + 1
  return q === 1 ? `${now.getFullYear() - 1}-Q4` : `${now.getFullYear()}-Q${q - 1}`
}

const period = ref(previousCompletedQuarter())
const periodOptions = computed(() => {
  const options: { label: string; value: string }[] = []
  const now = new Date()
  let year = now.getFullYear()
  let quarter = Math.floor(now.getMonth() / 3)
  if (!quarter) { year--; quarter = 4 }
  for (let i = 0; i < 8; i++) {
    options.push({ label: `${year}年第${quarter}季度`, value: `${year}-Q${quarter}` })
    if (--quarter === 0) { year--; quarter = 4 }
  }
  return options
})

async function load() {
  loading.value = true
  try {
    result.value = await getCitAssist(period.value, {
      operatingCostConfirmed: costConfirmed.value,
      totalProfitConfirmed: profitConfirmed.value,
      assetLoss: assetLossChoice.value === null ? null : assetLossChoice.value === 'yes',
      manualAdjustmentsConfirmed: true,
    })
  } catch {
    message.error('加载企业所得税数据失败')
  } finally {
    loading.value = false
  }
}

async function confirmAndRecalculate() {
  await load()
  if (result.value?.beginnerReady) message.success('确认完成，请对照电子税务局复核申报')
}

onMounted(load)
</script>

<template>
  <div class="report-container">
    <div class="report-header">
      <div class="report-header-left">
        <NButton text class="report-back-btn" @click="router.push('/reports')">
          <template #icon><i class="ri-arrow-left-line" /></template>返回
        </NButton>
        <div class="report-title-section">
          <h2>企业所得税申报辅助</h2>
          <p class="report-subtitle">账务数据已自动准备，你只需核对两项金额并确认是否发生资产损失</p>
        </div>
      </div>
      <NSelect v-model:value="period" :options="periodOptions" style="width:150px" @update:value="load" />
    </div>

    <NSpin :show="loading">
      <template v-if="result">
        <NCard :bordered="false" class="result-card mb-4">
          <div class="result-icon" :class="Number(result.estimatedTax) === 0 ? 'safe' : 'payable'">
            <i :class="Number(result.estimatedTax) === 0 ? 'ri-checkbox-circle-line' : 'ri-money-cny-circle-line'" />
          </div>
          <div>
            <h3>{{ Number(result.estimatedTax) === 0 ? '预计本季度无需预缴企业所得税' : `预计本期应补 ${formatMoney(result.estimatedTax)}` }}</h3>
            <p>系统根据已过账凭证和季度财务报表自动计算，提交前完成下方确认即可。</p>
          </div>
        </NCard>

        <section class="auto-grid mb-4">
          <div><span>营业收入</span><strong>{{ formatMoney(result.cumulativeRevenue) }}</strong><small>来自利润表</small></div>
          <div><span>营业成本</span><strong>{{ formatMoney(result.cumulativeOperatingCost) }}</strong><small>来自利润表</small></div>
          <div><span>利润总额</span><strong :class="Number(result.cumulativeProfit) < 0 ? 'expense' : ''">{{ formatMoney(result.cumulativeProfit) }}</strong><small>来自利润表</small></div>
          <div><span>季末资产总额</span><strong>{{ Number(result.assetTotalEnd).toFixed(2) }} 万元</strong><small>来自资产负债表</small></div>
        </section>

        <NCard v-if="!result.companyTaxProfileComplete" :bordered="false" class="profile-card mb-4">
          <div><strong>企业税务信息还没完善</strong><p>请在主体管理中确认限制行业和从业人数，之后每季度无需重复填写。</p></div>
          <NButton type="primary" secondary @click="router.push('/mine/companies')">前往主体管理</NButton>
        </NCard>

        <NCard :bordered="false" class="report-card mb-4">
          <h3 class="section-title">请确认两项金额</h3>
          <label class="confirm-row">
            <NCheckbox v-model:checked="costConfirmed" />
            <span>营业成本是 <strong>{{ formatMoney(result.cumulativeOperatingCost) }}</strong>，与已报送财务报表一致</span>
          </label>
          <label class="confirm-row">
            <NCheckbox v-model:checked="profitConfirmed" />
            <span>利润总额是 <strong>{{ formatMoney(result.cumulativeProfit) }}</strong>，与已报送财务报表一致</span>
          </label>
        </NCard>

        <NCard :bordered="false" class="report-card mb-4">
          <h3 class="section-title">本季度是否发生资产损失？</h3>
          <p class="help-text">例如存货报废、坏账损失、固定资产损毁。普通费用支出不属于资产损失。</p>
          <NSelect v-model:value="assetLossChoice" :options="[{label:'没有',value:'no'},{label:'有，需要进一步处理',value:'yes'}]" placeholder="请选择" style="max-width:320px" />
          <div v-if="assetLossChoice === 'yes'" class="asset-warning">需要准备资产损失相关资料；系统暂不把本期标记为可申报。</div>
        </NCard>

        <div class="action-row">
          <NButton type="primary" size="large" :disabled="!costConfirmed || !profitConfirmed || assetLossChoice === null || !result.companyTaxProfileComplete" :loading="loading" @click="confirmAndRecalculate">
            确认并生成申报结果
          </NButton>
        </div>

        <NCard :bordered="false" class="report-card detail-card">
          <NCollapse>
            <NCollapseItem title="查看系统计算明细（专业内容）" name="details">
              <div class="meta-line"><span>季初资产总额</span><strong>{{ Number(result.assetTotalStart).toFixed(2) }} 万元</strong></div>
              <div class="meta-line"><span>标准应纳所得税额</span><strong>{{ formatMoney(result.standardTax) }}</strong></div>
              <div class="meta-line"><span>减免所得税额</span><strong>{{ formatMoney(result.taxReduction) }}</strong></div>
              <table class="detail-table">
                <thead><tr><th>行次</th><th>项目</th><th>本年累计</th></tr></thead>
                <tbody><tr v-for="field in result.filingFields" :key="field.line"><td>{{ field.line }}</td><td>{{ field.label }}</td><td>{{ formatMoney(field.amount) }}</td></tr></tbody>
              </table>
            </NCollapseItem>
          </NCollapse>
        </NCard>
      </template>
    </NSpin>
  </div>
</template>

<style scoped>
.mb-4{margin-bottom:16px}.result-card{display:flex;gap:16px;align-items:center}.result-card h3{margin:0 0 5px;font-size:18px}.result-card p,.profile-card p,.help-text{margin:0;color:var(--solo-text-muted);font-size:13px}.result-icon{display:grid;place-items:center;width:48px;height:48px;border-radius:14px;font-size:25px}.result-icon.safe{color:var(--solo-income);background:rgba(16,185,129,.1)}.result-icon.payable{color:var(--solo-expense);background:rgba(239,68,68,.08)}.auto-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px}.auto-grid>div{display:flex;flex-direction:column;gap:6px;padding:16px;background:var(--solo-bg-card);border:1px solid var(--solo-border-glass);border-radius:var(--solo-radius-lg)}.auto-grid span,.auto-grid small{color:var(--solo-text-muted);font-size:12px}.auto-grid strong{font-size:17px}.profile-card{display:flex;align-items:center;justify-content:space-between;gap:16px;border:1px solid rgba(245,158,11,.3)!important}.section-title{margin:0 0 12px;font-size:16px}.confirm-row{display:flex;align-items:center;gap:10px;padding:12px 0;border-bottom:1px solid var(--solo-border-light);font-size:14px}.confirm-row:last-child{border-bottom:0}.help-text{margin-bottom:12px}.asset-warning{margin-top:12px;padding:10px 12px;border-radius:8px;color:var(--solo-expense);background:rgba(239,68,68,.06);font-size:13px}.action-row{display:flex;justify-content:flex-end;margin:18px 0}.detail-card{margin-top:8px}.meta-line{display:flex;justify-content:space-between;padding:8px 0}.detail-table{width:100%;border-collapse:collapse;margin-top:12px}.detail-table th,.detail-table td{padding:8px;border-bottom:1px solid var(--solo-border-light);text-align:left;font-size:12px}.detail-table th:last-child,.detail-table td:last-child{text-align:right}.expense{color:var(--solo-expense)}@media(max-width:800px){.auto-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.profile-card{align-items:flex-start;flex-direction:column}}@media(max-width:520px){.auto-grid{grid-template-columns:1fr}}
</style>
