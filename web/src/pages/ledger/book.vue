<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { usePageRefresh } from '@/composables/usePageRefresh'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { NButton, NDatePicker, NInput, NModal, NSelect, NSpin, useMessage } from 'naive-ui'
import {
  diffLedgerBookSnapshots,
  getLedgerBook,
  getLedgerBookSnapshot,
  issueLedgerBookSnapshot,
  listLedgerBookSnapshots,
  voidLedgerBookSnapshot,
  type BookScope,
  type LedgerBook,
  type LedgerSnapshotDiff,
  type LedgerBookSnapshotSummary,
  type LedgerDetailLine,
  type LedgerDetailPage,
} from '@/api/ledger'
import { useAuthStore } from '@/stores/auth'
import { exportLedgerBookToExcel } from '@/utils/export'
import { reportErrorToast } from '@/utils/errorPresenter'
import { SoloErrorState } from '@/components/ui'
import { formatDate, getCurrentMonth } from '@/utils/format'
import {
  directionLabel,
  ledgerAmountCell,
  ledgerBalanceOrDash,
  ledgerMoney,
  monthLabel,
  quantityText,
} from '@/utils/ledgerFormat'

const route = useRoute()
const router = useRouter()
const message = useMessage()
const authStore = useAuthStore()

const requestedMonth = String(route.query.month || '')
const month = ref(/^\d{4}-(0[1-9]|1[0-2])$/.test(requestedMonth) ? requestedMonth : getCurrentMonth())
// 账册口径可从 URL 带入，便于把"年度册"链接发给别人
const scope = ref<BookScope>(String(route.query.scope || '') === 'year' ? 'year' : 'month')
const year = ref(Number(month.value.slice(0, 4)))
const loading = ref(false)
const book = ref<LedgerBook | null>(null)
/** 生成失败的原因。与「这个月没有账簿」分开渲染，避免把失败说成空数据 */
const bookError = ref('')
const showZeroRows = ref(false)

const issuedList = ref<LedgerBookSnapshotSummary[]>([])
const snapshotMode = ref<{ id: string; bookNo: string; status: string } | null>(null)
const issuing = ref(false)
const voidDialog = ref(false)
const voidTarget = ref<LedgerBookSnapshotSummary | null>(null)
const diffDialog = ref(false)
const diffLoading = ref(false)
const diffData = ref<LedgerSnapshotDiff | null>(null)
const voidReason = ref('')
const voiding = ref(false)

const yearOptions = computed(() =>
  Array.from({ length: 6 }, (_, index) => {
    const value = new Date().getFullYear() - index
    return { label: `${value} 年`, value }
  }),
)

const preparer = computed(() => authStore.user?.nickname || '制表人')
const printDate = computed(() => formatDate(new Date()))
const periodText = computed(() =>
  scope.value === 'year' ? `${year.value} 年度` : monthLabel(month.value),
)
const currentPeriod = computed(() => (scope.value === 'year' ? String(year.value) : month.value))
const meta = computed(() => book.value?.meta)
const warnings = computed(() => book.value?.warnings || [])
const totalPages = computed(() => book.value?.pagination?.totalPages || 1)
const pageSize = computed(
  // 仅在后端未返回 pagination 时兜底；数值必须与后端 ledger_book_service.PAGE_SIZE 同步
  () => book.value?.pagination?.pageSize || { balance: 12, detail: 10, journal: 12, chronological: 12 },
)
const sectionIndex = computed(() => {
  const map: Record<string, { pageNo: number; pageCount: number }> = {}
  for (const section of book.value?.pagination?.sections || []) {
    map[section.key] = { pageNo: section.pageNo, pageCount: section.pageCount }
  }
  return map
})

/** 当期最近一册有效签发 */
const latestIssued = computed(() => issuedList.value.find((item) => item.status === 'issued') || null)
/** 已签发但内容已变（反结账 / 补记）：旧册照样能打，只是不能再当"当前账" */
const isStale = computed(() => {
  if (!latestIssued.value || !book.value || snapshotMode.value) return false
  return latestIssued.value.contentHash !== book.value.meta.contentHash
})
const canIssue = computed(
  () =>
    !snapshotMode.value
    && book.value?.meta.periodStatus === 'closed'
    && (!latestIssued.value || isStale.value),
)

const visibleBalanceRows = computed(() => {
  const items = book.value?.balance.items || []
  return showZeroRows.value ? items : items.filter((row) => row.hasActivity || row.hasBalance)
})

const generalItems = computed(() => book.value?.general?.items || [])
const detailPages = computed(() => book.value?.details || [])
const journalBooks = computed(() => (book.value?.journals || []).filter((item) => item.rows.length > 0))
const chronologicalRows = computed(() => book.value?.chronological.items || [])

const balanceHint = computed(() => {
  const balance = book.value?.balance
  if (!balance) return ''
  if (!balance.isBalanced) return '本期借方发生额与贷方发生额不相等，请先核对凭证。'
  if (!balance.isOpeningBalanced) return '期初余额借贷合计不相等，请检查期初。'
  if (!balance.isClosingBalanced) return '期末余额借贷合计不相等，请检查凭证。'
  return '期初、本期发生额、期末余额借贷合计均相等。'
})

function chunks<T>(rows: T[], size: number): T[][] {
  if (!rows.length) return [[]]
  const out: T[][] = []
  for (let index = 0; index < rows.length; index += size) out.push(rows.slice(index, index + size))
  return out
}

/** F6 年度册明细账按月分节：每物理页标题带该页首行业务日期的月份，便于翻册定位。
 *  分页逻辑不变（仍按 PAGE_SIZE 均匀切行），只增强可读性。 */
function chunkMonth(chunk: Array<{ date: string }>): string {
  if (meta.value?.scope !== 'year') return ''
  const first = chunk.find((line) => line?.date)
  return first ? String(first.date).slice(0, 7) : ''
}

function pageStart(key: string): number {
  return sectionIndex.value[key]?.pageNo || 1
}

function pageCount(key: string): number {
  return sectionIndex.value[key]?.pageCount || 1
}

// ── 屏幕端快速导航（不打印）：锚点定位 + scrollspy ──
interface BookNavItem { key: string; label: string; pageNo: number; children?: BookNavItem[] }

const navItems = computed<BookNavItem[]>(() => {
  if (!book.value) return []
  const items: BookNavItem[] = [{ key: 'balance', label: '科目余额表', pageNo: pageStart('balance') }]
  if (generalItems.value.length) {
    items.push({ key: 'general', label: '总　账', pageNo: pageStart('general') })
  }
  if (detailPages.value.length) {
    items.push({
      key: 'detail',
      label: '明细账',
      pageNo: pageStart(`detail:${detailPages.value[0].code}`),
      children: detailPages.value.map(page => ({
        key: `detail:${page.code}`,
        label: `${page.code} ${page.name}`,
        pageNo: pageStart(`detail:${page.code}`),
      })),
    })
  }
  if (journalBooks.value.length) {
    items.push({
      key: 'journal',
      label: '日记账',
      pageNo: pageStart(`journal:${journalBooks.value[0].kind}`),
      children: journalBooks.value.map(journal => ({
        key: `journal:${journal.kind}`,
        label: journal.name,
        pageNo: pageStart(`journal:${journal.kind}`),
      })),
    })
  }
  if (chronologicalRows.value.length) {
    items.push({ key: 'chronological', label: '序时账', pageNo: pageStart('chronological') })
  }
  items.push({ key: 'signoff', label: '签发落款', pageNo: totalPages.value })
  return items
})

/** scrollspy 用的一级+二级平铺 key 清单 */
const navLeafKeys = computed(() => navItems.value.flatMap(item =>
  item.children?.length ? item.children.map(child => child.key) : [item.key]))

