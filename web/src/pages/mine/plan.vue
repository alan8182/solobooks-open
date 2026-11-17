<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NProgress,
  NSpin,
  NTag,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { useUsageStore } from '@/stores/usage'
import {
  createBillingOrder,
  fetchBillingOrder,
  fetchBillingOrders,
  fetchPricing,
  type BillingOrder,
  type BillingOrderStatus,
  type PricingCatalog,
  type PricingItem,
} from '@/api/billing'
import { SoloStatusBadge } from '@/components/ui'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const usageStore = useUsageStore()

const pricing = ref<PricingCatalog | null>(null)
const pricingLoading = ref(false)
const orders = ref<BillingOrder[]>([])
const ordersLoading = ref(false)
const creatingPlan = ref('')
const pollingOrderNo = ref('')

// ── 当前套餐与用量 ──
const quotaInfo = computed(() => usageStore.summary?.quota ?? null)
const currentMonth = computed(() => usageStore.summary?.currentMonth ?? null)
const currentPlanCode = computed(() => quotaInfo.value?.planCode ?? 'trial')
const effectiveLimitTokens = computed(() => {
  const limit = quotaInfo.value?.limitTokens
  if (limit === null || limit === undefined) return null
  return limit + (quotaInfo.value?.bonusTokens ?? 0)
})
const quotaPercent = computed(() => {
  const limit = effectiveLimitTokens.value
  const used = quotaInfo.value?.usedTokens ?? 0
  if (!limit || limit <= 0) return 0
  return Math.min(100, Math.round((used / limit) * 100))
})

function formatTokens(n: number) {
  if (n >= 10000) return `${(n / 10000).toFixed(1)}w`
  return n.toLocaleString('zh-CN')
}

function formatPriceDisplay(raw?: string | null, fallback = '') {
  if (!raw) return fallback
  const num = Number(raw)
  if (!Number.isFinite(num)) return raw
  return Number.isInteger(num) ? num.toLocaleString('zh-CN') : raw
}

// ── 套餐方案展示目录（价格严格取后端 /api/billing/pricing 唯一真相）──
interface PlanCardItem {
  code: 'trial' | 'standard' | 'pro'
  badge: string
  name: string
  tagline: string
  fallbackPrice: string
  unit: string
  recommended?: boolean
  features: { text: string; included: boolean }[]
}

const PLAN_CARDS: PlanCardItem[] = [
  {
    code: 'trial',
    badge: '默认入门 · 零门槛体验',
    name: '云端免费试用版',
    tagline: '免部署开箱即用，功能与标准版一致，每月 10 万 Token · 最多 3 个账套',
    fallbackPrice: '0',
    unit: '基础免费',
    features: [
      { text: '全功能开放：AI 记账、三张报表与申报小抄（最多 3 个公司账套）', included: true },
      { text: '每月 10 万 Token 体验额度（含多模态票据识别与问答）', included: true },
      { text: '银行流水、微信 / 支付宝账单与发票自动解析导入', included: true },
      { text: '1 对 1 历史旧账导入（需自助导入）', included: false },
      { text: '专属服务群与会计师季度审账', included: false },
    ],
  },
  {
    code: 'standard',
    badge: '主力推荐 · 每日仅 ¥1.6',
    name: '云端标准版',
    tagline: '专业财务会计师 1 对 1 帮你把旧账导进来，年享 500 万 Token 额度',
    fallbackPrice: '598',
    unit: '元 / 年',
    recommended: true,
    features: [
      { text: '每年 500 万 AI Token 额度（含文本问答与多模态票据识别）', included: true },
      { text: '最多支持 3 个公司账套（更多账套将通过未来代账版开放）', included: true },
      { text: '1 对 1 历史旧账导入与期初建账（注册财务会计师负责）', included: true },
      { text: '专属问题服务群（系统实操指导 + 日常财税问题响应）', included: true },
      { text: '老板私卡垫资与免税提现冲销、研发辅助账与申报小抄', included: true },
    ],
  },
  {
    code: 'pro',
    badge: '人机双签 · 会计师季度护航',
    name: '人机双签版',
    tagline: 'AI 处理日常流水，中级注册财务会计师每季度审账并出具分析报告',
    fallbackPrice: '2,980',
    unit: '元 / 年',
    features: [
      { text: '含标准版全部软件功能、每年 500 万 Token（3 个账套）与 1 对 1 旧账导入', included: true },
      { text: '中级注册财务会计师每季度审账复核（人机双签把关）', included: true },
      { text: '每季度出具专属《季度财务分析报告》', included: true },
      { text: '每季度 30 分钟视频在线问答（1 对 1 筹划与解读）', included: true },
      { text: '日常经营财务问答（合同节税、公转私合规随时问）', included: true },
    ],
  },
]

