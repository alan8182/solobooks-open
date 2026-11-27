<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { h, ref } from 'vue'
import {
  NButton,
  NCard,
  NInput,
  NPopconfirm,
  NSelect,
  NTag,
  useMessage,
  type DataTableColumns,
} from 'naive-ui'
import {
  fetchAdminOrder,
  fetchAdminOrders,
  queryAdminOrder,
  refundAdminOrder,
  type AdminGatewayTrade,
  type AdminOrderDetail,
  type AdminOrderItem,
  type AdminOrderStatus,
} from '@/api/admin'
import { useAdminTable, formatAdminDateTime } from '@/composables/useAdminTable'
import { SoloTable, SoloDrawer, SoloStatusBadge, SoloButton } from '@/components/ui'

const message = useMessage()

// 状态筛选不进 useAdminTable 的 query（那是搜索词），单独持有并联动 load
const statusFilter = ref<AdminOrderStatus | ''>('')
const drawerVisible = ref(false)
const detailLoading = ref(false)
const activeOrder = ref<AdminOrderDetail | null>(null)
const refundReason = ref('')
const refunding = ref(false)
const querying = ref(false)
const gatewayResult = ref<AdminGatewayTrade | null | undefined>(undefined)

const {
  loading,
  items,
  total,
  page,
  pageSize,
  query,
  load,
  onSearch,
  onPageChange,
  onPageSizeChange,
} = useAdminTable<AdminOrderItem>({
  defaultPageSize: 20,
  loadData: async ({ page, pageSize, query }) => {
    return await fetchAdminOrders({ query, status: statusFilter.value, page, pageSize })
  },
})

const STATUS_OPTIONS: { label: string; value: AdminOrderStatus | '' }[] = [
  { label: '全部状态', value: '' },
  { label: '待支付', value: 'created' },
  { label: '已支付', value: 'paid' },
  { label: '已激活', value: 'activated' },
  { label: '已关闭', value: 'closed' },
  { label: '异常', value: 'failed' },
]

const STATUS_LABELS: Record<AdminOrderStatus, string> = {
  created: '待支付',
  paid: '已支付',
  activated: '已激活',
  closed: '已关闭',
  failed: '异常',
}

function statusTone(status: AdminOrderStatus): 'brand' | 'danger' | 'success' | 'neutral' | 'info' {
  if (status === 'activated') return 'brand'
  if (status === 'paid') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'created') return 'info'
  return 'neutral'
}

const REFUND_LABELS = {
  submitted: '退款处理中',
  success: '已全额退款',
  failed: '退款失败',
} as const

function refundTone(status: 'submitted' | 'success' | 'failed') {
  if (status === 'success') return 'danger'
  if (status === 'submitted') return 'info'
  return 'danger'
}

function onStatusFilter(value: AdminOrderStatus | '') {
  statusFilter.value = value
  page.value = 1
  void load()
}

async function openOrderDrawer(order: AdminOrderItem) {
  drawerVisible.value = true
  detailLoading.value = true
  activeOrder.value = null
  refundReason.value = ''
  gatewayResult.value = undefined
  try {
    activeOrder.value = await fetchAdminOrder(order.orderNo)
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '加载订单详情失败')
    drawerVisible.value = false
  } finally {
    detailLoading.value = false
  }
}

function canRefund(order: AdminOrderDetail): boolean {
  const refundable = order.status === 'paid' || order.status === 'activated'
  const alreadySuccess = (order.refunds || []).some((r) => r.status === 'success')
  return refundable && !alreadySuccess
}

async function doRefund() {
  if (!activeOrder.value) return
  const reason = refundReason.value.trim()
  if (!reason) {
    message.warning('退款必须填写原因（会进入退款记录与管理审计）')
    return
  }
  refunding.value = true
  try {
    const detail = await refundAdminOrder(activeOrder.value.orderNo, reason)
    activeOrder.value = detail
    refundReason.value = ''
    message.success(
      detail.orderType === 'token_topup'
        ? '退款成功，对应 AI 点数已扣回'
        : detail.orderType === 'first_month_promo'
          ? '退款成功，新人体验包额度已收回'
          : '退款成功，用户套餐权益已收回',
    )
    await load()
  } catch (err: any) {
    const detail = err?.response?.data?.detail
    const text =
      typeof detail === 'object' && detail !== null
        ? `${detail.message || '退款失败'}${detail.context?.subCode ? `（${detail.context.subCode}）` : ''}`
        : detail || '退款失败'
    message.error(text)
  } finally {
    refunding.value = false
  }
}