// 账簿导航默认收起：展开态是 fixed 面板，会压住工具栏的「打印 / 导出 PDF」按钮。
// 收起后只剩一个 FAB，需要时点开。
const navCollapsed = ref(true)
const expandedNav = ref<Record<string, boolean>>({ detail: true })
const activeSheetKey = ref('')

/** 导航 key 沿用后端分节 key（detail:1002 冒号分隔），锚点 id 一律转成连字符 */
function sheetEl(key: string) {
  return document.getElementById(`sheet-${key.replace(/:/g, '-')}`)
}

function jumpToSheet(key: string) {
  const el = sheetEl(key)
  const container = document.querySelector('main.content') as HTMLElement | null
  if (!el || !container) return
  // 不用 scrollIntoView：它在嵌套滚动容器 + smooth 组合下会静默失效（实测）。
  // 直接算出目标在容器内的位置瞬时定位；12px 只是贴着容器顶留一点呼吸空隙
  // （顶部固定栏在容器外，天然不会遮挡）。
  const headerOffset = 12
  const target = el.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop - headerOffset
  container.scrollTo({ top: Math.max(0, target), behavior: 'auto' })
}

/** 目录里的「报表中心」条目：多栏账与核算项目余额没装进这本册子，直接跳过去看 */
function jumpToReports() {
  router.push({ path: '/reports', query: { month: month.value } })
}

function toggleNavExpand(key: string) {
  expandedNav.value = { ...expandedNav.value, [key]: !expandedNav.value[key] }
}

/** 分组行点击：跳到该册首页；有子项时同时展开子菜单（收起动作只在箭头上） */
function onNavGroupClick(item: BookNavItem) {
  jumpToSheet(item.children?.length ? item.children[0].key : item.key)
  if (item.children?.length) {
    expandedNav.value = { ...expandedNav.value, [item.key]: true }
  }
}

// 用 setTimeout 而非 rAF 节流：后台标签页 rAF 会冻结，回前台时高亮会卡在旧位置
let navSpyTimer: ReturnType<typeof setTimeout> | null = null
function onBookScroll() {
  if (navSpyTimer) return
  navSpyTimer = setTimeout(() => {
    navSpyTimer = null
    // 阈值 = 滚动容器顶部 + 40px：与 jumpToSheet 的落点（容器顶 + 12px）同口径，
    // 点击导航后高亮必须落在被点击的那一节上
    const containerTop = document.querySelector('main.content')?.getBoundingClientRect().top ?? 0
    let current = navLeafKeys.value[0] || ''
    for (const key of navLeafKeys.value) {
      const el = sheetEl(key)
      if (el && el.getBoundingClientRect().top <= containerTop + 40) current = key
    }
    activeSheetKey.value = current
  }, 80)
}

/** 应用布局在 main.content 内部滚动，window 不滚——scrollspy 必须挂对容器 */
let navScrollTarget: HTMLElement | Document | null = null

function hasQuantityColumn(page: LedgerDetailPage): boolean {
  return page.hasQuantity
}

function leadColspan(page: LedgerDetailPage): number {
  return page.hasQuantity ? 6 : 4
}

function cpList(line: LedgerDetailLine): string[] {
  return line.counterparties.map((item) => `${item.code} ${item.name}`)
}

function cpShort(line: LedgerDetailLine): string {
  const list = cpList(line)
  if (!list.length) return ''
  if (list.length <= 2) return list.join('、')
  return `${list.slice(0, 2).join('、')} 等 ${list.length} 项`
}

function cpFull(line: LedgerDetailLine): string {
  return cpList(line).join('、')
}

function dirText(direction: string): string {
  return direction === 'flat' ? '' : directionLabel(direction)
}

let reloadTimer: number | undefined

function buildParams() {
  return scope.value === 'year'
    ? { year: year.value, scope: 'year' as const, showZeroRows: showZeroRows.value }
    : { month: month.value, scope: 'month' as const, showZeroRows: showZeroRows.value }
}

function errorText(err: unknown, fallback: string): string {
  const detail = (err as { response?: { data?: { detail?: { message?: string } | string } } })?.response?.data?.detail
  if (typeof detail === 'string') return detail
  return detail?.message || fallback
}

async function loadIssued() {
  try {
    const result = await listLedgerBookSnapshots(currentPeriod.value)
    issuedList.value = result.items || []
  } catch {
    // 快照列表失败不影响预览；只是签发状态暂时未知
    issuedList.value = []
  }
}

async function load(keepSnapshot = false) {
  loading.value = true
  if (!keepSnapshot) snapshotMode.value = null
  try {
    const [live] = await Promise.all([getLedgerBook(buildParams()), loadIssued()])
    book.value = live
    bookError.value = ''
  } catch (err) {
    book.value = null
    bookError.value = errorText(err, '生成电子账簿失败')
    reportErrorToast(message, err, '生成电子账簿失败')
  } finally {
    loading.value = false
  }
}

function scheduleReload() {
  window.clearTimeout(reloadTimer)
  reloadTimer = window.setTimeout(load, 200)
}

watch([month, year, scope], () => {
  if (scope.value === 'year') year.value = Number(month.value.slice(0, 4))
  scheduleReload()
})

onMounted(() => {
  load()
  navScrollTarget = (document.querySelector('main.content') as HTMLElement | null) || document
  navScrollTarget.addEventListener('scroll', onBookScroll, { passive: true })
  onBookScroll()
})
onBeforeUnmount(() => {
  window.clearTimeout(reloadTimer)
  navScrollTarget?.removeEventListener('scroll', onBookScroll)
  if (navSpyTimer) clearTimeout(navSpyTimer)
})

async function issue() {
  if (issuing.value) return
  issuing.value = true
  try {
    const detail = await issueLedgerBookSnapshot(buildParams())
    book.value = detail.payload
    snapshotMode.value = { id: detail.id, bookNo: detail.bookNo, status: detail.status }
    await loadIssued()
    message.success(`已签发正式账簿 ${detail.bookNo}，校验码 ${detail.contentHash.slice(0, 8)}`)
  } catch (err) {
    message.error(errorText(err, '签发失败'))
  } finally {
    issuing.value = false
  }
}

async function openSnapshot(item: LedgerBookSnapshotSummary) {
  try {
    const detail = await getLedgerBookSnapshot(item.id)
    book.value = detail.payload
    snapshotMode.value = { id: detail.id, bookNo: detail.bookNo, status: detail.status }
    message.success(`正在查看 ${detail.bookNo}（${detail.status === 'void' ? '已作废' : '已签发'}）`)
  } catch (err) {
    message.error(errorText(err, '读取账簿快照失败'))
  }
}

function backToLive() {
  snapshotMode.value = null
  load()
}

/** 与上一册比对：列表按时间倒序，后一项就是"上一册" */
function canDiff(item: LedgerBookSnapshotSummary): boolean {
  const index = issuedList.value.findIndex((row) => row.id === item.id)
  return index >= 0 && index < issuedList.value.length - 1
}

async function openDiff(item: LedgerBookSnapshotSummary) {
  const index = issuedList.value.findIndex((row) => row.id === item.id)
  const previous = issuedList.value[index + 1]
  if (!previous) return
  diffDialog.value = true
  diffLoading.value = true
  diffData.value = null
  try {
    diffData.value = await diffLedgerBookSnapshots(item.id, previous.id)
  } catch (err) {
    message.error(errorText(err, '账簿比对失败'))
  } finally {
    diffLoading.value = false
  }
}

function openVoidDialog(item: LedgerBookSnapshotSummary) {
  voidTarget.value = item
  voidReason.value = ''
  voidDialog.value = true
}