const pricingByCode = computed<Record<string, PricingItem>>(() => {
  const map: Record<string, PricingItem> = {}
  for (const item of pricing.value?.items ?? []) {
    map[item.planCode] = item
  }
  return map
})

const highlightedPlan = computed(() => {
  const q = route.query.plan
  return typeof q === 'string' && (q === 'standard' || q === 'pro') ? q : ''
})

function extractErrorMessage(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
  if (typeof detail === 'string' && detail) return detail
  if (detail && typeof detail === 'object' && 'message' in detail) {
    const msg = (detail as { message?: unknown }).message
    if (typeof msg === 'string' && msg) return msg
  }
  return fallback
}

async function loadPricing() {
  pricingLoading.value = true
  try {
    pricing.value = await fetchPricing()
  } catch (err: unknown) {
    message.error(extractErrorMessage(err, '获取套餐价格目录失败，请稍后重试'))
  } finally {
    pricingLoading.value = false
  }
}

async function loadOrders() {
  ordersLoading.value = true
  try {
    const res = await fetchBillingOrders(20)
    orders.value = res.items ?? []
  } catch {
    // 订单列表为附属信息，静默降级不阻塞主界面
  } finally {
    ordersLoading.value = false
  }
}

async function handleBuy(planCode: string) {
  if (creatingPlan.value || planCode === 'trial') return
  if (pricing.value && !pricing.value.alipayEnabled) {
    message.info('当前环境暂未开通在线支付宝收款，请联系客服开通套餐')
    return
  }
  creatingPlan.value = planCode
  try {
    const order = await createBillingOrder(planCode)
    message.success('订单已创建，正在跳转支付宝收银台…')
    window.location.href = order.payUrl
  } catch (err: unknown) {
    message.error(extractErrorMessage(err, '创建订单失败，请重试'))
  } finally {
    creatingPlan.value = ''
  }
}

async function pollOrderStatus(orderNo: string) {
  pollingOrderNo.value = orderNo
  try {
    for (let i = 0; i < 40; i++) {
      try {
        const order = await fetchBillingOrder(orderNo)
        if (order.status === 'activated') {
          message.success('支付成功，套餐已生效')
          await Promise.all([usageStore.ensureLoaded(true), loadOrders()])
          return
        }
        if (order.status === 'closed' || order.status === 'failed') {
          message.warning('订单未完成支付；如已付款请联系客服核实')
          await loadOrders()
          return
        }
      } catch {
        // 网络抖动继续轮询
      }
      await new Promise((resolve) => setTimeout(resolve, 2500))
    }
    message.info('订单状态确认中，稍后可点击刷新订单状态')
    await loadOrders()
  } finally {
    pollingOrderNo.value = ''
  }
}

// ── 订单状态映射与表格列 ──
const ORDER_STATUS_META: Record<
  BillingOrderStatus,
  { label: string; tone: 'neutral' | 'info' | 'success' | 'warning' | 'danger' }
