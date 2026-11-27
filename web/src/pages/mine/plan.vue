<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, h, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NCard,
  NCheckbox,
  NDataTable,
  NDrawer,
  NDrawerContent,
  NInputNumber,
  NProgress,
  NSpin,
  NTag,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import { useUsageStore } from '@/stores/usage'
import {
  createBillingOrder,
  createFirstMonthPromoOrder,
  createTokenTopupOrder,
  fetchBillingOrder,
  fetchBillingOrders,
  fetchPricing,
  type BillingOrder,
  type BillingOrderStatus,
  type PaymentChannel,
  type PricingCatalog,
  type PricingItem,
} from '@/api/billing'
import { SoloStatusBadge } from '@/components/ui'
import WechatPayQrModal from './WechatPayQrModal.vue'

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
const agreedToTerms = ref(false)

// ── 支付入口（渠道可用性以后端 pricing 为唯一真相）──
// 套餐、新人体验包与 AI 点数充值统一走「1 步直出」：默认优先拉起微信扫码弹窗
// （弹窗内可直接切支付宝并附底部条款）；若仅开通支付宝则直接下单跳转收银台。
type PurchaseTarget =
  | { kind: 'plan'; planCode: string }
  | { kind: 'topup'; amountYuan: number }
  | { kind: 'promo' }
const activePurchaseTarget = ref<PurchaseTarget | null>(null)
const switchingToAlipay = ref(false)
const wechatModalVisible = ref(false)
const wechatOrder = ref<{
  orderNo: string
  codeUrl: string
  amount: string
  planName: string
  expiresAt: string
} | null>(null)