async function confirmVoid() {
  if (!voidTarget.value) return
  if (!voidReason.value.trim()) {
    message.warning('请填写作废原因')
    return
  }
  voiding.value = true
  try {
    const target = voidTarget.value
    await voidLedgerBookSnapshot(target.id, voidReason.value.trim())
    voidDialog.value = false
    await loadIssued()
    if (snapshotMode.value?.id === target.id) snapshotMode.value = { ...snapshotMode.value, status: 'void' }
    message.success('已作废，原册仍保留可查')
  } catch (err) {
    message.error(errorText(err, '作废失败'))
  } finally {
    voiding.value = false
  }
}

/** 含零发生额科目会改变分页页数，必须整册重取（保留快照视图，否则会退回实时账册） */
function onToggleZeroRows(event: Event) {
  showZeroRows.value = (event.target as HTMLInputElement).checked
  load(!!snapshotMode.value)
}

function print() {
  window.print()
}

function exportExcel() {
  if (!book.value) {
    message.warning('当前没有可导出的账簿数据')
    return
  }
  exportLedgerBookToExcel({
    ...book.value,
    meta: {
      ...book.value.meta,
      generatedBy: book.value.meta.generatedBy || preparer.value,
      generatedAt: book.value.meta.generatedAt || new Date().toISOString(),
    },
  })
  message.success('全账簿已导出')
}

// 从别的标签切回来时静默重拉，避免对着旧数字干活
usePageRefresh(load)
</script>