> = {
  created: { label: '待支付', tone: 'warning' },
  paid: { label: '已支付处理中', tone: 'info' },
  activated: { label: '已开通', tone: 'success' },
  closed: { label: '已关闭', tone: 'neutral' },
  failed: { label: '异常/失败', tone: 'danger' },
}

function formatDateTime(raw?: string | null): string {
  if (!raw) return '—'
  return raw.replace('T', ' ').slice(0, 19)
}

const orderColumns: DataTableColumns<BillingOrder> = [
  {
    title: '订单号',
    key: 'orderNo',
    minWidth: 190,
    render: (row) => h('span', { class: 'order-mono' }, row.orderNo),
  },
  { title: '订阅套餐', key: 'subject', minWidth: 160 },
  {
    title: '金额',
    key: 'amount',
    width: 110,
    align: 'right',
    render: (row) => `¥${row.amount}`,
  },
  {
    title: '状态',
    key: 'status',
    width: 115,
    render: (row) => {
      const meta = ORDER_STATUS_META[row.status] ?? { label: row.status, tone: 'neutral' as const }
      return h(SoloStatusBadge, { tone: meta.tone, size: 'small' }, { default: () => meta.label })
    },
  },
  {
    title: '创建时间',
    key: 'createdAt',
    width: 165,
    render: (row) => formatDateTime(row.createdAt),
  },
  {
    title: '开通时间',
    key: 'activatedAt',
    width: 165,
    render: (row) => formatDateTime(row.activatedAt),
  },
]

onMounted(() => {
  usageStore.ensureLoaded(true)
  void loadPricing()
  void loadOrders()

  const orderNo = route.query.orderNo
  if (typeof orderNo === 'string' && orderNo) {
    const nextQuery = { ...route.query }
    delete nextQuery.orderNo
    void router.replace({ path: '/mine/plan', query: nextQuery })
    void pollOrderStatus(orderNo)
  }
})
</script>