const alipayAvailable = computed(() => pricing.value?.alipayEnabled ?? false)
const wechatAvailable = computed(() => pricing.value?.wechatEnabled ?? false)
const anyChannelAvailable = computed(() => alipayAvailable.value || wechatAvailable.value)

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
// 套餐本期剩余（不含充值点数）；usedTokens 含超出套餐后从点数扣减的部分
const planRemainingTokens = computed(() => {
  const limit = effectiveLimitTokens.value
  if (limit === null) return null
  return Math.max(0, limit - (quotaInfo.value?.usedTokens ?? 0))
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

// ── AI 点数充值（与套餐独立，余额永久有效；消耗先套餐后点数）──
const tokenTopup = computed(() => pricing.value?.tokenTopup ?? null)
const tokenBalance = computed(() => quotaInfo.value?.tokenBalance ?? 0)
const promoTokenBalance = computed(() => quotaInfo.value?.promoTokenBalance ?? 0)
const firstMonthPromo = computed(() => pricing.value?.firstMonthPromo ?? null)
const showPromoCard = computed(() =>
  Boolean(firstMonthPromo.value?.enabled && firstMonthPromo.value?.eligible),
)
const highlightedPromo = computed(() => route.query.promo === 'first_month')
const creatingPromo = ref(false)
const topupAvailable = computed(
  () => Boolean(tokenTopup.value?.enabled) && anyChannelAvailable.value,
)
const TOPUP_QUICK_YUAN = [10, 30, 100, 300]
const topupYuan = ref<number | null>(10)
const creatingTopup = ref(false)
const topupTokens = computed(() => {
  const yuan = topupYuan.value
  const perYuan = tokenTopup.value?.tokensPerYuan ?? 0
  if (!yuan || !perYuan) return 0
  return Math.round(yuan * perYuan)
})
const topupAmountValid = computed(() => {
  const cfg = tokenTopup.value
  const yuan = topupYuan.value
  if (!cfg || !yuan) return false
  return Number.isInteger(yuan) && yuan >= cfg.minYuan && yuan <= cfg.maxYuan
})

const promoTermsTitle = computed(() => '新人体验包服务条款')
const activeModalTerms = computed<{ title: string; lines: string[] }>(() => {
  const target = activePurchaseTarget.value
  if (target?.kind === 'topup') {
    return {
      title: '充值服务条款',
      lines: [
        'AI 点数为虚拟商品，支付成功后立即到账且永久有效，不随套餐到期清零。',
        '消耗顺序：先扣本期套餐额度，套餐用尽后自动扣点数余额。',
        '退款规则：仅未消耗的点数部分可联系客服原路退回，已消耗部分不予退还；充值不改变当前套餐与账套上限。',
      ],
    }
  }
  if (target?.kind === 'promo') {
    return {
      title: promoTermsTitle.value,
      lines: [
        `新人一次性特惠（限购 1 次），支付后立即到账，有效期 ${firstMonthPromo.value?.validDays ?? 30} 天，到期未使用自动失效。`,
        '消耗顺序：先扣本期套餐额度，再扣体验包额度，最后扣永久充值余额。',
        '可联系客服全额退还实付金额，退款后收回剩余体验包额度且不恢复首单特惠资格。',
      ],
    }
  }
  return {
    title: '套餐服务条款',
    lines: [
      '开通即按年订阅并激活对应权益（每年 1000 万 Token 额度及 1 对 1 旧账导入），到期可续费顺延。',
      '7 日内未启动人工服务可全额退费；已交付人工服务（旧账导入 100 元/次、季度审账 180 元/次）按次扣减后退还余款。',
      '本平台为 AI 辅助自管账软件，不构成法定审计报告。',
    ],
  }
})

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
    badge: '默认入门 · 首月 20 万 Token',
    name: '免费体验版',
    tagline: '免部署开箱即用，功能与标准版一致',
    fallbackPrice: '0',
    unit: '基础免费',
    features: [
      { text: '全功能开放：AI 记账、三张报表与申报小抄（最多 3 个公司账套）', included: true },
      { text: '新用户首月 20 万 Token（新人可 ¥1 扩容至 100 万），次月起每月 10 万 Token', included: true },
      { text: '银行流水、微信 / 支付宝账单与发票自动解析导入', included: true },
      { text: '1 对 1 历史旧账导入（需自助导入）', included: false },
      { text: '专属服务群与会计师季度审账', included: false },
    ],
  },
  {
    code: 'standard',
    badge: '主力推荐 · 每日仅 ¥1.6',
    name: '云端标准版',
    tagline: '专业财务顾问/会计师 1 对 1 帮你把旧账导进来',
    fallbackPrice: '598',
    unit: '元 / 年',
    recommended: true,
    features: [
      { text: '每年 1000 万 AI Token 额度（含文本问答与多模态票据识别）', included: true },
      { text: '最多支持 3 个公司账套（更多账套将通过未来机构联营版开放）', included: true },
      { text: '1 对 1 历史旧账导入与期初建账（专业财务顾问/会计师协助）', included: true },
      { text: '专属问题服务群（系统实操指导 + 日常财税问题响应）', included: true },
      { text: '老板私卡垫资合规冲销归还、研发辅助账与申报小抄', included: true },
    ],
  },
  {
    code: 'pro',
    badge: '人机双签 · 会计师季度护航',
    name: '人机双签版',
    tagline: 'AI 处理日常流水，专业会计师每季度审账复核',
    fallbackPrice: '2,980',
    unit: '元 / 年',
    features: [
      { text: '含标准版全部软件功能、每年 1000 万 Token（3 个账套）与 1 对 1 旧账导入', included: true },
      { text: '专业会计师每季度审账复核（内部管理把关，非事务所法定审计报告）', included: true },
      { text: '每季度出具专属《季度财务分析报告》', included: true },
      { text: '每季度 30 分钟视频在线问答（1 对 1 筹划与解读）', included: true },
      { text: '日常经营财务问答（合同合规、公转私规范随时问）', included: true },
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

const promoCreatingDisabled = computed(
  () => creatingPromo.value || Boolean(creatingPlan.value) || creatingTopup.value,
)

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

async function createOrderForTarget(target: PurchaseTarget, channel: PaymentChannel) {
  if (target.kind === 'plan') return createBillingOrder(target.planCode, channel)
  if (target.kind === 'promo') return createFirstMonthPromoOrder(channel)
  return createTokenTopupOrder(target.amountYuan, channel)
}

function setTargetBusy(target: PurchaseTarget, busy: boolean) {
  if (target.kind === 'plan') creatingPlan.value = busy ? target.planCode : ''
  else if (target.kind === 'promo') creatingPromo.value = busy
  else creatingTopup.value = busy
}

function isTargetBusy(target: PurchaseTarget): boolean {
  if (target.kind === 'plan') return Boolean(creatingPlan.value)
  if (target.kind === 'promo') return promoCreatingDisabled.value
  return creatingTopup.value || Boolean(creatingPlan.value)
}

async function startPurchase(target: PurchaseTarget) {
  if (isTargetBusy(target)) return
  if (pricing.value && !anyChannelAvailable.value) {
    message.info('当前环境暂未开通在线收款，请联系客服办理')
    return
  }
  const preferredChannel: PaymentChannel = wechatAvailable.value ? 'wechat' : 'alipay'
  agreedToTerms.value = true
  setTargetBusy(target, true)
  try {
    const order = await createOrderForTarget(target, preferredChannel)
    if (preferredChannel === 'alipay') {
      message.success('订单已创建，正在跳转支付宝收银台…')
      window.location.href = order.payUrl ?? ''
      return
    }
    activePurchaseTarget.value = target
    wechatOrder.value = {
      orderNo: order.orderNo,
      codeUrl: order.codeUrl ?? '',
      amount: order.amount,
      planName: order.planName,
      expiresAt: order.expiresAt,
    }
    wechatModalVisible.value = true
  } catch (err: unknown) {
    message.error(extractErrorMessage(err, '创建订单失败，请重试'))
  } finally {
    setTargetBusy(target, false)
  }
}

async function handleSwitchToAlipay() {
  const target = activePurchaseTarget.value
  if (!target || !alipayAvailable.value || switchingToAlipay.value) return
  switchingToAlipay.value = true
  try {
    const order = await createOrderForTarget(target, 'alipay')
    wechatModalVisible.value = false
    message.success('订单已创建，正在跳转支付宝收银台…')
    window.location.href = order.payUrl ?? ''
  } catch (err: unknown) {
    wechatModalVisible.value = false
    message.error(extractErrorMessage(err, '切换支付宝失败，请重试'))
  } finally {
    switchingToAlipay.value = false
  }
}

async function handleBuy(planCode: string) {
  if (planCode === 'trial') return
  await startPurchase({ kind: 'plan', planCode })
}

async function handleTopup() {
  if (creatingTopup.value || creatingPlan.value) return
  if (!topupAvailable.value) {
    message.info('当前环境暂未开通在线充值')
    return
  }
  if (!topupAmountValid.value) {
    const cfg = tokenTopup.value
    message.warning(
      cfg ? `请输入 ${cfg.minYuan}–${cfg.maxYuan} 之间的整数元金额` : '充值暂不可用',
    )
    return
  }
  await startPurchase({ kind: 'topup', amountYuan: topupYuan.value as number })
}

async function handleBuyPromo() {
  if (!showPromoCard.value) return
  await startPurchase({ kind: 'promo' })
}

function handleWechatPaySuccess() {
  wechatModalVisible.value = false
  void Promise.all([usageStore.ensureLoaded(true), loadOrders(), loadPricing()])
}

async function pollOrderStatus(orderNo: string) {
  pollingOrderNo.value = orderNo
  try {
    for (let i = 0; i < 40; i++) {
      try {
        const order = await fetchBillingOrder(orderNo)
        if (order.status === 'activated') {
          message.success(
            order.orderType === 'token_topup'
              ? '支付成功，AI 点数已到账'
              : order.orderType === 'first_month_promo'
                ? '支付成功，新人体验包 80 万 Token 已到账'
                : '支付成功，套餐已生效',
          )
          await Promise.all([usageStore.ensureLoaded(true), loadOrders(), loadPricing()])
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
  {
    title: '商品',
    key: 'subject',
    minWidth: 210,
    render: (row) =>
      h('span', { class: 'order-subject' }, [
        h('span', null, row.subject),
        row.orderType === 'token_topup'
          ? h('span', { class: 'order-type-chip' }, 'AI 点数')
          : row.orderType === 'first_month_promo'
            ? h('span', { class: 'order-type-chip' }, '新人特惠')
            : null,
      ]),
  },
  {
    title: '金额',
    key: 'amount',
    width: 110,
    align: 'right',
    render: (row) => `¥${row.amount}`,
  },
  {
    title: '支付渠道',
    key: 'channel',
    width: 95,
    render: (row) => (row.channel === 'wechat' ? '微信支付' : '支付宝'),
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

const showOrdersDrawer = ref(false)

function openOrdersDrawer() {
  showOrdersDrawer.value = true
  void loadOrders()
}

onMounted(() => {
  usageStore.ensureLoaded(true)
  void loadPricing()

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
      </div>
      <NButton size="small" secondary @click="openOrdersDrawer">
        <template #icon><i class="ri-file-list-3-line" /></template>
        我的订单
      </NButton>
    </div>

    <NAlert v-if="pollingOrderNo" type="info" :bordered="false" class="mb-4">
      正在确认订单 <strong>{{ pollingOrderNo }}</strong> 的支付结果，请稍候…
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
                年付额度
              </NTag>
              <NTag v-else size="small" :bordered="false">
                {{ currentPlanCode === 'trial' && (quotaInfo?.limitTokens ?? 0) >= 200_000 ? '首月体验期' : '月度体验额度' }}
              </NTag>
              <NTag v-if="promoTokenBalance > 0" size="small" type="warning" :bordered="false">
                新人特惠剩余 {{ formatTokens(promoTokenBalance) }}（{{ quotaInfo?.promoExpiresAt }} 到期）
              </NTag>
              <NTag v-if="quotaInfo?.maxCompanies != null" size="small" :bordered="false">
                账套 {{ quotaInfo.companyCount ?? 0 }} / {{ quotaInfo.maxCompanies }}
              </NTag>
            </div>
            <p v-if="quotaInfo?.limitTokens === null && currentPlanCode === 'trial'" class="current-plan-desc">
              自有部署环境，使用自备模型服务，AI 用量不设限。
            </p>
            <p v-else-if="quotaInfo?.limitTokens === null" class="current-plan-desc">
              当前方案不设 AI 用量限制。
            </p>
          </div>
          <div class="current-usage-stats">
            <div class="current-usage-stat">
              <span class="stat-label">{{ quotaInfo?.periodType === 'annual' ? '本年度已消耗 AI Token' : '本月已消耗 AI Token' }}</span>
              <strong class="stat-value">
                {{ formatTokens(quotaInfo?.usedTokens ?? currentMonth?.totalTokens ?? 0) }}<span
                  v-if="effectiveLimitTokens !== null"
                  class="stat-total"
                > / {{ formatTokens(effectiveLimitTokens) }}</span>
              </strong>
            </div>
          </div>
        </div>

        <div v-if="effectiveLimitTokens !== null" class="quota-bar-wrap">
          <div class="quota-meta">
            <span>计费周期：{{ quotaInfo?.periodStart }} ~ {{ quotaInfo?.periodEnd }}</span>
            <span v-if="(quotaInfo?.bonusTokens ?? 0) > 0">
              含赠送 {{ formatTokens(quotaInfo?.bonusTokens ?? 0) }}
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
            <template v-if="quotaPercent >= 100">
              <template v-if="promoTokenBalance > 0">
                套餐额度已用完，后续优先从新人体验包扣减（剩余 {{ formatTokens(promoTokenBalance) }} 点，{{ quotaInfo?.promoExpiresAt }} 到期）
              </template>
              <template v-else-if="tokenBalance > 0">
                套餐额度已用完，后续 AI 调用自动从点数余额扣减（当前 {{ formatTokens(tokenBalance) }} 点）
              </template>
              <template v-else-if="currentPlanCode === 'trial'">
                <template v-if="showPromoCard">
                  本期体验额度已用完，可 ¥1 解锁 80 万新人体验包，或升级云端标准版
                </template>
                <template v-else>
                  本期体验额度已用完，请升级云端标准版、充值 AI 点数或等待下个周期自动恢复
                </template>
              </template>
              <template v-else>
                本年度 AI Token 额度已用完，请充值 AI 点数、联系客服或等待下个订阅周期自动恢复
              </template>
            </template>
            <template v-else>
              本期剩余 {{ formatTokens(planRemainingTokens ?? 0) }} tokens
            </template>
          </div>
        </div>
      </NSpin>
    </NCard>

    <!-- 1.5 新人首月 AI 体验包（仅限未买过、未订阅年付的 trial 用户展示） -->
    <NCard
      v-if="showPromoCard"
      :bordered="false"
      class="plan-card-shell mb-4 promo-card"
      :class="{ highlighted: highlightedPromo }"
    >
      <div class="promo-row">
        <div class="promo-main">
          <div class="promo-badge-row">
            <span class="promo-badge">新人首月限时特惠 · 每账号限购 1 次</span>
          </div>
          <h4 class="promo-title">{{ firstMonthPromo?.name || '新人首月 AI 体验包' }}</h4>
          <p class="promo-desc">
            立享 <strong>{{ formatTokens(firstMonthPromo?.tokens ?? 800_000) }}</strong> Token（80 万额外额度），自支付成功日起
            <strong>{{ firstMonthPromo?.validDays ?? 30 }} 天有效</strong>，先用套餐额度、再用体验包。长期记账推荐下方 ¥598/年 云端标准版。
          </p>
        </div>
        <div class="promo-action">
          <div class="promo-price-box">
            <span class="promo-currency">¥</span>
            <span class="promo-price">{{ formatPriceDisplay(firstMonthPromo?.priceYuan, '1') }}</span>
            <del class="promo-original">原价 ¥{{ formatPriceDisplay(firstMonthPromo?.originalPriceYuan, '80') }}</del>
          </div>
          <NButton
            type="primary"
            class="promo-submit"
            :loading="creatingPromo"
            :disabled="creatingPromo || Boolean(creatingPlan) || creatingTopup || Boolean(pricing && !anyChannelAvailable)"
            @click="handleBuyPromo"
          >
            <template #icon><i class="ri-flashlight-line" /></template>
            <template v-if="pricing && !anyChannelAvailable">暂未开通在线支付</template>
            <template v-else>¥{{ formatPriceDisplay(firstMonthPromo?.priceYuan, '1') }} 立即解锁 80 万</template>
          </NButton>
        </div>
      </div>
    </NCard>

    <!-- 2. 订阅方案对比与下单 -->
    <div class="section-head">
      <div>
        <h3>选择适合您的订阅方案</h3>
      </div>
    </div>

    <NAlert
      v-if="pricing && !anyChannelAvailable"
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
              :disabled="Boolean(creatingPlan) || Boolean(pricing && !anyChannelAvailable)"
              @click="handleBuy(tier.code)"
            >
              <template #icon><i class="ri-secure-payment-line" /></template>
              <template v-if="pricing && !anyChannelAvailable">暂未开通在线支付</template>
              <template v-else-if="currentPlanCode === tier.code">续费顺延 1 年</template>
              <template v-else>立即开通 {{ pricingByCode[tier.code]?.planName || tier.name }}</template>
            </NButton>
          </div>
        </div>
      </div>

      <div class="purchase-terms-box mb-6">
        <NCheckbox v-model:checked="agreedToTerms" class="terms-checkbox">
          <span>
            下单前请确认：我已阅读并同意
            <a href="/agreement" target="_blank" rel="noreferrer">《服务协议（含付费订阅与退款规则）》</a>
            及
            <a href="/privacy" target="_blank" rel="noreferrer">《隐私政策》</a>
          </span>
        </NCheckbox>
        <p class="terms-note">
          本平台为 AI 辅助自管账软件，不构成法定审计报告。年付开通 7 日内未启动人工服务可全额退费；已交付人工服务（旧账导入 100 元/次、季度审账复核 180 元/次）按次扣减后退还余款。
        </p>
      </div>
    </NSpin>

    <!-- 3. AI 点数充值（与套餐独立，余额永久有效） -->
    <NCard v-if="tokenTopup?.enabled" :bordered="false" class="plan-card-shell mb-4 topup-card">
      <div class="topup-head">
        <div class="topup-head-main">
          <h4>AI 点数充值</h4>
          <p>
            1 元 = {{ tokenTopup.tokensPerYuan.toLocaleString('zh-CN') }} 点（1 点 = 1 token），永久有效；先扣套餐额度，用尽后自动扣点数。
          </p>
        </div>
        <div class="current-usage-stat">
          <span class="stat-label">当前点数余额</span>
          <strong class="stat-value">{{ formatTokens(tokenBalance) }}</strong>
        </div>
      </div>

      <div class="topup-body">
        <div class="topup-quick">
          <button
            v-for="yuan in TOPUP_QUICK_YUAN"
            :key="yuan"
            type="button"
            class="topup-quick-btn"
            :class="{ active: topupYuan === yuan }"
            :disabled="creatingTopup"
            @click="topupYuan = yuan"
          >
            {{ yuan }} 元
          </button>
        </div>
        <NInputNumber
          v-model:value="topupYuan"
          class="topup-input"
          :min="tokenTopup.minYuan"
          :max="tokenTopup.maxYuan"
          :precision="0"
          :step="1"
          :show-button="false"
          :disabled="creatingTopup"
          placeholder="自定义金额"
        >
          <template #prefix>¥</template>
        </NInputNumber>
        <div class="topup-preview">
          到账 <strong>{{ formatTokens(topupTokens) }}</strong> 点
        </div>
        <NButton
          type="primary"
          class="topup-submit"
          :loading="creatingTopup"
          :disabled="!topupAvailable || !topupAmountValid"
          @click="handleTopup"
        >
          <template #icon><i class="ri-add-circle-line" /></template>
          立即充值
        </NButton>
      </div>

      <NAlert v-if="!anyChannelAvailable" type="warning" :bordered="false" class="mt-2">
        当前暂未开启在线支付通道；如需充值 AI 点数，请联系客服人工办理。
      </NAlert>
    </NCard>

    <!-- 4. 订阅与充值订单右侧抽屉 -->
    <NDrawer v-model:show="showOrdersDrawer" placement="right" :width="840" style="max-width: 94vw;">
      <NDrawerContent closable>
        <template #header>
          <div class="orders-drawer-header">
            <span>我的订单</span>
            <NButton size="small" secondary :loading="ordersLoading" @click="loadOrders">
              <template #icon><i class="ri-refresh-line" /></template>
              刷新订单
            </NButton>
          </div>
        </template>
        <NDataTable
          :columns="orderColumns"
          :data="orders"
          :loading="ordersLoading"
          :bordered="false"
          size="small"
          :scroll-x="780"
        >
          <template #empty>
            <div class="orders-empty">暂无在线订单记录</div>
          </template>
        </NDataTable>
      </NDrawerContent>
    </NDrawer>

    <WechatPayQrModal
      v-if="wechatOrder"
      v-model:show="wechatModalVisible"
      :order-no="wechatOrder.orderNo"
      :code-url="wechatOrder.codeUrl"
      :amount="wechatOrder.amount"
      :plan-name="wechatOrder.planName"
      :expires-at="wechatOrder.expiresAt"
      :can-switch-alipay="alipayAvailable"
      :switching-alipay="switchingToAlipay"
      :terms-title="activeModalTerms.title"
      :terms-lines="activeModalTerms.lines"
      @switch-alipay="handleSwitchToAlipay"
      @success="handleWechatPaySuccess"
    />
  </div>
</template>

<style scoped>
.plan-page {
  max-width: 1080px;
  margin: 0 auto;
  padding: 8px 16px 56px;
}

.plan-header {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 12px;
  margin-bottom: 20px;
}

.orders-drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  padding-right: 12px;
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

.current-usage-stats {
  display: flex;
  align-items: flex-start;
  gap: 28px;
  flex-shrink: 0;
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

.stat-total {
  font-size: 13px;
  font-weight: 500;
  color: var(--solo-text-muted);
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

.orders-empty {
  padding: 24px 0;
  color: var(--solo-text-muted);
  font-size: 13px;
}

.purchase-terms-box {
  padding: 14px 16px;
  border-radius: var(--solo-radius-md, 10px);
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-card);
}

.terms-checkbox {
  font-size: 13px;
  color: var(--solo-text-primary);
}

.terms-checkbox a {
  color: var(--solo-primary);
  text-decoration: none;
}

.terms-checkbox a:hover {
  text-decoration: underline;
}

.terms-note {
  margin: 8px 0 0 24px;
  font-size: 12px;
  line-height: 1.6;
  color: var(--solo-text-muted);
}

:deep(.order-mono) {
  font-family: monospace;
  font-size: 12.5px;
}

:deep(.order-subject) {
  display: inline-flex;
  align-items: center;
  gap: 8px;
}

:deep(.order-type-chip) {
  padding: 0 6px;
  border-radius: 4px;
  background: var(--solo-primary-soft);
  color: var(--solo-primary);
  font-size: 11px;
  line-height: 18px;
}

.mt-2 {
  margin-top: 8px;
}

/* ── 新人首月体验包卡片 ── */
.promo-card {
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-blue-soft);
}

.promo-card.highlighted {
  border-color: var(--solo-primary);
  box-shadow: var(--solo-shadow);
}

.promo-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
}

.promo-main {
  flex: 1;
  min-width: 0;
}

.promo-badge-row {
  margin-bottom: 6px;
}

.promo-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: var(--solo-radius-pill);
  font-size: 11.5px;
  font-weight: 600;
  color: var(--solo-warning);
  background: var(--solo-warning-bg);
}

.promo-title {
  margin: 0 0 6px;
  font-size: 16px;
  font-weight: 700;
  color: var(--solo-text-primary);
}

.promo-desc {
  margin: 0;
  font-size: 13px;
  line-height: 1.6;
  color: var(--solo-text-secondary);
}

.promo-action {
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  gap: 10px;
  flex-shrink: 0;
}

.promo-price-box {
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.promo-currency {
  font-size: 15px;
  font-weight: 700;
  color: var(--solo-primary);
}

.promo-price {
  font-size: 28px;
  font-weight: 750;
  color: var(--solo-primary);
  line-height: 1.1;
  font-variant-numeric: tabular-nums;
}

.promo-original {
  margin-left: 4px;
  font-size: 12px;
  color: var(--solo-text-muted);
}

@media (max-width: 768px) {
  .promo-row {
    flex-direction: column;
    align-items: flex-start;
  }
  .promo-action {
    width: 100%;
    flex-direction: row;
    align-items: center;
    justify-content: space-between;
  }
}

/* ── AI 点数充值卡片 ── */
.topup-card {
  border: 1px solid var(--solo-border);
}

.topup-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
}

.topup-head-main h4 {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 700;
  color: var(--solo-text-primary);
}

.topup-head-main p {
  margin: 0;
  max-width: 620px;
  font-size: 13px;
  line-height: 1.6;
  color: var(--solo-text-muted);
}

.topup-body {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
  margin-top: 16px;
}

.topup-quick {
  display: flex;
  gap: 8px;
}

.topup-quick-btn {
  min-width: 64px;
  padding: 6px 12px;
  border-radius: 8px;
  border: 1px solid var(--solo-border);
  background: var(--solo-bg-card);
  color: var(--solo-text-primary);
  font-size: 13px;
  cursor: pointer;
  transition: all 0.15s ease;
}

.topup-quick-btn:hover:not(:disabled) {
  border-color: var(--solo-primary);
}

.topup-quick-btn.active {
  border-color: var(--solo-primary);
  background: var(--solo-primary-soft);
  color: var(--solo-primary);
  font-weight: 600;
}

.topup-quick-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.topup-input {
  width: 130px;
}

.topup-preview {
  font-size: 13px;
  color: var(--solo-text-muted);
}

.topup-preview strong {
  color: var(--solo-primary);
  font-size: 15px;
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