<template>
  <div class="book-page">
    <div class="no-print toolbar">
      <NButton text @click="router.back()">← 返回</NButton>
      <div class="toolbar-center">
        <div class="scope-switch">
          <button :class="{ active: scope === 'month' }" @click="scope = 'month'">本月</button>
          <button :class="{ active: scope === 'year' }" @click="scope = 'year'">全年</button>
        </div>
        <NDatePicker
          v-if="scope === 'month'"
          v-model:formatted-value="month"
          type="month"
          value-format="yyyy-MM"
          format="yyyy-MM"
          style="width: 140px;"
        />
        <NSelect v-else v-model:value="year" :options="yearOptions" style="width: 120px;" />
      </div>
      <div class="toolbar-right">
        <NButton secondary :disabled="!book" @click="exportExcel">
          <template #icon><i class="ri-file-excel-2-line" /></template>
          导出 Excel
        </NButton>
        <NButton type="primary" :disabled="!book" @click="print">
          <template #icon><i class="ri-printer-line" /></template>
          打印 / 导出 PDF
        </NButton>
      </div>
    </div>

    <!-- 签发状态条 -->
    <div v-if="book" class="no-print issue-bar">
      <div class="issue-state">
        <template v-if="snapshotMode">
          <span class="state-dot issued" />
          <b>{{ snapshotMode.bookNo }}</b>
          <span class="state-hint">
            {{ snapshotMode.status === 'void' ? '已作废（仅存档查阅）' : '已签发 · 内容已定版' }}
          </span>
          <NButton text type="primary" size="tiny" @click="backToLive">返回实时账册</NButton>
        </template>
        <template v-else-if="latestIssued && !isStale">
          <span class="state-dot issued" />
          <b>{{ latestIssued.bookNo }}</b>
          <span class="state-hint">
            校验码 {{ latestIssued.contentHash.slice(0, 8) }} · {{ latestIssued.pageCount }} 页 ·
            {{ latestIssued.generatedBy || '—' }}
          </span>
        </template>
        <template v-else-if="isStale">
          <span class="state-dot stale" />
          <b>账簿已过期</b>
          <span class="state-hint">
            期间已反结账或存在新凭证（原册 {{ latestIssued?.bookNo }}），请重新签发
          </span>
        </template>
        <template v-else-if="meta?.periodStatus !== 'closed'">
          <span class="state-dot draft" />
          <b>预览件</b>
          <span class="state-hint">本期未结账，不能签发正式账簿</span>
        </template>
        <template v-else>
          <span class="state-dot draft" />
          <b>未签发</b>
          <span class="state-hint">本期已结账，可签发正式账簿</span>
        </template>
      </div>
      <div class="issue-actions">
        <NButton
          v-if="canIssue"
          size="small"
          type="primary"
          :loading="issuing"
          @click="issue"
        >
          签发正式账簿
        </NButton>
      </div>
    </div>

    <!-- 历史账册 -->
    <div v-if="issuedList.length" class="no-print history">
      <span class="history-title">已签发账册</span>
      <div v-for="item in issuedList" :key="item.id" class="history-item" :class="{ void: item.status === 'void' }">
        <b>{{ item.bookNo }}</b>
        <span>{{ item.generatedAt ? formatDate(item.generatedAt) : '—' }}</span>
        <span>{{ item.pageCount }} 页</span>
        <span class="history-hash">{{ item.contentHash.slice(0, 8) }}</span>
        <NButton text size="tiny" type="primary" @click="openSnapshot(item)">打开</NButton>
        <NButton v-if="canDiff(item)" text size="tiny" @click="openDiff(item)">与上一册比对</NButton>
        <NButton
          v-if="item.status === 'issued'"
          text
          size="tiny"
          type="warning"
          @click="openVoidDialog(item)"
        >
          作废
        </NButton>
        <span v-else class="history-void">已作废</span>
      </div>
    </div>

    <div v-if="warnings.length" class="no-print notice-bar">
      <p v-for="warning in warnings" :key="warning.code" class="notice-line">
        <i :class="warning.code === 'NOT_BALANCED' ? 'ri-error-warning-line' : 'ri-information-line'" />
        {{ warning.message }}
      </p>
    </div>

    <NSpin :show="loading">
      <template v-if="book">
        <!-- 封面 -->
        <section class="page-sheet cover-sheet">
          <div class="book-cover">
            <div class="cover-company">{{ meta?.companyName }}</div>
            <div class="cover-title">电 子 账 簿</div>
            <div class="cover-period">{{ periodText }}</div>
            <div class="cover-meta">
              <div><span>账套期间</span><b>{{ periodText }}</b></div>
              <div><span>会计准则</span><b>{{ meta?.accountingStandard }}</b></div>
              <div><span>凭证张数</span><b>{{ meta?.entryCount }} 张</b></div>
              <div><span>编制人</span><b>{{ meta?.generatedBy || preparer }}</b></div>
              <div><span>打印日期</span><b>{{ printDate }}</b></div>
              <div><span>账簿编号</span><b>{{ meta?.bookNo || '（预览件，未签发）' }}</b></div>
            </div>
            <div class="cover-foot">
              本账簿由梭罗记账生成，含科目余额表{{ generalItems.length ? '、总账' : '' }}、明细账、日记账与序时账
            </div>
          </div>
          <div class="page-footer">
            <span>{{ meta?.bookNo || '预览件' }}</span>
            <span>第 {{ pageStart('cover') }} 页 共 {{ totalPages }} 页</span>
          </div>
        </section>

        <!-- 目录（带页码） -->
        <section class="page-sheet">
          <h3 class="section-title">目　录</h3>
          <ol class="toc">
            <li class="toc-link" title="点击定位" @click="jumpToSheet('balance')"><span>科目余额表</span><i>{{ pageStart('balance') }}</i></li>
            <li v-if="generalItems.length" class="toc-link" title="点击定位" @click="jumpToSheet('general')"><span>总账</span><i>{{ pageStart('general') }}</i></li>
            <li v-for="page in detailPages" :key="page.code" class="toc-link" title="点击定位" @click="jumpToSheet(`detail:${page.code}`)">
              <span>{{ page.code }} {{ page.name }} 明细账</span><i>{{ pageStart(`detail:${page.code}`) }}</i>
            </li>
            <li v-for="journal in journalBooks" :key="journal.kind" class="toc-link" title="点击定位" @click="jumpToSheet(`journal:${journal.kind}`)">
              <span>{{ journal.name }}</span><i>{{ pageStart(`journal:${journal.kind}`) }}</i>
            </li>
            <li v-if="chronologicalRows.length" class="toc-link" title="点击定位" @click="jumpToSheet('chronological')">
              <span>序时账</span><i>{{ pageStart('chronological') }}</i>
            </li>
            <li class="toc-ref toc-link" title="点击前往报表中心" @click="jumpToReports">
              <span>多栏账、核算项目余额表</span><i>报表中心</i>
            </li>
            <li class="toc-ref"><span>研发支出辅助账、固定资产卡片</span><i>另册</i></li>
          </ol>
          <div class="page-footer">
            <span>{{ meta?.bookNo || '预览件' }}</span>
            <span>第 {{ pageStart('toc') }} 页 共 {{ totalPages }} 页</span>
          </div>
        </section>

        <!-- 科目余额表：按固定行数切页 -->
        <section
          v-for="(chunk, pageIndex) in chunks(visibleBalanceRows, pageSize.balance)"
          :key="`balance-${pageIndex}`"
          :id="pageIndex === 0 ? 'sheet-balance' : undefined"
          class="page-sheet"
        >
          <div class="section-head">
            <h3 class="section-title">科目余额表</h3>
            <label v-if="pageIndex === 0" class="no-print zero-toggle">
              <input :checked="showZeroRows" type="checkbox" @change="onToggleZeroRows" />
              显示无发生额科目（{{ book.balance.hiddenZeroCount }}）
            </label>
          </div>
          <div class="section-meta">{{ meta?.companyName }} · {{ periodText }} · 单位：元</div>
          <table class="book-table">
            <thead>
              <tr>
                <th class="col-code">科目编码</th>
                <th>科目名称</th>
                <th class="col-dir">期初方向</th>
                <th class="col-amount">期初余额</th>
                <th class="col-amount">借方发生</th>
                <th class="col-amount">贷方发生</th>
                <th class="col-dir">期末方向</th>
                <th class="col-amount">期末余额</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in chunk" :key="row.accountCode" :class="{ parent: row.isParent }">
                <td>{{ row.accountCode }}</td>
                <td><div class="cell-clip">{{ row.accountName }}<span v-if="row.isParent" class="hint">（子级小计）</span></div></td>
                <td class="center">{{ dirText(row.openingDirection) }}</td>
                <td class="amount">{{ ledgerBalanceOrDash(row.openingBalance) }}</td>
                <td class="amount">{{ ledgerAmountCell(row.debitTotal) || '—' }}</td>
                <td class="amount">{{ ledgerAmountCell(row.creditTotal) || '—' }}</td>
                <td class="center">{{ dirText(row.closingDirection) }}</td>
                <td class="amount strong">{{ ledgerBalanceOrDash(row.closingBalance) }}</td>
              </tr>
              <tr v-if="!visibleBalanceRows.length">
                <td colspan="8" class="empty-cell">本册期间没有可列示的科目余额</td>
              </tr>
            </tbody>
            <tfoot v-if="pageIndex === pageCount('balance') - 1 && visibleBalanceRows.length">
              <tr>
                <td colspan="4" class="total">合　计</td>
                <td class="amount total">{{ ledgerMoney(book.balance.totalDebit) }}</td>
                <td class="amount total">{{ ledgerMoney(book.balance.totalCredit) }}</td>
                <td colspan="2" class="total"></td>
              </tr>
              <tr>
                <td colspan="8" class="footnote-cell">{{ balanceHint }}</td>
              </tr>
            </tfoot>
          </table>
          <div class="page-footer">
            <span>{{ meta?.bookNo || '预览件' }}</span>
            <span>第 {{ pageStart('balance') + pageIndex }} 页 共 {{ totalPages }} 页</span>
          </div>
        </section>

        <!-- 总账：一科目一页 -->
        <section
          v-for="(page, pageIndex) in generalItems"
          :key="`general-${page.accountCode}`"
          :id="pageIndex === 0 ? 'sheet-general' : undefined"
          class="page-sheet"
        >
          <h3 class="section-title">总　账</h3>
          <div class="section-meta">
            {{ page.accountCode }} {{ page.accountName }} · {{ meta?.companyName }} · {{ generalItems.length }} 个科目 · 单位：元
          </div>
          <table class="book-table">
            <thead>
              <tr>
                <th class="col-month">月份</th>
                <th class="col-amount">借方发生</th>
                <th class="col-amount">贷方发生</th>
                <th class="col-dir">借或贷</th>
                <th class="col-amount">余额</th>
              </tr>
            </thead>
            <tbody>
              <tr class="opening-row">
                <td>年初余额</td>
                <td></td>
                <td></td>
                <td class="center">{{ dirText(page.openingDirection) }}</td>
                <td class="amount">{{ ledgerBalanceOrDash(page.opening) }}</td>
              </tr>
              <tr v-for="item in page.months" :key="item.month" :class="{ idle: !item.hasActivity }">
                <td>{{ item.month }}</td>
                <td class="amount">{{ ledgerAmountCell(item.debit) || '—' }}</td>
                <td class="amount">{{ ledgerAmountCell(item.credit) || '—' }}</td>
                <td class="center">{{ dirText(item.balanceDirection) }}</td>
                <td class="amount">{{ ledgerBalanceOrDash(item.balance) }}</td>
              </tr>
            </tbody>
            <tfoot>
              <tr class="total-row">
                <td>本年合计</td>
                <td class="amount">{{ ledgerMoney(page.totalDebit) }}</td>
                <td class="amount">{{ ledgerMoney(page.totalCredit) }}</td>
                <td class="center">{{ dirText(page.closingDirection) }}</td>
                <td class="amount strong">{{ ledgerBalanceOrDash(page.closing) }}</td>
              </tr>
            </tfoot>
          </table>
          <div class="page-footer">
            <span>{{ meta?.bookNo || '预览件' }}</span>
            <span>第 {{ pageStart('general') + pageIndex }} 页 共 {{ totalPages }} 页</span>
          </div>
        </section>

        <!-- 明细账：固定行数切页，首末页带期初与合计 -->
        <template v-for="page in detailPages" :key="`detail-${page.code}`">
          <section
            v-for="(chunk, pageIndex) in chunks(page.lines, pageSize.detail)"
            :key="`${page.code}-${pageIndex}`"
            :id="pageIndex === 0 ? `sheet-detail-${page.code}` : undefined"
            class="page-sheet"
          >
            <h3 class="section-title">
              <template v-if="chunkMonth(chunk)">{{ chunkMonth(chunk) }} · </template>{{ page.code }} {{ page.name }} 明细账
            </h3>
            <div class="section-meta">{{ meta?.companyName }} · {{ periodText }} · 单位：元</div>
            <table class="book-table">
              <thead>
                <tr>
                  <th class="col-date">日期</th>
                  <th class="col-voucher">凭证号</th>
                  <th>摘要</th>
                  <th class="col-cp">对方科目</th>
                  <th v-if="hasQuantityColumn(page)" class="col-qty">数量</th>
                  <th v-if="hasQuantityColumn(page)" class="col-price">单价</th>
                  <th class="col-amount">借方</th>
                  <th class="col-amount">贷方</th>
                  <th class="col-dir">借或贷</th>
                  <th class="col-amount">余额</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="pageIndex === 0" class="opening-row">
                  <td></td>
                  <td></td>
                  <td>期初余额</td>
                  <td></td>
                  <td v-if="hasQuantityColumn(page)" class="amount">{{ quantityText(page.quantityOpening) }}</td>
                  <td v-if="hasQuantityColumn(page)"></td>
                  <td></td>
                  <td></td>
                  <td class="center">{{ dirText(page.openingDirection) }}</td>
                  <td class="amount">{{ ledgerBalanceOrDash(page.opening) }}</td>
                </tr>
                <tr v-for="(line, index) in chunk" :key="`${page.code}-${pageIndex}-${index}`">
                  <td>{{ line.date }}</td>
                  <td>{{ line.voucherRef || '—' }}</td>
                  <td>
                    {{ line.summary }}<span v-if="line.backfill" class="badge">补记</span>
                  </td>
                  <td class="cp" :title="cpFull(line)">{{ cpShort(line) }}</td>
                  <td v-if="hasQuantityColumn(page)" class="amount">{{ quantityText(line.quantity) }}</td>
                  <td v-if="hasQuantityColumn(page)" class="amount">{{ line.unitPrice ? ledgerMoney(line.unitPrice) : '' }}</td>
                  <td class="amount">{{ ledgerAmountCell(line.debit) }}</td>
                  <td class="amount">{{ ledgerAmountCell(line.credit) }}</td>
                  <td class="center">{{ dirText(line.balanceDirection) }}</td>
                  <td class="amount">{{ ledgerBalanceOrDash(line.balance) }}</td>
                </tr>
              </tbody>
              <tfoot v-if="pageIndex === pageCount(`detail:${page.code}`) - 1">
                <tr class="total-row">
                  <td :colspan="leadColspan(page)">本期合计（{{ page.lineCount }} 笔）</td>
                  <td class="amount">{{ ledgerMoney(page.periodTotals.debit) }}</td>
                  <td class="amount">{{ ledgerMoney(page.periodTotals.credit) }}</td>
                  <td class="center">{{ dirText(page.closingDirection) }}</td>
                  <td class="amount strong">{{ ledgerBalanceOrDash(page.closing) }}</td>
                </tr>
                <tr class="total-row">
                  <td :colspan="leadColspan(page)">本年累计</td>
                  <td class="amount">{{ ledgerMoney(page.yearTotals.debit) }}</td>
                  <td class="amount">{{ ledgerMoney(page.yearTotals.credit) }}</td>
                  <td colspan="2"></td>
                </tr>
              </tfoot>
            </table>
            <div class="page-footer">
              <span>{{ meta?.bookNo || '预览件' }} · {{ page.code }}</span>
              <span>第 {{ pageStart(`detail:${page.code}`) + pageIndex }} 页 共 {{ totalPages }} 页</span>
            </div>
          </section>
        </template>

        <!-- 日记账 -->
        <template v-for="journal in journalBooks" :key="`journal-${journal.kind}`">
          <section
            v-for="(chunk, pageIndex) in chunks(journal.rows, pageSize.journal)"
            :key="`${journal.kind}-${pageIndex}`"
            :id="pageIndex === 0 ? `sheet-journal-${journal.kind}` : undefined"
            class="page-sheet"
          >
            <h3 class="section-title">{{ journal.name }}</h3>
            <div class="section-meta">{{ meta?.companyName }} · {{ periodText }} · 单位：元</div>
            <table class="book-table">
              <thead>
                <tr>
                  <th class="col-date">日期</th>
                  <th>摘要</th>
                  <th class="col-count">凭证张数</th>
                  <th class="col-amount">借方</th>
                  <th class="col-amount">贷方</th>
                  <th class="col-dir">借或贷</th>
                  <th class="col-amount">余额</th>
                </tr>
              </thead>
              <tbody>
                <tr v-if="pageIndex === 0" class="opening-row">
                  <td></td>
                  <td>期初余额</td>
                  <td></td>
                  <td></td>
                  <td></td>
                  <td class="center">{{ dirText(journal.openingDirection) }}</td>
                  <td class="amount">{{ ledgerBalanceOrDash(journal.opening) }}</td>
                </tr>
                <tr v-for="day in chunk" :key="day.date">
                  <td>{{ day.date }}</td>
                  <td>
                    {{ day.summary }}<span v-if="day.backfill" class="badge">补记</span>
                  </td>
                  <td class="center">{{ day.voucherCount }}</td>
                  <td class="amount">{{ ledgerAmountCell(day.debit) }}</td>
                  <td class="amount">{{ ledgerAmountCell(day.credit) }}</td>
                  <td class="center">{{ dirText(day.balanceDirection) }}</td>
                  <td class="amount">{{ ledgerBalanceOrDash(day.balance) }}</td>
                </tr>
              </tbody>
              <tfoot v-if="pageIndex === pageCount(`journal:${journal.kind}`) - 1">
                <tr class="total-row">
                  <td colspan="3">本册合计</td>
                  <td class="amount">{{ ledgerMoney(journal.totalDebit) }}</td>
                  <td class="amount">{{ ledgerMoney(journal.totalCredit) }}</td>
                  <td class="center">{{ dirText(journal.closingDirection) }}</td>
                  <td class="amount strong">{{ ledgerBalanceOrDash(journal.closing) }}</td>
                </tr>
              </tfoot>
            </table>
            <div class="page-footer">
              <span>{{ meta?.bookNo || '预览件' }} · {{ journal.code }}</span>
              <span>第 {{ pageStart(`journal:${journal.kind}`) + pageIndex }} 页 共 {{ totalPages }} 页</span>
            </div>
          </section>
        </template>

        <!-- 序时账 -->
        <section
          v-for="(chunk, pageIndex) in chronologicalRows.length ? chunks(chronologicalRows, pageSize.chronological) : []"
          :key="`chrono-${pageIndex}`"
          :id="pageIndex === 0 ? 'sheet-chronological' : undefined"
          class="page-sheet"
        >
          <h3 class="section-title">序时账</h3>
          <div class="section-meta">
            {{ meta?.companyName }} · {{ periodText }} · 共 {{ book.chronological.voucherCount }} 张凭证 · 单位：元
          </div>
          <table class="book-table">
            <thead>
              <tr>
                <th class="col-date">日期</th>
                <th class="col-voucher">凭证号</th>
                <th>摘要</th>
                <th class="col-account">会计科目</th>
                <th class="col-amount">借方</th>
                <th class="col-amount">贷方</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="(row, index) in chunk" :key="`${row.entryId}-${pageIndex}-${index}`">
                <td>{{ row.date }}</td>
                <td>{{ row.voucherRef || '—' }}</td>
                <td><div class="cell-clip">{{ row.summary }}<span v-if="row.backfill" class="badge">补记</span></div></td>
                <td><div class="cell-clip">{{ row.accountCode }} {{ row.accountName }}</div></td>
                <td class="amount">{{ ledgerAmountCell(row.debit) }}</td>
                <td class="amount">{{ ledgerAmountCell(row.credit) }}</td>
              </tr>
            </tbody>
            <tfoot v-if="pageIndex === pageCount('chronological') - 1">
              <tr class="total-row">
                <td colspan="4">合　计</td>
                <td class="amount">{{ ledgerMoney(book.chronological.totalDebit) }}</td>
                <td class="amount">{{ ledgerMoney(book.chronological.totalCredit) }}</td>
              </tr>
            </tfoot>
          </table>
          <div class="page-footer">
            <span>{{ meta?.bookNo || '预览件' }}</span>
            <span>第 {{ pageStart('chronological') + pageIndex }} 页 共 {{ totalPages }} 页</span>
          </div>
        </section>

        <!-- 落款 -->
        <section id="sheet-signoff" class="page-sheet">
          <div class="compile-notes">
            <h3 class="section-title">编制说明</h3>
            <ol>
              <li>本册按记账期间归集，不按业务发生日期；期内补记的凭证仍计入本册。</li>
              <li>明细账中标注「补记」的行，业务日期早于本册期间，金额计入本册所属期间。</li>
              <li>日记账与序时账按业务发生日期排列，便于与实际收付逐笔核对。</li>
              <li>金额单位为元、保留两位小数；余额不写负号，方向列示于「借或贷」栏。</li>
            </ol>
          </div>
          <div class="signoff">
            <h3 class="section-title">签　章</h3>
            <div class="signoff-row">
              <span class="signoff-item">记账人：<em></em></span>
              <span class="signoff-item">审核人：<em></em></span>
              <span class="signoff-item">会计机构负责人：<em></em></span>
            </div>
            <div class="signoff-row">
              <span>制表人：{{ meta?.generatedBy || preparer }}</span>
              <span>制表日期：{{ printDate }}</span>
              <span>账簿编号：{{ meta?.bookNo || '—' }}</span>
            </div>
            <div v-if="meta?.contentHash" class="signoff-row soft">
              校验码：{{ meta.contentHash }}
            </div>
          </div>
          <div class="page-footer">
            <span>{{ meta?.bookNo || '预览件' }}</span>
            <span>第 {{ pageStart('signoff') }} 页 共 {{ totalPages }} 页</span>
          </div>
        </section>
      </template>

      <div v-else-if="!loading && bookError" class="book-empty">
        <SoloErrorState
          title="电子账簿加载失败"
          description="没能生成这一期的账簿数据，可以直接重试。"
          :detail="bookError"
          @retry="load()"
        />
      </div>

      <div v-else-if="!loading" class="book-empty">
        <i class="ri-book-open-line" />
        <p>还没有可生成的账簿</p>
        <span>选一个月份后会自动加载；如果该月没有已过账凭证，账簿只出科目余额表。</span>
      </div>
    </NSpin>

    <!-- 屏幕端快速导航（打印自动隐藏） -->
    <div v-if="book && !loading" class="book-nav no-print" :class="{ 'is-collapsed': navCollapsed }">
      <template v-if="!navCollapsed">
        <div class="book-nav-head">
          <span>账簿导航</span>
          <button class="book-nav-collapse" title="收起" @click="navCollapsed = true">
            <i class="ri-arrow-right-double-line" />
          </button>
        </div>
        <div v-for="item in navItems" :key="item.key" class="book-nav-group">
          <div
            class="book-nav-item"
            :class="{ active: activeSheetKey === item.key || item.children?.some(c => c.key === activeSheetKey) }"
            @click="onNavGroupClick(item)"
          >
            <span class="book-nav-label">
              <i
                v-if="item.children?.length"
                class="ri-arrow-right-s-line book-nav-chevron"
                :class="{ 'is-open': expandedNav[item.key] }"
                title="展开 / 收起"
                @click.stop="toggleNavExpand(item.key)"
              />
              {{ item.label }}
            </span>
            <i class="book-nav-page">p.{{ item.pageNo }}</i>
          </div>
          <div v-if="item.children?.length && expandedNav[item.key]" class="book-nav-sub">
            <div
              v-for="child in item.children"
              :key="child.key"
              class="book-nav-item is-sub"
              :class="{ active: activeSheetKey === child.key }"
              @click="jumpToSheet(child.key)"
            >
              <span class="book-nav-label">{{ child.label }}</span>
              <i class="book-nav-page">p.{{ child.pageNo }}</i>
            </div>
          </div>
        </div>
      </template>
      <button v-else class="book-nav-fab" title="账簿导航" @click="navCollapsed = false">
        <i class="ri-list-unordered" />
      </button>
    </div>

    <NModal v-model:show="diffDialog" preset="card" title="账簿差异比对" style="width: 720px;">
      <NSpin :show="diffLoading">
        <template v-if="diffData">
          <p class="diff-head">
            <b>{{ diffData.base.bookNo }}</b> → <b>{{ diffData.target.bookNo }}</b>
            <span v-if="diffData.hashEqual" class="diff-same">内容完全一致</span>
            <span v-else class="diff-changed">
              影响 {{ diffData.balance.length }} 个科目 · 新增 {{ diffData.vouchersAdded.length }} 张凭证
              <template v-if="diffData.pageCountDelta"> · 页数 {{ diffData.pageCountDelta > 0 ? '+' : '' }}{{ diffData.pageCountDelta }}</template>
            </span>
          </p>
          <table v-if="diffData.balance.length" class="diff-table">
            <thead>
              <tr>
                <th>科目编码</th>
                <th>科目名称</th>
                <th class="right">期初差额</th>
                <th class="right">借方差额</th>
                <th class="right">贷方差额</th>
                <th class="right">期末差额</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in diffData.balance" :key="row.accountCode">
                <td>{{ row.accountCode }}</td>
                <td>{{ row.accountName }}</td>
                <td class="right">{{ ledgerMoney(row.openingDelta) }}</td>
                <td class="right">{{ ledgerMoney(row.debitDelta) }}</td>
                <td class="right">{{ ledgerMoney(row.creditDelta) }}</td>
                <td class="right">{{ ledgerMoney(row.closingDelta) }}</td>
              </tr>
            </tbody>
          </table>
          <p v-else class="diff-empty">两册账簿的科目余额完全一致。</p>
          <div v-if="diffData.vouchersAdded.length" class="diff-vouchers">
            新增凭证：{{ diffData.vouchersAdded.slice(0, 8).join('、') }}
            <template v-if="diffData.vouchersAdded.length > 8"> 等 {{ diffData.vouchersAdded.length }} 张</template>
          </div>
          <div v-if="diffData.vouchersRemoved.length" class="diff-vouchers">
            消失凭证：{{ diffData.vouchersRemoved.slice(0, 8).join('、') }}
          </div>
        </template>
      </NSpin>
      <template #footer>
        <div class="void-footer"><NButton @click="diffDialog = false">关闭</NButton></div>
      </template>
    </NModal>

    <NModal v-model:show="voidDialog" preset="card" title="作废账簿" style="width: 440px;">
      <p class="void-hint">
        作废只改状态，账页内容与校验码永久保留。已打印装订的纸质册请随新册一并存档，
        并在备查簿登记作废原因。
      </p>
      <p class="void-target">{{ voidTarget?.bookNo }}</p>
      <NInput
        v-model:value="voidReason"
        type="textarea"
        :rows="3"
        placeholder="例如：科目用错，反结账后重新签发"
      />
      <template #footer>
        <div class="void-footer">
          <NButton @click="voidDialog = false">取消</NButton>
          <NButton type="warning" :loading="voiding" @click="confirmVoid">确认作废</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.book-page {
  --book-paper: #fff;
  --book-ink-strong: #1f2937;
  --book-ink: rgba(31, 41, 55, 0.82);
  --book-ink-soft: rgba(31, 41, 55, 0.56);
  --book-line: rgba(31, 41, 55, 0.32);
  --book-head: rgba(31, 41, 55, 0.06);
  --book-tint: rgba(31, 41, 55, 0.03);
  max-width: 940px;
  margin: 0 auto;
  color: var(--book-ink-strong);
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 12px;
  padding: 10px 16px;
  border: 1px solid rgba(31, 41, 55, 0.14);
  border-radius: 8px;
  background: var(--book-paper);
}
.toolbar-center { display: flex; align-items: center; gap: 10px; }
.toolbar-right { display: flex; gap: 8px; }