<template>
  <div class="plan-page">
    <div class="plan-header">
      <div class="plan-header-left">
        <NButton text class="back-btn" @click="router.push('/mine')">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回个人设置
        </NButton>
        <h2>套餐与订阅</h2>
        <p class="plan-subtitle">查看当前账号套餐周期、AI Token 额度消耗、年付升级方案与订阅订单记录</p>
      </div>
    </div>

    <NAlert v-if="pollingOrderNo" type="info" :bordered="false" class="mb-4">
      正在确认支付宝订单 <strong>{{ pollingOrderNo }}</strong> 的支付结果，请稍候…
    </NAlert>

    <!-- 1. 当前套餐与额度概览 -->
    <NCard :bordered="false" class="plan-card-shell mb-4">
      <NSpin :show="usageStore.loading && !quotaInfo" size="small">
        <div class="current-plan-row">
          <div class="current-plan-main">
            <div class="current-plan-title">
              <span class="current-plan-name">{{ quotaInfo?.planName || '免费体验版' }}</span>
              <NTag v-if="quotaInfo?.planExpiresAt" size="small" type="info" :bordered="false">
                {{ quotaInfo.planExpiresAt }} 到期
              </NTag>
              <NTag v-if="quotaInfo?.limitTokens === null" size="small" type="success" :bordered="false">
                当前不限 AI 用量
              </NTag>
              <NTag v-else-if="quotaInfo?.periodType === 'annual'" size="small" type="primary" :bordered="false">
                年付 {{ formatTokens(effectiveLimitTokens ?? 5000000) }} 额度
              </NTag>
              <NTag v-else size="small" :bordered="false">月度体验额度</NTag>
              <NTag v-if="quotaInfo?.maxCompanies != null" size="small" :bordered="false">
                账套 {{ quotaInfo.companyCount ?? 0 }} / {{ quotaInfo.maxCompanies }}
              </NTag>
            </div>
            <p class="current-plan-desc">
              <template v-if="quotaInfo?.limitTokens === null && currentPlanCode === 'trial'">
                当前为自有部署环境，使用您自备的模型服务，AI 对话与票据识别不设月度用量限制（本月已使用 {{ formatTokens(quotaInfo?.usedTokens ?? 0) }} tokens）。
              </template>
              <template v-else-if="quotaInfo?.limitTokens === null">
                当前方案不设 AI Token 用量限制，本月已使用 {{ formatTokens(quotaInfo?.usedTokens ?? 0) }} tokens。
              </template>
              <template v-else-if="quotaInfo?.periodType === 'annual'">
                年付订阅期内每年享 {{ formatTokens(effectiveLimitTokens ?? 5000000) }} Token 额度（含文本问答与多模态票据识别），跨账套共享；最多支持 {{ quotaInfo?.maxCompanies ?? 3 }} 个公司账套（未来将面向代账机构开放更多账套）。到期未续费将自动回落为免费体验版。
              </template>
              <template v-else>
                每月赠送 10 万 Token 体验额度（含文本问答与多模态票据识别），跨账套共享；最多支持 {{ quotaInfo?.maxCompanies ?? 3 }} 个公司账套，升级云端标准版可享每年 500 万额度。
              </template>
            </p>
          </div>
          <div class="current-usage-stat">
            <span class="stat-label">{{ quotaInfo?.periodType === 'annual' ? '本年度已消耗 AI Token' : '本月已消耗 AI Token' }}</span>
            <strong class="stat-value">
              {{ formatTokens(quotaInfo?.usedTokens ?? currentMonth?.totalTokens ?? 0) }}
            </strong>
          </div>
        </div>

        <div v-if="effectiveLimitTokens !== null" class="quota-bar-wrap">
          <div class="quota-meta">
            <span>计费周期：{{ quotaInfo?.periodStart }} ~ {{ quotaInfo?.periodEnd }}</span>
            <span>
              已用 {{ formatTokens(quotaInfo?.usedTokens ?? 0) }} / {{ formatTokens(effectiveLimitTokens) }}
              <template v-if="(quotaInfo?.bonusTokens ?? 0) > 0">
                （含赠送 {{ formatTokens(quotaInfo?.bonusTokens ?? 0) }}）
              </template>
            </span>
          </div>
          <NProgress
            type="line"
            :percentage="quotaPercent"
            :show-indicator="false"
            :height="8"
            border-radius="4px"
            :status="quotaPercent >= 100 ? 'error' : 'default'"
          />
          <div class="quota-tip">
            {{
              quotaPercent >= 100
                ? (currentPlanCode === 'trial'
                  ? '本期 10 万体验额度已用完，请升级云端标准版或等待下个周期自动恢复'
                  : '本年度 AI Token 额度已用完，请联系客服或等待下个订阅周期自动恢复')
                : `本期剩余 ${formatTokens(quotaInfo?.remainingTokens ?? 0)} tokens`
            }}
          </div>
        </div>
      </NSpin>
    </NCard>

    <!-- 2. 订阅方案对比与下单 -->
    <div class="section-head">
      <div>
        <h3>选择适合您的订阅方案</h3>
        <p>支持支付宝年付即时开通权益；如需注册财务会计师 1 对 1 旧账导入或季度审账把关，可按需选择对应方案。</p>
      </div>
    </div>

    <NAlert
      v-if="pricing && !pricing.alipayEnabled"
      type="warning"
      :bordered="false"
      class="mb-4"
    >
      当前暂未开启在线支付通道；如需开通云端标准版或人机双签版服务，请联系客服人工办理。
    </NAlert>

    <NSpin :show="pricingLoading" size="small">
      <div class="tiers-grid mb-6">
        <div
          v-for="tier in PLAN_CARDS"
          :key="tier.code"
          class="tier-card"
          :class="{
            recommended: tier.recommended,
            highlighted: highlightedPlan === tier.code,
            active: currentPlanCode === tier.code,
          }"
        >
          <div class="tier-top">
            <div class="tier-badges">
              <span class="tier-badge" :class="{ brand: tier.recommended }">
                {{ tier.badge }}
              </span>
              <NTag v-if="currentPlanCode === tier.code" size="tiny" type="success" :bordered="false">
                当前方案
              </NTag>
            </div>
            <h4 class="tier-name">{{ pricingByCode[tier.code]?.planName || tier.name }}</h4>
            <p class="tier-tagline">{{ tier.tagline }}</p>
            <div class="tier-price-row">
              <span class="tier-currency">¥</span>
              <span class="tier-price">
                {{ formatPriceDisplay(pricingByCode[tier.code]?.priceAnnual, tier.fallbackPrice) }}
              </span>
              <span class="tier-unit">{{ tier.unit }}</span>
            </div>
          </div>

          <ul class="tier-features">
            <li
              v-for="feat in tier.features"
              :key="feat.text"
              :class="{ muted: !feat.included }"
            >
              <i :class="feat.included ? 'ri-checkbox-circle-fill' : 'ri-close-circle-line'" />
              <span>{{ feat.text }}</span>
            </li>
          </ul>

          <div class="tier-footer">
            <NButton
              v-if="tier.code === 'trial'"
              block
              secondary
              disabled
            >
              {{ currentPlanCode === 'trial' ? '当前正在使用' : '注册默认基础方案' }}
            </NButton>
            <NButton
              v-else
              block
              :type="tier.recommended || highlightedPlan === tier.code ? 'primary' : 'default'"
              :secondary="!tier.recommended && highlightedPlan !== tier.code"
              :loading="creatingPlan === tier.code"
              :disabled="Boolean(creatingPlan) || Boolean(pricing && !pricing.alipayEnabled)"
              @click="handleBuy(tier.code)"
            >
              <template #icon><i class="ri-alipay-line" /></template>
              <template v-if="pricing && !pricing.alipayEnabled">暂未开通在线支付</template>
              <template v-else-if="currentPlanCode === tier.code">续费顺延 1 年</template>
              <template v-else>立即开通 {{ pricingByCode[tier.code]?.planName || tier.name }}</template>
            </NButton>
          </div>
        </div>
      </div>
    </NSpin>

    <!-- 3. 线下交付与订单记录 -->
    <NCard :bordered="false" class="plan-card-shell mb-4">
      <div class="orders-header">
        <div>
          <h4>我的订阅订单</h4>
          <p class="orders-tip">
            支付宝付款成功后系统自动生效；开通「云端标准版」或「人机双签版」后，可凭订单号联系右下角客服加入专属服务群并预约注册财务会计师 1 对 1 旧账导入。
          </p>
        </div>
        <NButton size="small" secondary :loading="ordersLoading" @click="loadOrders">
          <template #icon><i class="ri-refresh-line" /></template>
          刷新订单
        </NButton>
      </div>
      <NDataTable
        :columns="orderColumns"
        :data="orders"
        :loading="ordersLoading"
        :bordered="false"
        size="small"
        :scroll-x="780"
      >
        <template #empty>
          <div class="orders-empty">暂无在线订阅订单记录</div>
        </template>
      </NDataTable>
    </NCard>
  </div>