async function doGatewayQuery() {
  if (!activeOrder.value) return
  querying.value = true
  try {
    const res = await queryAdminOrder(activeOrder.value.orderNo)
    gatewayResult.value = res.gatewayTrade
    if (res.gatewayTrade === null) message.info('支付宝网关无此交易记录')
  } catch (err: any) {
    message.error(err?.response?.data?.detail || '网关查询失败')
  } finally {
    querying.value = false
  }
}

const columns: DataTableColumns<AdminOrderItem> = [
  {
    title: '订单',
    key: 'orderNo',
    minWidth: 230,
    render: (row) =>
      h('div', { class: 'order-cell', onClick: () => openOrderDrawer(row) }, [
        h('span', { class: 'order-no' }, row.orderNo),
        h('span', { class: 'order-sub' }, `${row.userId} · ${row.subject}`),
      ]),
  },
  {
    title: '金额',
    key: 'amount',
    width: 110,
    render: (row) => h('span', { class: 'amount-cell' }, `¥${row.amount}`),
  },
  {
    title: '状态',
    key: 'status',
    width: 96,
    render: (row) =>
      h(
        SoloStatusBadge,
        { tone: statusTone(row.status), plain: row.status === 'created' || row.status === 'closed', size: 'small' },
        { default: () => STATUS_LABELS[row.status] },
      ),
  },
  {
    title: '渠道',
    key: 'channel',
    width: 90,
    render: (row) =>
      h(
        'span',
        { class: row.channel === 'wechat' ? 'channel-cell wechat' : 'channel-cell' },
        row.channel === 'wechat' ? '微信支付' : '支付宝',
      ),
  },
  {
    title: '交易流水',
    key: 'tradeNo',
    width: 170,
    render: (row) =>
      row.tradeNo
        ? h('span', { class: 'trade-no' }, row.tradeNo)
        : h('span', { class: 'cell-muted' }, '—'),
  },
  {
    title: '退款',
    key: 'refund',
    width: 120,
    render: (row) =>
      row.refund
        ? h(
            SoloStatusBadge,
            { tone: refundTone(row.refund.status), plain: row.refund.status !== 'success', size: 'small' },
            { default: () => REFUND_LABELS[row.refund!.status] },
          )
        : h('span', { class: 'cell-muted' }, '—'),
  },
  { title: '支付时间', key: 'paidAt', width: 150, render: (row) => formatAdminDateTime(row.paidAt) },
  { title: '创建时间', key: 'createdAt', width: 150, render: (row) => formatAdminDateTime(row.createdAt) },
  {
    title: '操作',
    key: 'actions',
    width: 90,
    render: (row) =>
      h(
        NButton,
        { size: 'tiny', quaternary: true, onClick: () => openOrderDrawer(row) },
        { default: () => '详情' },
      ),
  },
]
</script>