.scope-switch {
  display: inline-flex;
  padding: 3px;
  border-radius: 8px;
  background: rgba(31, 41, 55, 0.08);
}
.scope-switch button {
  min-width: 56px;
  padding: 4px 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--book-ink);
  cursor: pointer;
  font-size: 12px;
}
.scope-switch button.active {
  background: var(--book-paper);
  color: var(--book-ink-strong);
  font-weight: 500;
}

.issue-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
  padding: 10px 16px;
  border: 1px solid rgba(31, 41, 55, 0.14);
  border-radius: 8px;
  background: var(--book-paper);
}
.issue-state { display: flex; align-items: center; gap: 8px; font-size: 12px; }
.issue-state b { font-size: 13px; }
.state-dot { width: 8px; height: 8px; border-radius: 999px; }
.state-dot.issued { background: var(--solo-income); }
.state-dot.stale { background: var(--solo-warning); }
.state-dot.draft { background: rgba(31, 41, 55, 0.32); }
.state-hint { color: var(--book-ink-soft); }

.history {
  display: grid;
  gap: 4px;
  margin-bottom: 10px;
  padding: 10px 16px;
  border: 1px solid rgba(31, 41, 55, 0.14);
  border-radius: 8px;
  background: var(--book-paper);
  font-size: 12px;
}
.history-title { color: var(--book-ink-soft); }
.history-item { display: flex; align-items: center; gap: 12px; }
.history-item.void { color: var(--book-ink-soft); text-decoration: line-through; }
.history-item.void .history-void { text-decoration: none; }
.history-hash { color: var(--book-ink-soft); font-family: var(--solo-font-mono, monospace); }