</template>

<style scoped>
.plan-page {
  max-width: 1080px;
  margin: 0 auto;
  padding: 8px 16px 56px;
}

.plan-header {
  margin-bottom: 20px;
}

.back-btn {
  margin-bottom: 6px;
  color: var(--solo-text-muted);
}

.plan-header h2 {
  margin: 0;
  font-size: 22px;
  font-weight: 700;
  color: var(--solo-text-primary);
}

.plan-subtitle {
  margin: 4px 0 0;
  font-size: 13.5px;
  color: var(--solo-text-muted);
}

.mb-4 {
  margin-bottom: 16px;
}

.mb-6 {
  margin-bottom: 24px;
}

.plan-card-shell {
  border-radius: var(--solo-radius-lg);
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-card);
}

.current-plan-row {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}

.current-plan-title {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
}

.current-plan-name {
  font-size: 18px;
  font-weight: 700;
  color: var(--solo-text-primary);
}

.current-plan-desc {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--solo-text-secondary);
  line-height: 1.6;
}

.current-usage-stat {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  flex-shrink: 0;
}

.stat-label {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.stat-value {
  font-size: 24px;
  font-weight: 700;
  color: var(--solo-primary);
  font-variant-numeric: tabular-nums;
}

.quota-bar-wrap {
  margin-top: 16px;
  padding-top: 14px;
  border-top: 1px solid var(--solo-border);
}

.quota-meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 12.5px;
  color: var(--solo-text-secondary);
  margin-bottom: 8px;
}