<template>
  <div class="admin-orders">
    <NCard :bordered="false" class="filter-card">
      <div class="toolbar">
        <NInput
          v-model:value="query"
          placeholder="搜索订单号 / 用户 ID / 交易流水号"
          size="small"
          clearable
          style="width: 300px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        >
          <template #prefix><i class="ri-search-line" /></template>
        </NInput>
        <NSelect
          :value="statusFilter"
          :options="STATUS_OPTIONS"
          size="small"
          style="width: 140px"
          @update:value="onStatusFilter"
        />
        <SoloButton size="small" variant="primary" @click="onSearch">
          <template #icon><i class="ri-search-line" /></template>
          搜索
        </SoloButton>
        <span class="toolbar-note">
          <i class="ri-error-warning-line" />
          退款为不可逆资金操作：套餐订单整单全额退并收回权益；AI 点数充值订单仅退未消耗点数部分，成功后扣回对应点数
        </span>
      </div>
    </NCard>

    <NCard :bordered="false" class="table-card">
      <SoloTable
        :columns="columns"
        :data="items"
        :loading="loading"
        :bordered="false"
        size="small"
        clickable
        :pagination="{
          page,
          pageSize,
          itemCount: total,
          showSizePicker: true,
          pageSizes: [10, 20, 50],
          onChange: onPageChange,
          onUpdatePageSize: onPageSizeChange,
        }"
        empty-title="未找到匹配的订单"
        empty-description="请尝试调整搜索条件或状态筛选"
        @row-click="openOrderDrawer"
      />
    </NCard>

    <!-- 订单详情抽屉 -->
    <SoloDrawer
      v-model:show="drawerVisible"
      :title="activeOrder ? `订单详情 · ${activeOrder.orderNo}` : '订单详情'"
      :width="600"
    >
      <div v-if="detailLoading" class="drawer-loading">加载中…</div>
      <div v-else-if="activeOrder" class="order-drawer-content">
        <div class="order-hero-card">
          <div class="order-hero-meta">
            <h3>{{ activeOrder.subject }}</h3>
            <span class="order-id-sub">{{ activeOrder.orderNo }}</span>
          </div>
          <div class="order-hero-amount">
            <span class="amount-text">¥{{ activeOrder.amount }}</span>
            <SoloStatusBadge
              :tone="statusTone(activeOrder.status)"
              :plain="activeOrder.status === 'created' || activeOrder.status === 'closed'"
            >
              {{ STATUS_LABELS[activeOrder.status] }}
            </SoloStatusBadge>
          </div>
        </div>

        <div class="detail-section">
          <h4 class="section-title">交易信息</h4>
          <div class="info-grid">
            <div class="info-item">
              <span class="info-label">用户 ID</span>
              <span class="info-val mono">{{ activeOrder.userId }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">订单类型</span>
              <span class="info-val">
                {{
                  activeOrder.orderType === 'token_topup'
                    ? 'AI 点数充值'
                    : activeOrder.orderType === 'first_month_promo'
                      ? '新人首月特惠'
                      : '套餐订阅'
                }}
              </span>
            </div>
            <div class="info-item">
              <span class="info-label">套餐</span>
              <span class="info-val">{{ activeOrder.planCode }}</span>
            </div>
            <div
              v-if="activeOrder.orderType === 'token_topup' || activeOrder.orderType === 'first_month_promo'"
              class="info-item"
            >
              <span class="info-label">
                {{ activeOrder.orderType === 'first_month_promo' ? '体验包点数' : '充值点数' }}
              </span>
              <span class="info-val">
                {{ (activeOrder.tokenAmount ?? 0).toLocaleString('zh-CN') }} 点
              </span>
            </div>
            <div class="info-item">
              <span class="info-label">支付渠道</span>
              <span class="info-val">{{ activeOrder.channel === 'wechat' ? '微信支付' : '支付宝' }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">交易流水号</span>
              <span class="info-val mono">{{ activeOrder.tradeNo || '—' }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">买家账号</span>
              <span class="info-val">{{ activeOrder.buyerLogonId || '—' }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">支付时间</span>
              <span class="info-val">{{ formatAdminDateTime(activeOrder.paidAt) }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">激活时间</span>
              <span class="info-val">{{ formatAdminDateTime(activeOrder.activatedAt) }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">支付有效期</span>
              <span class="info-val">{{ formatAdminDateTime(activeOrder.expiresAt) }}</span>
            </div>
            <div class="info-item">
              <span class="info-label">创建时间</span>
              <span class="info-val">{{ formatAdminDateTime(activeOrder.createdAt) }}</span>
            </div>
          </div>
        </div>

        <div class="detail-section">
          <div class="section-head">
            <h4 class="section-title">退款记录</h4>
            <NButton
              size="tiny"
              quaternary
              :loading="querying"
              @click="doGatewayQuery"
            >
              查询支付网关状态（只读）
            </NButton>
          </div>
          <div v-if="gatewayResult !== undefined" class="gateway-result">
            <template v-if="gatewayResult === null">支付网关无此交易记录</template>
            <template v-else>
              <div v-for="(value, key) in gatewayResult" :key="key" class="gateway-row">
                <span class="info-label">{{ key }}</span>
                <span class="gateway-val">{{ value }}</span>
              </div>
            </template>
          </div>
          <div v-if="!(activeOrder.refunds || []).length" class="empty-tip">该订单暂无退款记录</div>
          <div v-for="refund in activeOrder.refunds" :key="refund.outRequestNo" class="refund-item">
            <div class="refund-head">
              <SoloStatusBadge :tone="refundTone(refund.status)" :plain="refund.status !== 'success'" size="small">
                {{ REFUND_LABELS[refund.status] }}
              </SoloStatusBadge>
              <span class="refund-amount">¥{{ refund.refundAmount }}</span>
              <span class="refund-time">{{ formatAdminDateTime(refund.refundedAt || refund.createdAt) }}</span>
            </div>
            <div class="refund-meta">
              <span>原因：{{ refund.reason }}</span>
              <span>操作人：{{ refund.operatorUserId }}</span>
              <span v-if="refund.gatewaySubCode">网关：{{ refund.gatewaySubCode }}</span>
            </div>
          </div>
        </div>

        <div v-if="canRefund(activeOrder)" class="detail-section danger-section">
          <h4 class="section-title">
            {{ activeOrder.orderType === 'token_topup' ? '发起点数退款' : '发起全额退款' }}
          </h4>
          <p v-if="activeOrder.orderType === 'token_topup'" class="section-desc">
            按用户当前<span class="mono">未消耗点数</span>折算退款金额（1 元 = 10,000 点，精确到 0.01 元），原路退回（不可逆）；
            退款成功后自动扣回对应点数，已消耗部分不予退还。余额为 0 时系统会拒绝退款。
          </p>
          <p v-else-if="activeOrder.orderType === 'first_month_promo'" class="section-desc">
            整单退回 ¥{{ activeOrder.amount }} 至买家原支付账户（不可逆）；成功后收回当前剩余体验包额度，且不恢复首单特惠购买资格。
          </p>
          <p v-else class="section-desc">
            整单退回 ¥{{ activeOrder.amount }} 至买家原支付账户（不可逆）；成功后该用户套餐立即降回免费体验版。
            若该用户还有其他有效订单，系统会拒绝退款并提示先手工调整套餐。
          </p>
          <NInput
            v-model:value="refundReason"
            type="textarea"
            size="small"
            :rows="2"
            placeholder="退款原因（必填，进入退款记录与管理审计）"
          />
          <div class="action-row">
            <NPopconfirm :on-positive-click="doRefund">
              <template #trigger>
                <SoloButton size="small" variant="danger" :loading="refunding" :disabled="!refundReason.trim()">
                  <template #icon><i class="ri-arrow-go-back-line" /></template>
                  {{
                    activeOrder.orderType === 'token_topup'
                      ? '确认退款（仅退未消耗点数）'
                      : `确认全额退款 ¥${activeOrder.amount}`
                  }}
                </SoloButton>
              </template>
              <template v-if="activeOrder.orderType === 'token_topup'">
                确认对订单 {{ activeOrder.orderNo }} 退款未消耗点数部分？实际退款金额由系统按当前余额折算，该操作不可撤销，且对应点数会被扣回。
              </template>
              <template v-else>
                确认对订单 {{ activeOrder.orderNo }} 全额退款 ¥{{ activeOrder.amount }}？该操作不可撤销，且用户套餐权益立即收回。
              </template>
            </NPopconfirm>
          </div>
        </div>

        <div class="detail-section">
          <h4 class="section-title">支付通知日志（最近 20 条，只增流水）</h4>
          <div v-if="!(activeOrder.notifyLogs || []).length" class="empty-tip">无通知记录</div>
          <div v-for="(log, index) in activeOrder.notifyLogs" :key="index" class="notify-item">
            <NTag size="tiny" :bordered="false" round :type="log.signVerified ? 'success' : 'error'">
              {{ log.signVerified ? '验签通过' : '验签失败' }}
            </NTag>
            <span class="notify-status">{{ log.tradeStatus || '—' }}</span>
            <span class="notify-result">{{ log.result }}</span>
            <span class="notify-time">{{ formatAdminDateTime(log.createdAt) }}</span>
          </div>
        </div>
      </div>
    </SoloDrawer>
  </div>
</template>

<style scoped>
.admin-orders {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.toolbar-note {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-left: auto;
  font-size: 12.5px;
  color: var(--solo-text-muted);
}

:deep(.order-cell) {
  display: flex;
  flex-direction: column;
  line-height: 1.4;
  cursor: pointer;
}

:deep(.order-no) {
  font-weight: 600;
  font-family: monospace;
  color: var(--solo-text-primary);
}

:deep(.order-sub) {
  font-size: 12px;
  color: var(--solo-text-muted);
}

:deep(.amount-cell) {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

:deep(.channel-cell) {
  color: var(--solo-text-secondary);
}

:deep(.channel-cell.wechat) {
  color: #07c160;
}

:deep(.trade-no) {
  font-family: monospace;
  font-size: 12px;
}

:deep(.cell-muted) {
  color: var(--solo-text-muted);
}

/* 抽屉样式 */
.order-drawer-content {
  display: flex;
  flex-direction: column;
  gap: 24px;
  padding: 8px 0;
}

.drawer-loading {
  padding: 24px 0;
  text-align: center;
  color: var(--solo-text-muted);
}

.order-hero-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  padding: 16px;
  background: var(--solo-bg-soft);
  border-radius: var(--solo-radius, 8px);
}

.order-hero-meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.order-hero-meta h3 {
  margin: 0;
  font-size: 15px;
  font-weight: 650;
  color: var(--solo-text-primary);
}

.order-id-sub {
  font-size: 12px;
  color: var(--solo-text-muted);
  font-family: monospace;
}

.order-hero-amount {
  display: flex;
  align-items: center;
  gap: 10px;
}

.amount-text {
  font-size: 20px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-primary);
}

.detail-section {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.section-title {
  margin: 0;
  font-size: 13.5px;
  font-weight: 600;
  color: var(--solo-text-primary);
}

.section-desc {
  margin: 0;
  font-size: 12.5px;
  color: var(--solo-text-muted);
}

.info-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius, 8px);
  padding: 14px 16px;
}

.info-item {
  display: flex;
  flex-direction: column;
  gap: 3px;
  min-width: 0;
}

.info-label {
  font-size: 12px;
  color: var(--solo-text-muted);
}

.info-val {
  font-size: 13px;
  font-weight: 500;
  color: var(--solo-text-primary);
  word-break: break-all;
}

.info-val.mono {
  font-family: monospace;
}

.empty-tip {
  font-size: 12.5px;
  color: var(--solo-text-muted);
  padding: 6px 0;
}

.refund-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  padding: 12px 14px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius, 8px);
}

.refund-head {
  display: flex;
  align-items: center;
  gap: 10px;
}

.refund-amount {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
}

.refund-time {
  margin-left: auto;
  font-size: 12px;
  color: var(--solo-text-muted);
}

.refund-meta {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  font-size: 12.5px;
  color: var(--solo-text-secondary);
}

.action-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.gateway-result {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 14px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius, 8px);
  font-size: 12.5px;
}

.gateway-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
}

.gateway-val {
  color: var(--solo-text-primary);
  word-break: break-all;
  text-align: right;
}

.notify-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  border-bottom: 1px dashed var(--solo-border);
  font-size: 12.5px;
}

.notify-status {
  min-width: 110px;
  color: var(--solo-text-primary);
}

.notify-result {
  flex: 1;
  color: var(--solo-text-secondary);
  word-break: break-all;
}

.notify-time {
  color: var(--solo-text-muted);
  font-size: 12px;
}
</style>