.notice-bar {
  margin-bottom: 14px;
  padding: 10px 14px;
  border: 1px solid rgba(160, 92, 16, 0.32);
  border-radius: 8px;
  background: rgba(160, 92, 16, 0.08);
}
.notice-line {
  display: flex;
  align-items: center;
  gap: 6px;
  margin: 0;
  color: var(--solo-warning);
  font-size: 12px;
  line-height: 1.8;
}

.page-sheet {
  position: relative;
  margin-bottom: 20px;
  padding: 40px 44px 56px;
  border-radius: 12px;
  background: var(--book-paper);
}

.cover-sheet { padding-bottom: 40px; }
.book-cover {
  display: flex;
  min-height: 520px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}
.cover-company { font-size: 18px; letter-spacing: 2px; }
.cover-title { margin: 18px 0 10px; font-size: 34px; font-weight: 700; letter-spacing: 12px; }
.cover-period { color: var(--book-ink); font-size: 16px; }
.cover-meta { display: grid; gap: 10px; margin-top: 44px; font-size: 14px; }
.cover-meta span { display: inline-block; width: 96px; color: var(--book-ink-soft); }
.cover-foot { margin-top: 52px; color: var(--book-ink-soft); font-size: 12px; }

.section-head { display: flex; align-items: center; justify-content: center; gap: 16px; }
.section-title { margin: 0 0 6px; font-size: 18px; font-weight: 700; text-align: center; }
.section-meta { margin-bottom: 12px; color: var(--book-ink-soft); font-size: 12px; text-align: center; }
.zero-toggle {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  color: var(--book-ink-soft);
  cursor: pointer;
  font-size: 12px;
  font-weight: 400;
}