.quota-tip {
  margin-top: 6px;
  font-size: 12px;
  color: var(--solo-text-muted);
}

.section-head {
  margin: 8px 0 14px;
}

.section-head h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 650;
  color: var(--solo-text-primary);
}

.section-head p {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--solo-text-muted);
}

.tiers-grid {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 16px;
  align-items: stretch;
}

.tier-card {
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 20px;
  border-radius: var(--solo-radius-lg);
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-card);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.tier-card.recommended,
.tier-card.highlighted {
  border-color: var(--solo-primary);
  box-shadow: 0 6px 24px rgba(23, 71, 176, 0.08);
}

.tier-badges {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  margin-bottom: 10px;
}

.tier-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11.5px;
  font-weight: 600;
  color: var(--solo-text-secondary);
  background: var(--solo-bg-soft);
}

.tier-badge.brand {
  color: var(--solo-primary);
  background: var(--solo-bg-blue-soft, #edf3ff);
}

.tier-name {
  margin: 0;
  font-size: 18px;
  font-weight: 700;
  color: var(--solo-text-primary);
}

.tier-tagline {
  margin: 6px 0 14px;
  font-size: 12.5px;
  color: var(--solo-text-muted);
  line-height: 1.5;
  min-height: 38px;
}

.tier-price-row {
  display: flex;
  align-items: baseline;
  gap: 3px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--solo-border);
}

.tier-currency {
  font-size: 16px;
  font-weight: 650;
  color: var(--solo-text-primary);
}

.tier-price {
  font-size: 30px;
  font-weight: 750;
  color: var(--solo-text-primary);
  font-variant-numeric: tabular-nums;
  line-height: 1.1;
}

.tier-unit {
  font-size: 12.5px;
  color: var(--solo-text-muted);
  margin-left: 4px;
}

.tier-features {
  list-style: none;
  padding: 14px 0 18px;
  margin: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
  flex: 1;
}

.tier-features li {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  font-size: 13px;
  color: var(--solo-text-primary);
  line-height: 1.45;
}

.tier-features li i {
  margin-top: 2px;
  font-size: 15px;
  color: var(--solo-primary);
  flex-shrink: 0;
}

.tier-features li.muted {
  color: var(--solo-text-muted);
}

.tier-features li.muted i {
  color: var(--solo-text-muted);
}

.orders-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 12px;
}

.orders-header h4 {
  margin: 0;
  font-size: 15px;
  font-weight: 650;
  color: var(--solo-text-primary);
}

.orders-tip {
  margin: 4px 0 0;
  font-size: 12.5px;
  color: var(--solo-text-muted);
  line-height: 1.5;
}

.orders-empty {
  padding: 24px 0;
  color: var(--solo-text-muted);
  font-size: 13px;
}

:deep(.order-mono) {
  font-family: monospace;
  font-size: 12.5px;
}

@media (max-width: 900px) {
  .tiers-grid {
    grid-template-columns: 1fr;
  }
  .current-plan-row {
    flex-direction: column;
  }
  .current-usage-stat {
    align-items: flex-start;
  }
}
</style>
