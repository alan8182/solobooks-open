<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
/**
 * Solo Audit Share —— CPA 免注册只读协审视图（/share/:token）。
 *
 * 设计目标（商业计划书"单户体检 ≤15 分钟"）：
 * 预消化的体检动线：总览红绿灯 → 异常清单（可批注）→ 勾稽核对 → 试算平衡 → 总体意见。
 * 只读 + 可批注：批注是唯一写操作，是咨询参考而非记账指令。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { NButton, NInput, NSpin, NTag, useMessage } from 'naive-ui'
import { SoloErrorState } from '@/components/ui'
import {
  createShareAnnotation,
  fetchShareAnnotations,
  fetchShareView,
  type AnomalyItem,
  type ShareAnnotation,
  type ShareCheckItem,
  type ShareView,
} from '@/api/auditShare'

const route = useRoute()
const message = useMessage()
const token = computed(() => String(route.params.token || ''))

const loading = ref(true)
const loadError = ref<{ title: string; description: string; detail: string } | null>(null)
const view = ref<ShareView | null>(null)
const annotations = ref<ShareAnnotation[]>([])

// CPA 昵称：首次批注时填写，本地记住（免注册，不落库为用户）
const authorName = ref(localStorage.getItem('audit_share_author') || '')
const nicknameEditing = ref(false)
const overallOpinion = ref('')
const submittingOverall = ref(false)
const commentingAnomalyId = ref<string | null>(null)
const commentDraft = ref('')

const LEVEL_ORDER: Record<string, number> = { high: 0, medium: 1, low: 2 }
const LEVEL_LABEL: Record<string, string> = { high: '高', medium: '中', low: '低' }
const TYPE_LABEL: Record<string, string> = {
  receipt: '补票据', tax: '开票/税务', risk: '风险', task: '待处理', filing: '申报', bank: '流水',
}

const sortedAnomalies = computed<AnomalyItem[]>(() => {
  const list = [...(view.value?.snapshot.anomalies || [])]
  list.sort((a, b) => (LEVEL_ORDER[a.level] ?? 9) - (LEVEL_ORDER[b.level] ?? 9))
  return list
})

const checkRows = computed<ShareCheckItem[]>(() => view.value?.snapshot.checks?.checks || [])
const income = computed(() => view.value?.snapshot.incomeSummary)
const unmatched = computed(() => view.value?.snapshot.unmatched)

async function load() {
  loading.value = true
  loadError.value = null
  try {
    view.value = await fetchShareView(token.value)
    annotations.value = await fetchShareAnnotations(token.value)
  } catch (error: unknown) {
    const status = (error as { response?: { status?: number } })?.response?.status
    const detail = (error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || ''
    if (status === 410) {
      loadError.value = {
        title: '协审链接已失效',
        description: '链接已被撤销或已过期，请联系经营者重新生成。',
        detail,
      }
    } else if (status === 404) {
      loadError.value = {
        title: '协审链接不存在',
        description: '请核对链接是否完整，或联系经营者重新发送。',
        detail,
      }
    } else {
      loadError.value = {
        title: '无法打开协审链接',
        description: '网络或服务暂时不可用，请稍后重试。',
        detail,
      }
    }
  } finally {
    loading.value = false
  }
}

async function submitAnnotation(anchorType: string, anchorId: string | null, body: string) {
  const text = body.trim()
  if (!text) {
    message.warning('请填写批注内容')
    return null
  }
  const author = authorName.value.trim()
  if (!author) {
    nicknameEditing.value = true
    message.warning('请先填写您的署名（如"注册会计师·陈"）')
    return null
  }
  const created = await createShareAnnotation(token.value, {
    authorName: author, anchorType, anchorId, body: text,
  })
  localStorage.setItem('audit_share_author', author)
  annotations.value.push(created)
  message.success('批注已提交，经营者会在应用内看到')
  return created
}

async function submitOverall() {
  if (submittingOverall.value) return
  submittingOverall.value = true
  try {
    const created = await submitAnnotation('overall', null, overallOpinion.value)
    if (created) overallOpinion.value = ''
  } catch (error: unknown) {
    message.error((error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '提交失败，请稍后再试')
  } finally {
    submittingOverall.value = false
  }
}

async function submitAnomalyComment(anomaly: AnomalyItem) {
  try {
    const created = await submitAnnotation('entry', anomaly.entryId || anomaly.id, commentDraft.value)
    if (created) {
      commentDraft.value = ''
      commentingAnomalyId.value = null
    }
  } catch (error: unknown) {
    message.error((error as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '提交失败')
  }
}

function saveNickname() {
  if (authorName.value.trim()) {
    nicknameEditing.value = false
    localStorage.setItem('audit_share_author', authorName.value.trim())
  }
}

function printPage() {
  window.print()
}

function checkTone(status?: string): 'success' | 'error' | 'warning' {
  if (status === 'passed') return 'success'
  if (status === 'blocking') return 'error'
  return 'warning'
}
function checkLabel(status?: string): string {
  if (status === 'passed') return '通过'
  if (status === 'blocking') return '阻塞'
  return '待核验'
}

onMounted(load)
</script>

<template>
  <div class="share-page">
    <header class="share-header">
      <div class="share-title">
        <h1>{{ view?.share.companyName || '账套体检' }}</h1>
        <span class="share-month">{{ view?.share.month }} 月度体检</span>
        <NTag v-if="view?.share.anonymized" size="small" type="info" :bordered="false">已脱敏</NTag>
      </div>
      <div class="share-meta">
        <span>快照生成于 {{ view?.share.generatedAt?.slice(0, 10) || '—' }}</span>
        <NButton size="tiny" quaternary @click="printPage">打印</NButton>
      </div>
    </header>

    <div class="share-disclaimer" role="note">
      {{ view?.share.disclaimer || '本视图仅供软件操作辅导与会计规则参考，不构成法定代理记账或审计意见。' }}
    </div>

    <NSpin v-if="loading" class="share-loading" />
    <SoloErrorState
      v-else-if="loadError"
      :title="loadError.title"
      :description="loadError.description"
      :detail="loadError.detail"
      :show-retry="false"
    />

    <template v-else-if="view">
      <!-- ① 总览：五项检查 + 损益 -->
      <section class="share-section" aria-labelledby="sec-overview">
        <h2 id="sec-overview">一、体检总览</h2>
        <div class="check-grid">
          <div v-for="check in checkRows" :key="check.code" class="check-card" :data-status="check.status">
            <div class="check-name">{{ check.name }}</div>
            <NTag :type="checkTone(check.status)" size="small" :bordered="false">{{ checkLabel(check.status) }}</NTag>
            <div class="check-detail">
              <template v-if="check.pendingCount">待过账 {{ check.pendingCount }}；</template>
              <template v-if="check.pendingReviewCount">待复核 {{ check.pendingReviewCount }}；</template>
              <template v-if="check.unreconciledBankCount">未对账流水 {{ check.unreconciledBankCount }}；</template>
              <template v-if="check.issueCount">核对问题 {{ check.issueCount }}</template>
            </div>
          </div>
        </div>
        <div class="summary-row" v-if="income">
          <div class="summary-item"><span class="label">收入</span><span class="value">{{ income.revenue }}</span></div>
          <div class="summary-item"><span class="label">费用</span><span class="value">{{ income.expense }}</span></div>
          <div class="summary-item"><span class="label">利润</span><span class="value">{{ income.profit }}</span></div>
          <div class="summary-item">
            <span class="label">暂估未冲回</span>
            <span class="value">{{ view.snapshot.accruals?.estimateCount ?? '—' }} 笔</span>
          </div>
        </div>
      </section>

      <!-- ② 异常清单（可批注） -->
      <section class="share-section" aria-labelledby="sec-anomaly">
        <h2 id="sec-anomaly">二、异常清单（{{ sortedAnomalies.length }}）</h2>
        <p v-if="!sortedAnomalies.length" class="muted">本月无待处理的异常事项。</p>
        <div v-for="anomaly in sortedAnomalies" :key="anomaly.id" class="anomaly-row" :data-level="anomaly.level">
          <div class="anomaly-main">
            <NTag :type="anomaly.level === 'high' ? 'error' : anomaly.level === 'medium' ? 'warning' : 'default'"
                  size="small" :bordered="false">{{ LEVEL_LABEL[anomaly.level] || anomaly.level }}</NTag>
            <NTag size="tiny" :bordered="false">{{ TYPE_LABEL[anomaly.type] || anomaly.type }}</NTag>
            <span class="anomaly-title">{{ anomaly.title }}</span>
            <span v-if="anomaly.amount" class="anomaly-amount">¥{{ anomaly.amount }}</span>
          </div>
          <div v-if="anomaly.description" class="anomaly-desc">{{ anomaly.description }}</div>
          <div class="anomaly-actions">
            <NButton size="tiny" quaternary type="primary"
                     @click="commentingAnomalyId = commentingAnomalyId === anomaly.id ? null : anomaly.id">
              批注
            </NButton>
          </div>
          <div v-if="commentingAnomalyId === anomaly.id" class="comment-box">
            <NInput v-model:value="commentDraft" type="textarea" :rows="2" maxlength="500" show-count
                    placeholder="对此条的建议（咨询参考，不构成记账指令）" />
            <NButton size="small" type="primary" :loading="submittingOverall" @click="submitAnomalyComment(anomaly)">
              提交批注
            </NButton>
          </div>
        </div>
      </section>

      <!-- ③ 勾稽核对 -->
      <section class="share-section" aria-labelledby="sec-recon">
        <h2 id="sec-recon">三、票流账勾稽</h2>
        <template v-if="unmatched?.status === 'unavailable'">
          <p class="muted">{{ unmatched?.message || '票流账差异数据不可用。' }}</p>
        </template>
        <template v-else>
          <div class="recon-row">
            <span>银行有、账上没有：<strong>{{ unmatched?.bank_only_count ?? 0 }}</strong> 笔</span>
            <span>账上有、银行没有：<strong>{{ unmatched?.book_only_count ?? 0 }}</strong> 笔</span>
            <span>核销影响异常：<strong>{{ unmatched?.matched_effect_issue_count ?? 0 }}</strong> 笔</span>
            <span v-if="unmatched?.balance_reconciliation?.has_data">
              余额调节：
              <strong :class="{ 'recon-bad': !unmatched.balance_reconciliation.flow_balanced }">
                {{ unmatched.balance_reconciliation.flow_balanced ? '连续' : '不连续' }}
              </strong>
              <template v-if="unmatched.balance_reconciliation.book_difference">
                （差额 {{ unmatched.balance_reconciliation.book_difference }}）
              </template>
            </span>
          </div>
          <details v-if="unmatched?.bank_only?.length" class="recon-details">
            <summary>银行未入账明细（{{ unmatched.bank_only.length }}）</summary>
            <table class="mini-table">
              <tbody>
                <tr v-for="(row, i) in unmatched.bank_only" :key="i">
                  <td>{{ row.date }}</td><td>{{ row.description }}</td><td class="num">¥{{ row.amount }}</td>
                </tr>
              </tbody>
            </table>
          </details>
          <details v-if="unmatched?.book_only?.length" class="recon-details">
            <summary>账面未关联流水明细（{{ unmatched.book_only.length }}）</summary>
            <table class="mini-table">
              <tbody>
                <tr v-for="(row, i) in unmatched.book_only" :key="i">
                  <td>{{ row.date || '—' }}</td><td>{{ row.title || '—' }}</td><td class="num">¥{{ row.amount ?? '—' }}</td>
                </tr>
              </tbody>
            </table>
          </details>
        </template>
      </section>

      <!-- ④ 试算平衡表 -->
      <section class="share-section" aria-labelledby="sec-balance">
        <h2 id="sec-balance">四、科目余额表（截至本月）</h2>
        <div class="balance-scroll">
          <table class="mini-table report-table">
            <thead>
              <tr><th>科目</th><th>科目名称</th><th class="num">借方累计</th><th class="num">贷方累计</th><th class="num">余额</th></tr>
            </thead>
            <tbody>
              <tr v-for="row in view.snapshot.trialBalance" :key="row.accountCode">
                <td>{{ row.accountCode }}</td><td>{{ row.accountName }}</td>
                <td class="num">{{ row.debit }}</td><td class="num">{{ row.credit }}</td><td class="num">{{ row.balance }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="muted balance-total">
          当月发生额：借 {{ view.snapshot.checks?.debit || '0.00' }} / 贷 {{ view.snapshot.checks?.credit || '0.00' }}
        </p>
      </section>

      <!-- ⑤ 总体意见 -->
      <section class="share-section" aria-labelledby="sec-opinion">
        <h2 id="sec-opinion">五、总体意见</h2>
        <div v-if="!authorName || nicknameEditing" class="nickname-row">
          <NInput v-model:value="authorName" size="small" maxlength="64" placeholder="您的署名（如：注册会计师·陈）" style="max-width: 280px" />
          <NButton size="small" @click="saveNickname">保存署名</NButton>
        </div>
        <NInput v-model:value="overallOpinion" type="textarea" :rows="4" maxlength="500" show-count
                placeholder="账套整体结论与建议（经营者将在应用内看到此意见）" />
        <div class="opinion-actions">
          <NButton type="primary" :loading="submittingOverall" @click="submitOverall">提交总体意见</NButton>
        </div>
      </section>

      <!-- 已提交批注 -->
      <section v-if="annotations.length" class="share-section" aria-labelledby="sec-comments">
        <h2 id="sec-comments">已提交批注（{{ annotations.length }}）</h2>
        <ul class="comment-list">
          <li v-for="ann in annotations" :key="ann.id" :data-status="ann.status">
            <div class="comment-head">
              <strong>{{ ann.authorName }}</strong>
              <span class="muted">{{ ann.createdAt?.replace('T', ' ').slice(0, 16) }}</span>
              <NTag size="tiny" :bordered="false"
                    :type="ann.status === 'open' ? 'warning' : 'success'">
                {{ ann.status === 'open' ? '待处理' : '已处理' }}
              </NTag>
            </div>
            <p class="comment-body">{{ ann.body }}</p>
          </li>
        </ul>
      </section>

      <footer class="share-footer muted">
        SoloBooks 梭罗记账 · 账套健康度体检视图 · 本页数据为生成链接时的脱敏快照
      </footer>
    </template>
  </div>
</template>

<style scoped>
.share-page { max-width: 960px; margin: 0 auto; padding: 24px 16px 48px; }
.share-header { display: flex; justify-content: space-between; align-items: flex-end; gap: 12px; flex-wrap: wrap; }
.share-title { display: flex; align-items: center; gap: 10px; }
.share-title h1 { font-size: 20px; margin: 0; }
.share-month { color: var(--text-3, #888); font-size: 13px; }
.share-meta { display: flex; align-items: center; gap: 8px; color: var(--text-3, #888); font-size: 12px; }
.share-disclaimer {
  margin: 14px 0 20px; padding: 10px 14px; border-radius: 8px; font-size: 13px;
  background: var(--warning-1, #fdf6ec); color: var(--warning-5, #9a6134);
  border: 1px solid var(--warning-3, #f0d9b5);
}
.share-loading { display: block; margin: 80px auto; }
.share-section { margin-bottom: 32px; }
.share-section h2 { font-size: 16px; margin: 0 0 12px; }
.check-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; }
.check-card { border: 1px solid var(--border-2, #eee); border-radius: 8px; padding: 10px 12px; }
.check-card[data-status='passed'] { border-color: var(--success-3, #b7e8c1); }
.check-card[data-status='blocking'] { border-color: var(--error-3, #f3c1c1); }
.check-name { font-weight: 600; margin-bottom: 6px; }
.check-detail { font-size: 12px; color: var(--text-3, #888); margin-top: 6px; min-height: 16px; }
.summary-row { display: flex; gap: 12px; margin-top: 14px; flex-wrap: wrap; }
.summary-item { flex: 1; min-width: 120px; border: 1px solid var(--border-2, #eee); border-radius: 8px; padding: 10px 12px; }
.summary-item .label { display: block; font-size: 12px; color: var(--text-3, #888); margin-bottom: 4px; }
.summary-item .value { font-size: 16px; font-weight: 600; font-variant-numeric: tabular-nums; }
.anomaly-row { border: 1px solid var(--border-2, #eee); border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; }
.anomaly-row[data-level='high'] { border-left: 3px solid var(--error-5, #d03050); }
.anomaly-main { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; }
.anomaly-title { font-weight: 500; }
.anomaly-amount { margin-left: auto; font-variant-numeric: tabular-nums; color: var(--text-2, #555); }
.anomaly-desc { font-size: 13px; color: var(--text-3, #888); margin: 6px 0 4px; }
.anomaly-actions { text-align: right; }
.comment-box { margin-top: 8px; display: flex; flex-direction: column; gap: 8px; align-items: flex-end; }
.comment-box :deep(.n-input) { width: 100%; }
.recon-row { display: flex; gap: 18px; flex-wrap: wrap; font-size: 14px; }
.recon-bad { color: var(--error-5, #d03050); }
.recon-details { margin-top: 10px; font-size: 13px; }
.recon-details summary { cursor: pointer; color: var(--text-2, #555); }
.balance-scroll { overflow-x: auto; border: 1px solid var(--border-2, #eee); border-radius: 8px; }
.balance-total { margin-top: 8px; }
.mini-table { width: 100%; border-collapse: collapse; font-size: 13px; }
.mini-table th, .mini-table td { padding: 7px 10px; text-align: left; border-bottom: 1px solid var(--border-1, #f2f2f2); }
.report-table th { background: var(--fill-1, #fafafa); position: sticky; top: 0; }
.mini-table .num { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
.nickname-row { display: flex; gap: 8px; margin-bottom: 10px; align-items: center; }
.opinion-actions { margin-top: 10px; text-align: right; }
.comment-list { list-style: none; padding: 0; margin: 0; }
.comment-list li { border: 1px solid var(--border-2, #eee); border-radius: 8px; padding: 10px 12px; margin-bottom: 8px; }
.comment-list li[data-status='open'] { border-left: 3px solid var(--warning-5, #c2872e); }
.comment-head { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.comment-body { margin: 6px 0 0; font-size: 14px; white-space: pre-wrap; }
.share-footer { text-align: center; font-size: 12px; margin-top: 32px; }
.muted { color: var(--text-3, #888); font-size: 13px; }

@media print {
  .share-meta, .anomaly-actions, .comment-box, .opinion-actions, .nickname-row { display: none !important; }
}
</style>