.toc { margin: 16px 0 0; padding-left: 8px; list-style: none; line-height: 2; }
.toc-ref { color: var(--book-ink-soft); font-size: 11.5px; }
.toc-ref i { font-style: normal; }

/* 屏幕端：目录条目可点击定位（打印件仍是普通目录） */
.toc li.toc-link { cursor: pointer; border-radius: 6px; padding: 0 6px; transition: background-color .15s ease; }
.toc li.toc-link:hover { background: var(--solo-bg-soft, #f3f5f8); }
.toc li.toc-link:hover span { color: var(--solo-primary); }

/* ── 屏幕端快速导航（打印自动隐藏） ── */
.book-nav {
  position: fixed;
  right: 14px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 60;
  width: 158px;
  max-height: 72vh;
  overflow-y: auto;
  background: #fff;
  border: 1px solid var(--solo-border-light);
  border-radius: 10px;
  box-shadow: var(--solo-shadow);
  padding: 8px;
  font-size: 12.5px;
}
.book-nav-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  font-weight: 650;
  color: var(--solo-text-secondary);
  padding: 2px 6px 6px;
}
.book-nav-collapse {
  border: 0;
  background: none;
  cursor: pointer;
  color: var(--solo-text-muted);
  font-size: 14px;
  line-height: 1;
  padding: 2px;
}
.book-nav-collapse:hover { color: var(--solo-text-primary); }
.book-nav-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 6px;
  padding: 4px 6px;
  border-radius: 6px;
  cursor: pointer;
  color: var(--solo-text-primary);
}
.book-nav-item:hover { background: var(--solo-bg-soft, #f3f5f8); }
.book-nav-item.active {
  background: var(--solo-primary-50, #f0f7ff);
  color: var(--solo-primary);
  font-weight: 600;
}
.book-nav-item.is-sub { padding-left: 12px; font-size: 12px; }
.book-nav-label {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.book-nav-chevron {
  display: inline-flex;
  font-size: 13px;
  transition: transform 0.15s ease;
}
.book-nav-chevron.is-open { transform: rotate(90deg); }
.book-nav-page {
  font-style: normal;
  color: var(--solo-text-muted);
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  flex-shrink: 0;
}
.book-nav-sub {
  margin: 2px 0 2px 12px;
  border-left: 1px solid var(--solo-border-light);
  padding-left: 4px;
}
.book-nav-fab {
  display: block;
  width: 34px;
  height: 34px;
  margin: 0 auto;
  border-radius: 50%;
  border: 1px solid var(--solo-border-light);
  background: #fff;
  box-shadow: var(--solo-shadow);
  cursor: pointer;
  color: var(--solo-primary);
  font-size: 16px;
}
.book-nav-fab:hover { color: var(--solo-primary-light, #2B66E2); }
/* 折叠态：容器收缩成只包住小圆钮的迷你卡片 */
.book-nav.is-collapsed {
  width: auto;
  padding: 4px;
  border-radius: 50%;
  box-shadow: var(--solo-shadow);
}
.book-nav.is-collapsed .book-nav-fab {
  width: 28px;
  height: 28px;
  border: 0;
  box-shadow: none;
  font-size: 14px;
}
/* 锚点定位让出顶部固定栏 */
.page-sheet[id] { scroll-margin-top: 64px; }

.compile-notes {
  display: grid;
  gap: 6px;
  margin-bottom: 36px;
  color: var(--book-ink);
  font-size: 12px;
}
.compile-notes ol { margin: 0; padding-left: 20px; line-height: 1.9; }
.toc li { display: flex; align-items: baseline; gap: 8px; }
.toc li span { flex: 1; }
.toc li i {
  color: var(--book-ink-soft);
  font-style: normal;
  font-variant-numeric: tabular-nums;
}

/* 行高直接决定一页能放多少行：padding 5px + 12px 字号实测约 38px/行，
   24 行就把 A4 顶溢出。压到 3px + 11.5px 后约 30px/行，配合后端 PAGE_SIZE
   才能让「一页 = 一个 page-sheet」成立。改这里必须同步改后端 PAGE_SIZE。 */
.book-table { width: 100%; border-collapse: collapse; font-size: 11.5px; }
.book-table th,
.book-table td {
  border: 1px solid var(--book-line);
  padding: 3px 6px;
  line-height: 1.5;
  vertical-align: top;
}
.book-table th { background: var(--book-head); font-weight: 600; }
.book-table thead th { text-align: left; }
.book-table tfoot td { font-weight: 700; }

.col-code { width: 96px; }
.col-date { width: 92px; }
.col-voucher { width: 104px; }
.col-cp { width: 148px; }
.col-qty { width: 66px; }
.col-price { width: 78px; }
.col-amount { width: 102px; text-align: right; }
.col-dir { width: 50px; text-align: center; }
.col-count { width: 74px; text-align: center; }
.col-month { width: 92px; }
.col-account { width: 188px; }

/* 摘要/科目名这类长文本若不限行，一句话能撑到四五行，整页行数就不可控、
   表格跨页、页码错位（实测序时账 18 行排到 1240px，超出 A4 可用的 1009px）。
   两行封顶也是账簿惯例——会计本来只看前两行，完整内容在 Excel 导出里。
   这里用固定高度而不是 -webkit-line-clamp：后者在 table-cell 内不可靠，
   实测完全不生效，行高照样暴涨。 */
.cell-clip {
  display: block;
  max-height: 35px;
  overflow: hidden;
  word-break: break-word;
}

/* 打印可用宽度 = A4 210 − 左右各 12 ≈ 703px，比屏幕窄一大截。沿用屏幕列宽时，
   明细账的固定列会把摘要列挤到几像素——每个字换一行，行高暴涨，表格跨页、
   页码错位（实测 20 页的册子打出 30 页）。这里为打印单独收窄列宽，把省下的
   宽度让给摘要。改这里必须同步复核后端 PAGE_SIZE。 */
/* 摘要/科目名这类长文本若不限行，一句话能撑到四五行，整页行数就不可控、
   表格跨页、页码错位（实测序时账 18 行排到 1240px，超出 A4 可用的 1009px）。
   两行封顶也是账簿惯例——会计本来只看前两行，完整内容在 Excel 导出里。
   这里用固定高度而不是 -webkit-line-clamp：后者在 table-cell 内不可靠，
   实测完全不生效，行高照样暴涨。 */
.cell-clip {
  display: block;
  max-height: 35px;
  overflow: hidden;
  word-break: break-word;
}

@media print {
  .col-code { width: 68px; }
  .col-date { width: 68px; }
  .col-voucher { width: 76px; }
  .col-cp { width: 118px; }
  .col-qty { width: 50px; }
  .col-price { width: 58px; }
  .col-amount { width: 82px; }
  .col-dir { width: 38px; }
  .col-count { width: 54px; }
  .col-month { width: 66px; }
  .col-account { width: 128px; }
  .book-table { table-layout: fixed; }
  .book-table th,
  .book-table td { word-break: break-word; }
  /* 日期、凭证号、金额、方向这类短文本永远不该换行：列宽在部分浏览器里
     不会严格生效，一旦换行就会把单行撑到 100px 以上（实测日期列 111px），
     整页行数随之失控、表格跨页、页码错位。宁可溢出被裁，也不让它换行。 */
  .col-code,
  .col-date,
  .col-voucher,
  .col-qty,
  .col-price,
  .col-amount,
  .col-dir,
  .col-count,
  .col-month,
  .col-account {
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
}

.amount { text-align: right; font-variant-numeric: tabular-nums; }
.center { text-align: center; }
.strong { font-weight: 700; }
.cp { color: var(--book-ink); font-size: 11.5px; }
.hint { color: var(--book-ink-soft); font-size: 11px; }
.badge {
  margin-left: 4px;
  padding: 0 4px;
  border: 1px solid rgba(160, 92, 16, 0.4);
  border-radius: 4px;
  color: var(--solo-warning);
  font-size: 10px;
}
.parent td { background: var(--book-tint); font-weight: 600; }
.opening-row td { background: var(--book-tint); color: var(--book-ink); }
.total-row td { background: var(--book-tint); }
.idle td { color: var(--book-ink-soft); }
.empty-cell { padding: 26px; color: var(--book-ink-soft); text-align: center; }
.footnote-cell { padding: 8px; color: var(--book-ink-soft); font-size: 12px; font-weight: 400; }

.page-footer {
  display: flex;
  justify-content: space-between;
  margin-top: 10px;
  color: var(--book-ink-soft);
  font-size: 11px;
}

.signoff { display: grid; gap: 14px; padding-top: 40px; }
.signoff-row { display: flex; flex-wrap: wrap; gap: 32px; color: var(--book-ink); font-size: 13px; }
.signoff-row.soft { color: var(--book-ink-soft); font-size: 11px; }
.signoff-item em { display: inline-block; width: 96px; border-bottom: 1px solid var(--book-line); }

.book-empty {
  display: flex;
  min-height: 320px;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  border-radius: 12px;
  background: var(--book-paper);
  color: var(--book-ink-soft);
}
.book-empty i { font-size: 34px; }
.book-empty p { margin: 0; color: var(--book-ink-strong); font-size: 15px; font-weight: 600; }
.book-empty span { font-size: 12px; }

.diff-head { display: flex; align-items: center; gap: 10px; margin: 0 0 12px; font-size: 13px; }
.diff-same { color: var(--solo-income); }
.diff-changed { color: var(--solo-warning); }
.diff-table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
.diff-table th,
.diff-table td { border: 1px solid var(--book-line); padding: 5px 8px; }
.diff-table th { background: var(--book-head); font-weight: 500; }
.diff-table .right { text-align: right; font-variant-numeric: tabular-nums; }
.diff-empty { margin: 0; padding: 18px; color: var(--book-ink-soft); text-align: center; }
.diff-vouchers { margin-top: 10px; color: var(--book-ink); font-size: 12px; }

.void-hint { margin: 0 0 8px; color: var(--book-ink-soft); font-size: 12px; line-height: 1.7; }
.void-target { margin: 0 0 10px; font-weight: 600; }
.void-footer { display: flex; justify-content: flex-end; gap: 8px; }

@page {
  size: A4;
  margin: 16mm 12mm 14mm;
}

@media print {
  .no-print { display: none !important; }
  .book-page { max-width: none; }
  .page-sheet {
    display: flex;
    /* A4 可用高度 = 297 − 16(上) − 14(下) = 267mm。页高一旦贴近这个值，
       body 边距或亚像素误差就会让整页被推到下一页（实测 4 页变 6 页）。
       PAGE_SIZE 决定了一页最多 24 行（≈ 552px），200mm（756px）足够容纳并留足余量。 */
    min-height: 200mm;
    flex-direction: column;
    justify-content: space-between;
    margin: 0;
    padding: 0;
    border-radius: 0;
    break-after: page;
    page-break-after: always;
  }
  .page-sheet:last-child { break-after: auto; page-break-after: auto; }
  .book-cover { min-height: auto; flex: 1; }
  .book-table th,
  .book-table td { border-color: var(--book-ink-strong); }
  .book-table thead { display: table-header-group; }
  .book-table tr { break-inside: avoid; page-break-inside: avoid; }
  .toc { flex: 1; }
}

@media (max-width: 760px) {
  .toolbar { align-items: stretch; flex-wrap: wrap; }
  .issue-bar { align-items: flex-start; flex-wrap: wrap; }
  .page-sheet { padding: 24px 18px 44px; }
  .cover-title { font-size: 26px; letter-spacing: 6px; }
}
</style>

<!-- 打印专用（刻意不加 scoped）：应用外壳把高度锁死在 100vh、内容放在内部滚动区，
     浏览器打印只输出第一屏，27 页账簿会打成 1 页。打印媒体下把外壳恢复成普通文档流。
     侧栏与导航组件已带 no-print 标记，这里只需清掉侧栏让出的左边距。 -->
<style>
@media print {
  body { margin: 0 !important; }
  .workspace-view-notice { display: none !important; }
  /* sheet 间距不参与打印分页：屏幕用的 margin-bottom: 20px（scoped 特异性更高，
     必须 !important 压掉）会让 1002px 的页 + 20px 超出 267mm 内容区，把整页推后、
     页码错位（实测年度册 74 vs 73）。改用显式断页，一 sheet 恒一页。 */
  .page-sheet {
    margin-bottom: 0 !important;
    page-break-after: always;
    break-after: page;
  }
  .app-layout,
  .main-area,
  .content,
  .n-spin-container,
  .n-spin-content {
    height: auto !important;
    min-height: 0 !important;
    max-height: none !important;
    overflow: visible !important;
  }
  .main-area { margin-left: 0 !important; }
  .content { padding: 0 !important; }
}
</style>
