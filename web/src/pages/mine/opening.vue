<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { accountingErrorText } from '@/utils/errors'
import {
  NButton, NTag, NSpace, useMessage, NInput, NSpin, NSelect, NModal, NFormItem,
  NAlert, NInputNumber, NSwitch, NPopconfirm, NPopover,
} from 'naive-ui'
import LegacyImportTab from './legacy-import.vue'
import TrialBalanceTable from '@/components/business/TrialBalanceTable.vue'
import FileDropzone from '@/components/business/FileDropzone.vue'
import * as XLSX from 'xlsx'
import apiClient from '@/api/client'
import { getAuthMe, requestStepUp, updateCompany } from '@/api/auth'
import { useAuthStore } from '@/stores/auth'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import { copyToClipboard } from '@/utils/clipboard'
import { reportErrorToast } from '@/utils/errorPresenter'
import { sendVerificationCode } from '@/utils/verificationCode'
import { SoloFieldHelp } from '@/components/ui'
import {
  buildAgencyRequestCopy,
  shouldShowOpeningBalanceRow,
  type AgencyRequestScene,
  type OpeningAccountScope,
} from '@/utils/openingView'
import {
  exportOpeningTemplate,
  parseOpeningWorkbook,
  isProfitLossCode,
  suggestMappingTarget,
  checkOpeningPeriod,
  describeExpectedPeriod,
  expectedOpeningPeriod,
  type FixedAssetImportItem,
  type OpeningPeriodCheck,
  type ParseOpeningResult,
  type StatementPeriod,
  type UnmatchedOpeningRow,
} from '@/utils/openingBalanceImport'
import { loadOpeningMapping, saveOpeningMapping, pickConfirmedMappings } from '@/utils/openingMapping'
import { readWorkbookFromFile } from '@/utils/workbookFile'
import { companyInception, openingCutoffOptions, resolveOpeningPeriod } from '@/utils/openingPeriod'
import { mergeOpeningRows, mergeYtdRows } from '@/utils/openingMerge'
import { buildOpeningReconciliation } from '@/utils/openingReconciliation'
import {
  normalizeTrialBalanceRows,
  type TrialBalanceColumns,
  type TrialBalanceRow,
} from '@/utils/trialBalance'

interface BalanceItem {
  id?: string
  company_id?: string
  account_code: string
  account_name?: string
  debit_balance: number
  credit_balance: number
  partner_name?: string
  /** 原文件科目编码（1122001 这类），仅展示核对用 */
  originalCode?: string
  fiscal_year?: number
  voucherStatus?: string | null
  voucherRef?: string | null
}

type CategoryFilter = 'all' | 'asset' | 'liability' | 'equity'
type YtdCategoryFilter = 'all' | 'revenue' | 'expense'
type ActiveSheetTab = 'balance' | 'ytd' | 'reconcile' | 'trial'
type StepUpMode = 'save' | 'reset'

const message = useMessage()
const authStore = useAuthStore()
const viewStore = useWorkspaceViewStore()

const loading = ref(false)
const saving = ref(false)
const manualEntryExpanded = ref(false)
const accountScope = ref<OpeningAccountScope>('common')
const items = ref<BalanceItem[]>([])
const ytdItems = ref<BalanceItem[]>([])
// 年初（1 月 1 日）余额快照：只随导入更新、随保存落库，供报表「年初余额」列使用
const yearStartItems = ref<{ account_code: string; account_name?: string; partner_name?: string; debit_balance: number; credit_balance: number }[]>([])
// 全 0 的年初快照是「已知的 0」（当年成立），不能因为数组为空就当成未知
const yearStartSnapshotKnown = ref(false)
const fixedAssets = ref<FixedAssetImportItem[]>([])
const fiscalYear = ref(new Date().getFullYear())
const startMonth = ref('01')
const voucherNo = ref<string | null>(null)
const serverLocked = ref(false)

// 旧账导入入口先隐藏（?tab=legacy 也不再切换），等真实导出格式补齐后再开放
const mainTab = ref<'opening' | 'legacy'>('opening')

// 不再监听 mainTab：切 Tab 的 watcher 会在 load() 之前把草稿叠到旧表上，
// 随后的 load() 又把整张表覆盖回服务端数据——「带入期初设置细调」等于没生效。
async function onLegacyImported() {
  mainTab.value = 'opening'
  await load()
  await checkPendingImportFromSession()
}

// 筛选与视图状态
const activeTab = ref<ActiveSheetTab>('balance')
const categoryFilter = ref<CategoryFilter>('all')
const ytdCategoryFilter = ref<YtdCategoryFilter>('all')
const searchQuery = ref('')
const onlyFilled = ref(false)

// 短信二次验证与冲销重置状态
const stepUpVisible = ref(false)
const stepUpMode = ref<StepUpMode>('save')
const resetReason = ref('期初余额核对重设')
const phone = ref('')
const code = ref('')
const bizId = ref('')
const sendingCode = ref(false)

// 只认 posted：冲销后的凭证是审计墓碑，页面必须解锁让用户重新导入
const isLocked = computed(() =>
  serverLocked.value || items.value.some(i => (i.voucherStatus || '') === 'posted')
)

const startPeriod = computed(() => `${fiscalYear.value}-${startMonth.value}`)
const startMonthNum = computed(() => parseInt(startMonth.value, 10) || 1)
const isMidYear = computed(() => startMonthNum.value > 1)

const currentCompany = computed(
  () => authStore.companies.find((item) => item.company_id === authStore.currentCompanyId) ?? null,
)
const isMigrationCompany = computed(() => currentCompany.value?.onboardingType === 'migration')
const isSmallScaleTaxpayer = computed(
  () => (currentCompany.value?.taxpayerType || '小规模纳税人') !== '一般纳税人',
)
const switchingToZeroStart = ref(false)

async function switchToZeroStart() {
  const cid = authStore.currentCompanyId
  if (!cid || switchingToZeroStart.value) return
  switchingToZeroStart.value = true
  try {
    await updateCompany(cid, {
      onboardingType: 'new',
      onboardingChangeReason: 'zero_opening_confirmed',
    })
    await authStore.fetchProfile()
    message.success('已转为从零起账，现在可以导入业务数据或记账')
  } catch (e: unknown) {
    reportErrorToast(message, e, '切换建账方式失败')
  } finally {
    switchingToZeroStart.value = false
  }
}

watch(startMonth, (m) => {
  if (m === '01' && activeTab.value !== 'balance') {
    activeTab.value = 'balance'
  }
})

// 支持挂接往来客商辅助核算的科目（1221 已容器化，往来类一律用末级子科目）
// 往来科目前缀白名单与后端 core/chart_policy.PARTNER_ACCOUNT_PREFIXES 同口径。
// 曾经这里是精确 Set.has 且列了 1121/2201（后端没有）、漏了 1221 前缀，
// 导致子级往来码（1122001）不显示客商输入框——两边漂移过。
const partnerAccountCodes = new Set([
  '1122', '1123', '2202', '2203', '1221', '2241',
])

// 贷方余额性质科目（备抵资产 + 负债 + 权益 + 收入类）
const creditNormalCodes = new Set([
  '1407', '1602', '1622', '1702',
  '5001', '5051', '5111', '5301', '5603005',
])

function isCreditNormal(code: string): boolean {
  if (creditNormalCodes.has(code)) return true
  if (code.startsWith('2') || code.startsWith('3') || code.startsWith('5301')) return true
  return false
}

function supportsPartner(code: string): boolean {
  const text = String(code || '').trim()
  if (!text) return false
  for (const prefix of partnerAccountCodes) {
    if (text.startsWith(prefix)) return true
  }
  return false
}

function isBalanceAccount(code: string): boolean {
  return !isProfitLossCode(code)
}

function getAccountCategory(code: string): CategoryFilter {
  if (code.startsWith('1') || code.startsWith('4')) return 'asset'
  if (code.startsWith('2')) return 'liability'
  if (code.startsWith('3')) return 'equity'
  return 'asset'
}

function getAccountCategoryLabel(code: string): string {
  if (code.startsWith('1')) return '资产'
  if (code.startsWith('2')) return '负债'
  if (code.startsWith('3')) return '权益'
  if (code.startsWith('4')) return '成本'
  if (isCreditNormal(code)) return '收入'
  return '费用'
}

const balanceItems = computed(() => items.value.filter(i => isBalanceAccount(i.account_code)))

const commonBalanceCount = computed(() =>
  balanceItems.value.filter((item) =>
    shouldShowOpeningBalanceRow(item, {
      scope: 'common',
      smallScaleTaxpayer: isSmallScaleTaxpayer.value,
      categoryResolver: getAccountCategory,
    }),
  ).length,
)

const filteredBalanceItems = computed(() => {
  return balanceItems.value.filter((item) =>
    shouldShowOpeningBalanceRow(item, {
      scope: accountScope.value,
      category: categoryFilter.value,
      onlyFilled: onlyFilled.value,
      query: searchQuery.value,
      smallScaleTaxpayer: isSmallScaleTaxpayer.value,
      categoryResolver: getAccountCategory,
    }),
  )
})

const filteredYtdItems = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  return ytdItems.value.filter(item => {
    const isRev = isCreditNormal(item.account_code)
    if (ytdCategoryFilter.value === 'revenue' && !isRev) return false
    if (ytdCategoryFilter.value === 'expense' && isRev) return false
    if (onlyFilled.value && !(item.debit_balance > 0 || item.credit_balance > 0)) {
      return false
    }
    if (q) {
      const codeMatch = item.account_code.toLowerCase().includes(q)
      const nameMatch = (item.account_name || '').toLowerCase().includes(q)
      if (!codeMatch && !nameMatch) return false
    }
    return true
  })
})

const filledBalanceCount = computed(() =>
  balanceItems.value.filter(i => (i.debit_balance || 0) > 0 || (i.credit_balance || 0) > 0).length
)
const filledYtdCount = computed(() =>
  ytdItems.value.filter(i => (i.debit_balance || 0) > 0 || (i.credit_balance || 0) > 0).length
)
const isBlankState = computed(
  () =>
    !loading.value &&
    !isLocked.value &&
    filledBalanceCount.value === 0 &&
    filledYtdCount.value === 0 &&
    !manualEntryExpanded.value,
)

const totalDebit = computed(() =>
  Math.round(balanceItems.value.reduce((s, i) => s + (Number(i.debit_balance) || 0), 0) * 100) / 100
)
const totalCredit = computed(() =>
  Math.round(balanceItems.value.reduce((s, i) => s + (Number(i.credit_balance) || 0), 0) * 100) / 100
)
const balanceDiff = computed(() =>
  Math.round((totalDebit.value - totalCredit.value) * 100) / 100
)
const isBalanced = computed(() =>
  Math.abs(balanceDiff.value) <= 0.01 && totalDebit.value > 0
)

const ytdTotalDebit = computed(() =>
  Math.round(ytdItems.value.reduce((s, i) => s + (Number(i.debit_balance) || 0), 0) * 100) / 100
)
const ytdTotalCredit = computed(() =>
  Math.round(ytdItems.value.reduce((s, i) => s + (Number(i.credit_balance) || 0), 0) * 100) / 100
)
// 累计净利润 = 累计贷方(收入) - 累计借方(成本费用)
const ytdNetProfit = computed(() =>
  Math.round((ytdTotalCredit.value - ytdTotalDebit.value) * 100) / 100
)

// 期初对账（只读）：年初快照 ↔ 期初余额。资产负债类的本年累计不落库，
// 净变动按期初 − 年初推算；年初快照缺失时不能把 0 当成真实年初。
const yearStartKnown = computed(() =>
  !isMidYear.value || yearStartSnapshotKnown.value || yearStartItems.value.length > 0,
)
const reconciliation = computed(() =>
  buildOpeningReconciliation(items.value, yearStartItems.value, {
    yearStartKnown: yearStartKnown.value,
  })
)
const filteredReconciliationRows = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return reconciliation.value.rows
  return reconciliation.value.rows.filter(row =>
    row.account_code.toLowerCase().includes(q) ||
    row.account_name.toLowerCase().includes(q))
})
function amountOrDash(val: number): string {
  return Math.abs(Number(val) || 0) < 0.005 ? '—' : formatCurrency(Number(val))
}

// 固定资产卡片对账监控
const fixedAssetCardCostTotal = computed(() =>
  Math.round(fixedAssets.value.reduce((s, a) => s + (Number(a.cost) || 0), 0) * 100) / 100
)
const fixedAssetCardDeprTotal = computed(() =>
  Math.round(fixedAssets.value.reduce((s, a) => s + (Number(a.accumulated_depreciation) || 0), 0) * 100) / 100
)
const acct1601Debit = computed(() => {
  const it = balanceItems.value.find(i => i.account_code === '1601')
  return Number(it?.debit_balance) || 0
})
const acct1602Credit = computed(() => {
  const it = balanceItems.value.find(i => i.account_code === '1602')
  return Number(it?.credit_balance) || 0
})
const fixedAssetCostMatched = computed(() =>
  acct1601Debit.value === 0 || Math.abs(fixedAssetCardCostTotal.value - acct1601Debit.value) <= 0.05
)
const fixedAssetDeprMatched = computed(() =>
  acct1602Credit.value === 0 || Math.abs(fixedAssetCardDeprTotal.value - acct1602Credit.value) <= 0.05
)
const hasFixedAssetNotice = computed(() =>
  fixedAssets.value.length > 0 || acct1601Debit.value > 0
)

function formatCurrency(val: number): string {
  return (Number(val) || 0).toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
}

// 资产负债科目同行借贷互斥更新
function handleBalanceDebitChange(item: BalanceItem, val: number | null) {
  const num = val && val > 0 ? Math.round(val * 100) / 100 : 0
  item.debit_balance = num
  if (num > 0 && item.credit_balance > 0) {
    item.credit_balance = 0
  }
}

function handleBalanceCreditChange(item: BalanceItem, val: number | null) {
  const num = val && val > 0 ? Math.round(val * 100) / 100 : 0
  item.credit_balance = num
  if (num > 0 && item.debit_balance > 0) {
    item.debit_balance = 0
  }
}

function handleYtdDebitChange(item: BalanceItem, val: number | null) {
  const num = val && val > 0 ? Math.round(val * 100) / 100 : 0
  item.debit_balance = num
  if (num > 0 && item.credit_balance > 0) {
    item.credit_balance = 0
  }
}

function handleYtdCreditChange(item: BalanceItem, val: number | null) {
  const num = val && val > 0 ? Math.round(val * 100) / 100 : 0
  item.credit_balance = num
  if (num > 0 && item.debit_balance > 0) {
    item.debit_balance = 0
  }
}

// Excel 导入与解析
const fileInputRef = ref<HTMLInputElement | null>(null)
const importModalVisible = ref(false)
const lastWorkbook = ref<XLSX.WorkBook | null>(null)
const unmatchedRows = ref<UnmatchedOpeningRow[]>([])
const mappingOverrides = ref<Record<string, string>>({})
// 导入表期间校验：期间不符时不写入表格，先在弹窗里让用户确认或重导
const statementPeriod = ref<StatementPeriod | null>(null)
const periodCheck = ref<OpeningPeriodCheck | null>(null)
const cutoffMonth = ref('')
// 通过期间校验的导入绑定到的建账年月：保存前再比一次，建账月被改过就要求重导，
// 否则会拿「截至 5 月」的表按 7 月建账保存，6 月损益静默漏记。
const appliedPeriod = ref<{ fiscalYear: number; startMonth: string } | null>(null)

const expectedPeriod = computed(() =>
  expectedOpeningPeriod(
    fiscalYear.value,
    startMonth.value,
    companyStartDate(),
    companyIncorporationDate(),
  ),
)
const expectedPeriodLabel = computed(() => describeExpectedPeriod(expectedPeriod.value))
const agencyRequestScene = ref<AgencyRequestScene>('check')
const agencyRequestText = computed(() =>
  buildAgencyRequestCopy(expectedPeriodLabel.value, currentCompany.value?.name, agencyRequestScene.value),
)

async function copyAgencyRequest() {
  const ok = await copyToClipboard(agencyRequestText.value)
  if (ok) {
    message.success('已复制发给原代账的要表话术，可直接粘贴到微信')
  } else {
    message.warning('复制失败，请手动选中话术复制')
  }
}
const zeroOpening = computed(() => expectedPeriod.value.zeroOpening)
const hasIncorporationDate = computed(() => Boolean(companyIncorporationDate()))
// 成立日期优先；没填时退回启用日期，文案必须说成「启用」，不能冒充「成立」。
const inceptionDate = computed(() => companyIncorporationDate() || companyStartDate())
const inception = computed(() => companyInception(inceptionDate.value))
const inceptionLabel = computed(() =>
  inception.value
    ? `${inception.value.year}-${String(inception.value.month).padStart(2, '0')}`
    : '',
)
const zeroOpeningHint = computed(() => {
  if (hasIncorporationDate.value) {
    return `公司 ${inceptionLabel.value} 成立，${startMonthNum.value} 月启用无需期初余额；直接录入业务即可。若要迁入成立至上一月的历史账，请把启用月改到这些月份之后。`
  }
  const book = inceptionLabel.value ? `账套 ${inceptionLabel.value} 启用` : '账套未填启用日期'
  return `${book}，${startMonthNum.value} 月启用无需期初余额；直接录入业务即可。若公司成立早于启用月，请先到「主体管理」补填公司成立日期。`
})
// 年中建账 YTD 的起点：公司成立月与启用前一月取较早者
const ytdStartMonth = computed(() =>
  isMidYear.value
    ? Math.max(1, Math.min(expectedPeriod.value.startMonth, startMonthNum.value - 1))
    : 1,
)

function defaultCutoffMonth(): string {
  if (expectedPeriod.value.zeroOpening) return ''
  return `${expectedPeriod.value.endYear}-${String(expectedPeriod.value.endMonth).padStart(2, '0')}`
}

/** 兜底下拉的选项：只列能通过期间校验的月份 */
const cutoffOptions = computed(() =>
  openingCutoffOptions(expectedPeriod.value).map(value => ({ label: value, value })),
)

// 未匹配的资产负债科目让「文件本身是平的」变成「页面不平」。
// 差额能被它们解释时必须说清，否则用户会以为导入的数据有问题。
const unmatchedBalanceImpact = computed(() => {
  const rows = unmatchedRows.value.filter(r => r.affects_balance !== false)
  const debit = rows.reduce((s, r) => s + (Number(r.debit) || 0), 0)
  const credit = rows.reduce((s, r) => s + (Number(r.credit) || 0), 0)
  return { count: rows.length, net: Math.round((credit - debit) * 100) / 100 }
})
const diffExplainedByUnmatched = computed(
  () =>
    !isBalanced.value &&
    unmatchedBalanceImpact.value.count > 0 &&
    Math.abs(balanceDiff.value - unmatchedBalanceImpact.value.net) <= 0.01,
)

const leafAccountOptions = computed(() => {
  const all = [...items.value, ...ytdItems.value]
  return all.map(a => ({
    label: `${a.account_code} ${a.account_name || ''}`,
    value: a.account_code,
  }))
})

const importModalTab = ref<'mapping' | 'preview'>('mapping')
const suggestedMappingKeys = ref<Record<string, string>>({})
const highlightedMappingKey = ref('')

function mappingKeyOf(row: UnmatchedOpeningRow): string {
  return row.originalCode || row.originalName
}

const unmatchedBalanceRows = computed(() =>
  unmatchedRows.value.filter(r => r.affects_balance !== false),
)
const unmatchedYtdRows = computed(() =>
  unmatchedRows.value.filter(r => r.affects_balance === false),
)

const unmatchedMappingGroups = computed(() => {
  const groups: Array<{ key: 'balance' | 'ytd'; title: string; rows: UnmatchedOpeningRow[] }> = []
  if (unmatchedBalanceRows.value.length > 0) {
    groups.push({
      key: 'balance',
      title: `影响期初试算平衡（资产 / 负债 / 权益 · ${unmatchedBalanceRows.value.length} 项）`,
      rows: unmatchedBalanceRows.value,
    })
  }
  if (unmatchedYtdRows.value.length > 0) {
    groups.push({
      key: 'ytd',
      title: `仅影响本年累计发生额（损益类 · ${unmatchedYtdRows.value.length} 项，不影响期初平衡）`,
      rows: unmatchedYtdRows.value,
    })
  }
  return groups
})

const mappedBalanceSelectedCount = computed(() =>
  unmatchedBalanceRows.value.filter(row => Boolean(mappingOverrides.value[mappingKeyOf(row)])).length,
)

const projectedDiffAfterMapping = computed(() => {
  if (!importSummary.value) return 0
  let net = importSummary.value.totalDebit - importSummary.value.totalCredit
  for (const row of unmatchedBalanceRows.value) {
    const targetCode = mappingOverrides.value[mappingKeyOf(row)]
    if (targetCode && !isProfitLossCode(targetCode)) {
      net += (Number(row.debit) || 0) - (Number(row.credit) || 0)
    }
  }
  return Math.round(net * 100) / 100
})

const nonDuplicateImportWarnings = computed(() =>
  (importSummary.value?.warnings || []).filter(
    w => !w.startsWith('未匹配到系统科目：') && !w.startsWith('已跳过 '),
  ),
)

function jumpToUnmatchedMapping(row: TrialBalanceRow) {
  highlightedMappingKey.value = row.original_code || row.original_name
  importModalTab.value = 'mapping'
}

function mappingSourceBadge(row: UnmatchedOpeningRow): {
  label: string
  tone: 'warning' | 'info' | 'success'
} {
  const key = mappingKeyOf(row)
  const selected = mappingOverrides.value[key]
  if (!selected) return { label: '需手选', tone: 'warning' }
  if (selected === suggestedMappingKeys.value[key]) return { label: '系统建议', tone: 'info' }
  return { label: '已选定', tone: 'success' }
}

const importSummary = ref<{
  matchedCount: number
  unmatchedCount: number
  warnings: string[]
  totalDebit: number
  totalCredit: number
  isBalanced: boolean
  ytdMatchedCount: number
  fixedAssetCount: number
} | null>(null)

// 导入原表快照：预览弹窗立即展示；保存时写入期初凭证 metadata，工作台页签读回。
const importPreviewRows = ref<TrialBalanceRow[]>([])
const importColumns = ref<TrialBalanceColumns | null>(null)
const savedTrialBalanceRows = ref<TrialBalanceRow[]>([])
const savedTrialBalanceColumns = ref<TrialBalanceColumns | null>(null)
const activeTrialBalanceRows = computed(() =>
  importPreviewRows.value.length > 0 ? importPreviewRows.value : savedTrialBalanceRows.value,
)
const activeTrialBalanceColumns = computed(() =>
  importPreviewRows.value.length > 0 ? importColumns.value : savedTrialBalanceColumns.value,
)

function triggerUpload() {
  if (isLocked.value) {
    message.warning('期初凭证已过账锁定；如需重新导入，请先点击「冲销并重设期初」')
    return
  }
  fileInputRef.value?.click()
}

function handleDownloadTemplate() {
  const allAccounts = [...balanceItems.value, ...ytdItems.value]
  exportOpeningTemplate(allAccounts, fiscalYear.value, startPeriod.value)
  message.success('已下载期初科目余额导入模板')
}

function applyImportResult(parseResult: any) {
  // 1. 写入资产负债期初明细（合并规则见 utils/openingMerge：
  // 带客商的行按「科目+客商」拆开，账龄才能按往来单位分开）
  items.value = mergeOpeningRows(items.value, parseResult.items || [])

  // 2. 写入年中损益本年累计发生额
  const ytd = mergeYtdRows(ytdItems.value, parseResult.ytdItems || [])
  ytdItems.value = ytd.rows
  const ytdMatched = ytd.matchedCount

  // 3. 写入固定资产期初卡片
  if (parseResult.fixedAssets && parseResult.fixedAssets.length > 0) {
    fixedAssets.value = parseResult.fixedAssets
  }

  // 4. 年初余额快照：解析不出来就清空。宁可报表年初列留空（导出会被拦下），
  // 也不能沿用上一次导入的年初数——那份数对应的是另一张表。
  yearStartItems.value = Array.isArray(parseResult.yearStartItems) ? parseResult.yearStartItems : []
  // derived/explicit 都说明「年初数可以确定」；全 0 时数组为空，但 0 是有效结论
  yearStartSnapshotKnown.value = Boolean(parseResult.yearStartSource)

  unmatchedRows.value = parseResult.unmatchedRows || []
  importPreviewRows.value = parseResult.previewRows || []
  importColumns.value = parseResult.columns || null
  importModalTab.value = unmatchedRows.value.length > 0 ? 'mapping' : 'preview'
  highlightedMappingKey.value = ''

  importSummary.value = {
    matchedCount: parseResult.matchedCount,
    unmatchedCount: parseResult.unmatchedCount,
    warnings: parseResult.warnings,
    totalDebit: parseResult.totalDebit,
    totalCredit: parseResult.totalCredit,
    isBalanced: parseResult.isBalanced,
    ytdMatchedCount: ytdMatched,
    fixedAssetCount: parseResult.fixedAssets?.length || 0,
  }
  if ((parseResult.matchedCount || 0) > 0) {
    onlyFilled.value = true
  }
}

/** 解析器只在这个起期下把「本期发生额」当本年累计，重解析必须带同一口径 */
function parsePeriodOptions() {
  const expected = expectedPeriod.value
  return {
    expectedStart: expected.zeroOpening
      ? null
      : { year: expected.startYear, month: expected.startMonth },
  }
}

function reapplyMapping() {
  if (!lastWorkbook.value) return
  loading.value = true
  try {
    const allAccounts = [...balanceItems.value, ...ytdItems.value]
    const parseResult = parseOpeningWorkbook(
      lastWorkbook.value,
      allAccounts,
      mappingOverrides.value,
      parsePeriodOptions(),
    )
    applyImportResult(parseResult)
    // 用户确认过的映射按账套持久化，下次导入同格式文件自动套用
    saveOpeningMapping(authStore.currentCompanyId, { ...mappingOverrides.value })
    if (parseResult.isBalanced) {
      message.success(`映射已更新，成功匹配 ${parseResult.matchedCount} 个科目，借贷双方已平衡`)
    } else {
      message.warning(`映射已更新，尚有借贷差额 ¥${Math.abs(parseResult.totalDebit - parseResult.totalCredit).toFixed(2)}，请核对调整`)
    }
  } catch (err) {
    message.error(accountingErrorText(err))
  } finally {
    loading.value = false
  }
}

// 映射预填：按账套持久化的历史映射优先，其余按名称关键词给建议（确认后才生效）
function buildPrefilledMappings(
  parseResult: ParseOpeningResult,
  allAccounts: { account_code: string; account_name?: string }[],
): Record<string, string> {
  const persisted = loadOpeningMapping(authStore.currentCompanyId)
  const prefilled: Record<string, string> = {}
  const suggestions: Record<string, string> = {}
  for (const row of parseResult.unmatchedRows || []) {
    const key = row.originalCode || row.originalName
    if (!key) continue
    // 读的键要与写的一致：saveOpeningMapping 存的是 key(=originalCode||originalName)，
    // 此前读 persisted[row.originalCode]，originalCode 为空的行永远读不回历史映射。
    const saved = persisted[key]
    if (saved) {
      prefilled[key] = saved
      continue
    }
    const direction = row.debit > 0 ? 'debit' : row.credit > 0 ? 'credit' : undefined
    const suggestion = suggestMappingTarget(row.originalCode, row.originalName, allAccounts, direction)
    if (suggestion) {
      prefilled[key] = suggestion
      suggestions[key] = suggestion
    }
  }
  suggestedMappingKeys.value = suggestions
  return prefilled
}

/** 解析结果已通过期间校验后，预填映射并写入表格 */
function applyWorkbook(workbook: XLSX.WorkBook, parsed?: ParseOpeningResult) {
  const allAccounts = [...balanceItems.value, ...ytdItems.value]
  const options = parsePeriodOptions()
  const parseResult = parsed ?? parseOpeningWorkbook(workbook, allAccounts, undefined, options)
  mappingOverrides.value = buildPrefilledMappings(parseResult, allAccounts)
  // 上次已确认的映射直接套用；按名称猜的建议值不自动生效，仍留在映射面板等用户确认
  const confirmed = pickConfirmedMappings(
    mappingOverrides.value,
    loadOpeningMapping(authStore.currentCompanyId),
  )
  const effective =
    Object.keys(confirmed).length > 0
      ? parseOpeningWorkbook(workbook, allAccounts, confirmed, options)
      : parseResult
  applyImportResult(effective)
  appliedPeriod.value = { fiscalYear: fiscalYear.value, startMonth: startMonth.value }
  importModalVisible.value = true

  if (effective.isBalanced) {
    message.success(`成功导入 ${effective.matchedCount} 个科目，借贷双方已平衡`)
  } else {
    message.warning(`导入完成，尚有借贷差额 ¥${Math.abs(effective.totalDebit - effective.totalCredit).toFixed(2)}，请核对调整`)
  }
}

async function processOpeningFile(file: File) {
  importSummary.value = null
  importPreviewRows.value = []
  importColumns.value = null
  loading.value = true
  try {
    const workbook = await readWorkbookFromFile(file)
    lastWorkbook.value = workbook
    const allAccounts = [...balanceItems.value, ...ytdItems.value]
    const parseResult = parseOpeningWorkbook(workbook, allAccounts, undefined, parsePeriodOptions())
    const period = parseResult.period ?? null
    statementPeriod.value = period
    cutoffMonth.value = defaultCutoffMonth()
    const check = checkOpeningPeriod(
      period,
      fiscalYear.value,
      startMonth.value,
      companyStartDate(),
      companyIncorporationDate(),
    )
    periodCheck.value = check

    if (!check.ok) {
      // 期间不符不写表格：写进去的是一屏「看起来已经导好」的错数。
      importSummary.value = null
      unmatchedRows.value = []
      importPreviewRows.value = []
      importColumns.value = null
      importModalVisible.value = true
      message.warning('这份科目余额表的期间与账套建账期间不一致，请先确认期间')
      return
    }

    applyWorkbook(workbook, parseResult)
  } catch (err) {
    message.error(accountingErrorText(err))
  } finally {
    loading.value = false
  }
}

async function onFileSelected(e: Event) {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  target.value = ''
  if (!file) return
  await processOpeningFile(file)
}

/** 兜底：表头识别不到期间时，用户手选截止月后重新校验并应用 */
function applyCutoffSelection() {
  // 表头已识别出期间就不允许手选覆盖：1-9 月的表选成 7 月会让错月份的数据通过
  if (!lastWorkbook.value || !cutoffMonth.value || statementPeriod.value) return
  const expected = expectedPeriod.value
  if (expected.zeroOpening) return
  const [year, month] = cutoffMonth.value.split('-').map(Number)
  const assumed: StatementPeriod = {
    startYear: expected.startYear,
    startMonth: expected.startMonth,
    endYear: year,
    endMonth: month,
    label: `手动选择截止 ${cutoffMonth.value}`,
  }
  const check = checkOpeningPeriod(
    assumed,
    fiscalYear.value,
    startMonth.value,
    companyStartDate(),
    companyIncorporationDate(),
  )
  periodCheck.value = check
  if (!check.ok) return
  statementPeriod.value = assumed
  applyWorkbook(lastWorkbook.value)
}

function normalizeRowNumbers(rawList: any[]): BalanceItem[] {
  return (rawList || []).map((row: any) => ({
    ...row,
    debit_balance: parseFloat(String(row.debit_balance ?? 0)) || 0,
    credit_balance: parseFloat(String(row.credit_balance ?? 0)) || 0,
    partner_name: String(row.partner_name || ''),
  }))
}

function pendingRows(list: any[]): Array<{
  account_code: string
  account_name: string
  debit_balance: number
  credit_balance: number
  partner_name: string
}> {
  return (list || []).map((i: any) => ({
    account_code: String(i.accountCode || i.account_code || ''),
    account_name: String(i.accountName || i.account_name || ''),
    debit_balance: parseFloat(String(i.debitBalance ?? i.debit_balance ?? 0)) || 0,
    credit_balance: parseFloat(String(i.creditBalance ?? i.credit_balance ?? 0)) || 0,
    partner_name: String(i.partnerName || i.partner_name || ''),
  }))
}

async function checkPendingImportFromSession() {
  try {
    const raw = sessionStorage.getItem('solobooks:pending_opening_import')
    if (!raw) return
    sessionStorage.removeItem('solobooks:pending_opening_import')
    const parsed = JSON.parse(raw)
    if (!parsed || !Array.isArray(parsed.items)) return
    // 草稿属于另一个年度时先切年度重拉，否则会叠加到上一年的表上
    if (
      parsed.fiscalYear &&
      typeof parsed.fiscalYear === 'number' &&
      parsed.fiscalYear !== fiscalYear.value
    ) {
      fiscalYear.value = parsed.fiscalYear
      await load()
    }
    if (parsed.startPeriod && typeof parsed.startPeriod === 'string' && parsed.startPeriod.includes('-')) {
      const parts = parsed.startPeriod.split('-')
      if (parts[1]) startMonth.value = parts[1].padStart(2, '0')
    }
    // 与 Excel 导入共用合并规则：同科目多客商各自成行，按科目 Map 会只留最后一条。
    items.value = mergeOpeningRows(items.value, pendingRows(parsed.items))
    if (Array.isArray(parsed.ytdItems)) {
      ytdItems.value = mergeYtdRows(ytdItems.value, pendingRows(parsed.ytdItems)).rows
    }
    if (parsed.items.length > 0) {
      onlyFilled.value = true
    }
    message.success('已载入从旧账导入带入的期初余额，请核对并试算平衡')
  } catch (e) {
    console.error('Failed to load pending opening import', e)
  }
}

async function load() {
  appliedPeriod.value = null
  loading.value = true
  try {
    const data: any = await apiClient.get('/api/opening/balances', {
      params: { fiscal_year: fiscalYear.value },
    })
    items.value = normalizeRowNumbers(data.items || [])
    ytdItems.value = normalizeRowNumbers(data.ytd_items || [])
    yearStartItems.value = normalizeRowNumbers(data.year_start_items || [])
    yearStartSnapshotKnown.value = Boolean(data.year_start_known)

    // 已保存的原表快照：工作台页签展示；同时覆盖内存中的上次导入预览，
    // 防止冲销重设后未重新导入就保存时，把上一份旧快照再写进新凭证。
    savedTrialBalanceRows.value = normalizeTrialBalanceRows(data.trial_balance_items)
    savedTrialBalanceColumns.value =
      data.trial_balance_columns && typeof data.trial_balance_columns === 'object'
        ? (data.trial_balance_columns as TrialBalanceColumns)
        : null
    importPreviewRows.value = savedTrialBalanceRows.value
    importColumns.value = savedTrialBalanceColumns.value
    serverLocked.value = Boolean(data.is_locked)
    onlyFilled.value =
      serverLocked.value
      && items.value.some(i => (i.debit_balance || 0) > 0 || (i.credit_balance || 0) > 0)
    voucherNo.value = data.voucher_no || items.value.find(i => i.voucherRef)?.voucherRef || null

    // 已保存的建账时间优先；没保存过就跟公司信息里的启用日期对齐，不要一律落到 1 月。
    // start_period 在没保存时是后端兜底的 "{year}-01"，不能当作用户选择。
    const savedPeriod = data.start_period_saved ? data.start_period : null
    startMonth.value = resolveOpeningPeriod(savedPeriod, companyStartDate(), fiscalYear.value).month

    if (Array.isArray(data.fixed_assets)) {
      fixedAssets.value = data.fixed_assets.map((fa: any) => ({
        name: String(fa.name || '固定资产'),
        category: String(fa.category || '电子设备'),
        purchase_date: fa.purchaseDate || fa.purchase_date,
        cost: Number(fa.cost || fa.originalValue) || 0,
        accumulated_depreciation: Number(fa.accumulatedDepreciation || fa.accumulated_depreciation) || 0,
        useful_months: Number(fa.usefulMonths || fa.useful_months) || 36,
        salvage_rate: Number(fa.salvageRate || fa.salvage_rate) || 0.05,
      }))
    } else {
      fixedAssets.value = []
    }
  } catch {
    message.error('加载期初余额失败')
  } finally {
    loading.value = false
  }
}

function openSaveModal() {
  if (!isBalanced.value) {
    message.warning('资产负债期初借方合计与贷方合计不相等（或金额为 0），请先配平后再提交')
    return
  }
  if (isLocked.value) {
    message.warning('期初凭证已过账锁定；如需调整，请先点击「冲销并重设期初」')
    return
  }
  if (
    appliedPeriod.value &&
    (appliedPeriod.value.fiscalYear !== fiscalYear.value ||
      appliedPeriod.value.startMonth !== startMonth.value)
  ) {
    message.warning('建账年月已变更，这份余额表的期间不再匹配，请重新导入余额表后再生成期初凭证')
    return
  }
  stepUpMode.value = 'save'
  stepUpVisible.value = true
}

function openResetModal() {
  stepUpMode.value = 'reset'
  stepUpVisible.value = true
}

async function sendCode() {
  sendingCode.value = true
  try {
    const outcome = await sendVerificationCode(
      message,
      phone.value,
      '请输入当前账号已绑定的手机号',
    )
    if (outcome.ok) bizId.value = outcome.bizId
  } finally {
    sendingCode.value = false
  }
}

async function confirmStepUpAction() {
  if (!phone.value || !code.value) {
    message.warning('请输入手机号和短信验证码')
    return
  }
  saving.value = true
  try {
    const auth = await requestStepUp({
      action: 'adjust_opening_balance',
      phone: phone.value,
      code: code.value,
      bizId: bizId.value || undefined,
    })

    if (stepUpMode.value === 'reset') {
      await apiClient.post(
        '/api/opening/reset',
        {
          fiscal_year: fiscalYear.value,
          reason: resetReason.value.trim() || '期初数据冲销重置',
        },
        {
          headers: {
            'Step-Up-Token': auth.stepUpToken,
          },
        }
      )
      stepUpVisible.value = false
      code.value = ''
      message.success('原期初凭证已红字冲销，现在可以重新编辑或导入期初余额')
      await load()
      await authStore.fetchProfile().catch(() => {})
      return
    }

    await apiClient.put(
      '/api/opening/balances',
      {
        fiscal_year: fiscalYear.value,
        start_period: startPeriod.value,
        items: balanceItems.value.map(i => ({
          account_code: i.account_code,
          account_name: i.account_name,
          debit_balance: Number(i.debit_balance) || 0,
          credit_balance: Number(i.credit_balance) || 0,
          partner_name: i.partner_name?.trim() || undefined,
        })),
        ytd_items: isMidYear.value
          ? ytdItems.value.map(yi => ({
              account_code: yi.account_code,
              account_name: yi.account_name,
              debit_balance: Number(yi.debit_balance) || 0,
              credit_balance: Number(yi.credit_balance) || 0,
            }))
          : undefined,
        // 年初快照随期初凭证一起存，报表「年初余额」列用它（年中建账时
        // 期初余额 ≠ 年初余额，不能混用）
        year_start_items: yearStartItems.value.length
          ? yearStartItems.value.map(yi => ({
              account_code: yi.account_code,
              account_name: yi.account_name,
              partner_name: yi.partner_name || '',
              debit_balance: Number(yi.debit_balance) || 0,
              credit_balance: Number(yi.credit_balance) || 0,
            }))
          : undefined,
        // 全 0 的年初快照也要告诉后端「这是已知的 0」，否则报表会把当年成立当成无从得知
        year_start_known: yearStartSnapshotKnown.value || undefined,
        // 原表快照只用于事后核对，不参与分录；没有导入原表时不必写空数组
        trial_balance_items: importPreviewRows.value.length
          ? importPreviewRows.value
          : undefined,
        trial_balance_columns: importColumns.value || undefined,
        fixed_assets: fixedAssets.value.length > 0 ? fixedAssets.value : undefined,
      },
      {
        headers: {
          'Step-Up-Token': auth.stepUpToken,
          'Idempotency-Key': `opening-${fiscalYear.value}-${startMonth.value}-${Date.now()}`,
        },
      }
    )
    stepUpVisible.value = false
    code.value = ''
    message.success('期初凭证已生成并过账锁定')
    await load()
    await authStore.fetchProfile().catch(() => {})
  } catch (e: unknown) {
    reportErrorToast(message, e, '操作失败，请稍后重试')
  } finally {
    saving.value = false
  }
}

const currentYear = new Date().getFullYear()
const yearOptions = [
  { label: `${currentYear - 2} 会计年度`, value: currentYear - 2 },
  { label: `${currentYear - 1} 会计年度`, value: currentYear - 1 },
  { label: `${currentYear} 会计年度`, value: currentYear },
  { label: `${currentYear + 1} 会计年度`, value: currentYear + 1 },
]

const monthOptions = Array.from({ length: 12 }, (_, idx) => {
  const m = String(idx + 1).padStart(2, '0')
  return {
    label: idx === 0 ? '1 月启用（年初建账）' : `${idx + 1} 月启用（年中迁入）`,
    value: m,
  }
})

/** 公司信息里的启用日期（账套从这天开始记账），是建账时间的默认值 */
function companyStartDate(): string {
  const company = authStore.companies.find(
    (item) => item.company_id === authStore.currentCompanyId,
  )
  return String(company?.startDate || '')
}

/** 营业执照成立日期；未填时为空，调用方回退到启用日期 */
function companyIncorporationDate(): string {
  const company = authStore.companies.find(
    (item) => item.company_id === authStore.currentCompanyId,
  )
  return String(company?.incorporationDate || '')
}

// 账套相关初始化：首次 mount 与切换账套后都要走一遍
async function bootstrapOpeningPage() {
  fiscalYear.value = new Date().getFullYear()
  // 默认停在账套的启用年月（公司信息里的启用日期），之后仍可手动改。
  // 启用年在未来时后端 readiness 也按 max(今年, 启用年) 判定，这里必须同源，
  // 否则录完了也判不到；年份不在下拉范围内（很早以前建账）就保持今年。
  const { year: startYear } = resolveOpeningPeriod(null, companyStartDate())
  if (startYear && yearOptions.some(o => o.value === startYear)) {
    fiscalYear.value = startYear
  }

  await load()
  await checkPendingImportFromSession()
}

onMounted(async () => {
  await bootstrapOpeningPage()
  try {
    // /api/auth/me 返回扁平结构（phone 在顶层），此前误写成 profile.user.phone，
    // 取到的永远是 undefined，导致短信二次验证弹窗的手机号始终为空、用户
    // 直接点发送时一条短信都不会发出。保持 UserProfile 类型，防止回归。
    const profile = await getAuthMe()
    phone.value = profile.phone || ''
  } catch {
    // 绑定手机号会在提交时由后端再次校验
  }
})

// 本页被 KeepAlive 缓存，切换账套不会重新 mount：必须重取新账套的期初数据与
// 税务备忘；并收起二次验证弹窗，避免把旧账套的表单提交进新账套上下文
watch(
  () => authStore.currentCompanyId,
  (next, prev) => {
    if (next === prev) return
    stepUpVisible.value = false
    manualEntryExpanded.value = false
    importSummary.value = null
    onlyFilled.value = false
    void bootstrapOpeningPage()
  },
)

watch(
  () => reconciliation.value.rows.length,
  (len) => {
    if (len === 0 && activeTab.value === 'reconcile') {
      activeTab.value = 'balance'
    }
  },
)

watch(
  () => activeTrialBalanceRows.value.length,
  (len) => {
    if (len === 0 && activeTab.value === 'trial') {
      activeTab.value = 'balance'
    }
  },
)
</script>

<template>
  <div class="opening-page">
    <!-- 旧账导入入口暂不开放：真实导出文件的表头/凭证格式还没补齐 -->
    <div v-if="mainTab === 'legacy'" class="legacy-tab-pane">
      <LegacyImportTab embedded @imported="onLegacyImported" />
    </div>

    <div v-show="mainTab === 'opening'">
      <!-- 隐式文件输入 -->
      <input
        ref="fileInputRef"
        type="file"
        accept=".xlsx,.xls"
        style="display: none"
        @change="onFileSelected"
      />

    <!-- 顶部导航与工作台抬头（遵循 layout.css .page-header-left / .page-header-right 规范） -->
    <div class="page-header">
      <div class="page-header-left">
        <div class="title-section">
          <div class="title-with-badge">
            <h2>期初余额设置</h2>
            <span v-if="isLocked" class="status-pill pill-locked">
              <i class="ri-lock-2-line" />
              已过账锁定 {{ voucherNo ? `· ${voucherNo}` : '' }}
            </span>
            <span v-else-if="isMidYear" class="status-pill pill-midyear">
              <i class="ri-calendar-event-line" />
              年中建账（{{ startPeriod }}）
            </span>
          </div>
          <p v-if="zeroOpening" class="subtitle">
            {{ zeroOpeningHint }}
          </p>
          <p v-else-if="viewStore.isBoss" class="subtitle">
            把原代账的末级科目余额表（{{ expectedPeriodLabel }}）导进来，系统会自动核对平衡，账目就能连续接上。
          </p>
          <p v-else class="subtitle">
            导入 {{ expectedPeriodLabel }} 末级科目余额表（资产负债取期末余额，损益取本年累计发生额），试算平衡后生成不可篡改的系统期初凭证。
          </p>
        </div>
      </div>

      <div class="page-header-right">
        <div class="period-selectors">
          <NSelect
            v-model:value="fiscalYear"
            :options="yearOptions"
            style="width: 138px"
            size="medium"
            @update:value="load"
          />
          <NSelect
            v-model:value="startMonth"
            :options="monthOptions"
            :disabled="isLocked"
            style="width: 172px"
            size="medium"
          />
        </div>

        <div class="header-divider" />

        <div class="action-buttons">
          <NButton
            size="medium"
            :type="!isLocked && !isBalanced ? 'primary' : 'default'"
            :secondary="isLocked || isBalanced"
            :disabled="isLocked"
            @click="triggerUpload"
          >
            <template #icon><i class="ri-upload-2-line" /></template>
            导入余额表
          </NButton>
          <NButton
            v-if="isLocked"
            size="medium"
            type="error"
            ghost
            @click="openResetModal"
          >
            <template #icon><i class="ri-refresh-line" /></template>
            冲销并重设期初
          </NButton>
          <NButton
            v-else
            size="medium"
            :type="isBalanced ? 'primary' : 'default'"
            :secondary="!isBalanced"
            :loading="saving"
            :disabled="!isBalanced"
            @click="openSaveModal"
          >
            <template #icon><i class="ri-check-double-line" /></template>
            生成期初凭证
          </NButton>
        </div>
      </div>
    </div>

    <!-- 空白态：聚焦引导上传原代账科目余额表 -->
    <NSpin v-if="isBlankState" :show="loading">
      <div class="surface-card blank-onboarding-card">
        <div class="blank-onboarding-head">
          <div>
            <h3>
              {{ viewStore.isBoss ? '第一步：把原代账的科目余额表传进来' : `导入科目余额表（期间 ${expectedPeriodLabel}）` }}
            </h3>
            <p>
              {{
                viewStore.isBoss
                  ? `向原代账或会计要一份 Excel 版末级科目余额表（截至 ${expectedPeriodLabel}），拖进来系统自动匹配科目与往来明细。`
                  : '支持主流代账与财务软件导出的 Excel 余额表（资产负债取期末余额，损益取本年累计发生额）。'
              }}
            </p>
          </div>
          <NButton text size="small" type="primary" @click="handleDownloadTemplate">
            <template #icon><i class="ri-download-2-line" /></template>
            下载空白模板
          </NButton>
        </div>

        <FileDropzone
          accept=".xlsx,.xls"
          icon="ri-file-excel-2-line"
          title="点击或拖拽原代账科目余额表（.xlsx / .xls）"
          subtitle="支持金蝶、用友、柠檬云、亿企赢等导出表，自动识别科目编码、名称与借贷金额"
          :loading="loading"
          @file="processOpeningFile"
          @error="message.error($event)"
        />

        <div class="agency-request-box">
          <div class="agency-request-head">
            <div class="agency-request-title-group">
              <span class="agency-request-label">
                <i class="ri-chat-3-line" aria-hidden="true" />
                向原代账要表话术
              </span>
              <div class="agency-scene-switch" role="group" aria-label="选择要表沟通场景">
                <button
                  type="button"
                  class="agency-scene-btn"
                  :class="{ active: agencyRequestScene === 'check' }"
                  @click="agencyRequestScene = 'check'"
                >
                  先要表对账（推荐）
                </button>
                <button
                  type="button"
                  class="agency-scene-btn"
                  :class="{ active: agencyRequestScene === 'handover' }"
                  @click="agencyRequestScene = 'handover'"
                >
                  亲戚接手交接
                </button>
              </div>
            </div>
            <NButton size="tiny" type="primary" secondary @click="copyAgencyRequest">
              <template #icon><i class="ri-file-copy-line" /></template>
              一键复制话术
            </NButton>
          </div>
          <p class="agency-request-preview">{{ agencyRequestText }}</p>
          <div class="agency-request-tip-row">
            <span class="agency-request-tip">
              沟通技巧：先礼后兵，尽量别直接提「换了新代账/软件」引发拖延；拿到 Excel 直接拖入上方即可。
            </span>
            <NPopover trigger="click" placement="top-end" :style="{ maxWidth: '340px' }">
              <template #trigger>
                <button type="button" class="agency-help-trigger">对方拖延不给怎么办？</button>
              </template>
              <ul class="agency-help-popover">
                <li><strong>优先要电子版</strong>：只要带末级明细的 Excel 科目余额表即可导入，发消息时不要附带本页面截图。</li>
                <li><strong>依法要求移交</strong>：依据《代理记账管理办法》第 12 条，终止合作时原代账必须交接账务资料；有代理记账许可证的可向当地财政局反馈，无证代账可向市场监管（12315）投诉。</li>
                <li><strong>交接后改权限</strong>：拿到资料后，建议尽快在电子税务局变更财务负责人及办税员。</li>
              </ul>
            </NPopover>
          </div>
        </div>

        <div class="blank-onboarding-footer">
          <NButton size="small" secondary @click="manualEntryExpanded = true">
            <template #icon><i class="ri-edit-line" /></template>
            手工填写常用科目（{{ commonBalanceCount }} 项）
          </NButton>
          <NPopconfirm v-if="isMigrationCompany" @positive-click="switchToZeroStart">
            <template #trigger>
              <NButton size="small" quaternary :loading="switchingToZeroStart">
                原代账一直零申报、无历史余额？转为从零起账
              </NButton>
            </template>
            确认该账套无历史科目余额并转为「从零起账」？切换后将解除导入与记账限制（系统会记录本次变更审计日志）。
          </NPopconfirm>
        </div>
      </div>
    </NSpin>

    <!-- 已导入 / 手工填表 / 已锁定工作台 -->
    <template v-else>
    <!-- 吸顶试算平衡监控栏 -->
    <div
      class="balance-summary-bar"
      :class="{
        'is-locked': isLocked,
        'is-balanced': !isLocked && isBalanced,
        'is-unbalanced': !isLocked && !isBalanced && (totalDebit > 0 || totalCredit > 0),
        'is-empty': !isLocked && totalDebit === 0 && totalCredit === 0,
      }"
    >
      <div class="summary-metrics">
        <div class="metric-item">
          <span class="metric-label">期初借方合计</span>
          <span class="metric-value">¥ {{ formatCurrency(totalDebit) }}</span>
        </div>
        <div class="metric-divider" />
        <div class="metric-item">
          <span class="metric-label">期初贷方合计</span>
          <span class="metric-value">¥ {{ formatCurrency(totalCredit) }}</span>
        </div>
        <div class="metric-divider" />
        <div class="metric-item">
          <span class="metric-label">{{ viewStore.isBoss ? '借贷差额' : '借贷试算差额' }}</span>
          <span
            class="metric-value"
            :class="{
              'text-income': isBalanced,
              'text-expense': !isBalanced && (totalDebit > 0 || totalCredit > 0),
            }"
          >
            ¥ {{ formatCurrency(Math.abs(balanceDiff)) }}
            <small v-if="!isBalanced && balanceDiff !== 0" class="diff-side-hint">
              （{{ balanceDiff > 0 ? '借方多出' : '贷方多出' }}）
            </small>
          </span>
        </div>

        <template v-if="isMidYear">
          <div class="metric-divider" />
          <div class="metric-item">
            <template v-if="filledYtdCount > 0 && Math.abs(ytdNetProfit) < 0.01">
              <span class="metric-label">
                {{ ytdStartMonth }}-{{ Math.max(1, startMonthNum - 1) }}月损益累计
              </span>
              <span class="metric-value">
                已填 {{ filledYtdCount }} 项
                <small class="metric-sub-note">原账已结转至 3103</small>
              </span>
            </template>
            <template v-else>
              <span class="metric-label">
                {{ viewStore.isBoss
                  ? `${ytdStartMonth}-${Math.max(1, startMonthNum - 1)}月累计净利（自动结转）`
                  : `${ytdStartMonth}-${Math.max(1, startMonthNum - 1)}月累计净损益（自动转入 3103）` }}
              </span>
              <span
                class="metric-value"
                :class="ytdNetProfit >= 0 ? 'text-income' : 'text-expense'"
              >
                {{ ytdNetProfit >= 0 ? '+' : '' }}¥ {{ formatCurrency(ytdNetProfit) }}
              </span>
            </template>
          </div>
        </template>
      </div>

      <div class="summary-verdict">
        <span v-if="isLocked" class="verdict-badge badge-locked">
          <i class="ri-shield-check-line" />
          期初已过账锁定
        </span>
        <template v-else-if="isBalanced">
          <span class="verdict-badge badge-balanced">
            <i class="ri-checkbox-circle-line" />
            {{ viewStore.isBoss ? '已配平 · 可以生成期初' : '借贷平衡 · 满足建账标准' }}
          </span>
          <NButton type="primary" size="small" :loading="saving" @click="openSaveModal">
            <template #icon><i class="ri-shield-check-line" /></template>
            生成期初凭证
          </NButton>
        </template>
        <span v-else-if="totalDebit === 0 && totalCredit === 0" class="verdict-badge badge-empty">
          <i class="ri-information-line" />
          待录入或导入期初余额
        </span>
        <span v-else class="verdict-badge badge-unbalanced">
          <i class="ri-error-warning-line" />
          {{ viewStore.isBoss ? '尚未配平' : '借贷未平' }} · 差额 ¥{{ formatCurrency(Math.abs(balanceDiff)) }}
        </span>
      </div>

      <div
        v-if="!isLocked && !isBalanced && unmatchedBalanceImpact.count > 0"
        class="unmatched-impact-hint"
        :class="{ 'is-exact': diffExplainedByUnmatched }"
      >
        <i class="ri-links-line" />
        <span v-if="diffExplainedByUnmatched">
          差额全部来自 {{ unmatchedBalanceImpact.count }} 个未匹配的资产/负债科目（合计
          ¥{{ formatCurrency(Math.abs(unmatchedBalanceImpact.net)) }}）。在导入结果弹窗下方完成映射后即可配平，源文件本身是平的。
        </span>
        <span v-else>
          有 {{ unmatchedBalanceImpact.count }} 个未匹配的资产/负债科目（合计
          ¥{{ formatCurrency(Math.abs(unmatchedBalanceImpact.net)) }}）未计入合计，其余差额仍需手工核对。
        </span>
      </div>
    </div>

    <!-- 固定资产期初卡片接续与对账提示条 -->
    <div v-if="hasFixedAssetNotice" class="surface-card fixed-asset-notice-card">
      <div class="fa-notice-left">
        <i class="ri-computer-line fa-notice-icon" />
        <div class="fa-notice-content">
          <span class="fa-notice-title">
            固定资产期初卡片接续
            <NTag size="tiny" :type="fixedAssets.length > 0 ? 'success' : 'warning'" :bordered="false" style="margin-left: 6px">
              {{ fixedAssets.length > 0 ? `已关联 ${fixedAssets.length} 张卡片` : '待接续资产卡片' }}
            </NTag>
          </span>
          <span class="fa-notice-desc">
            原值卡片合计：¥{{ formatCurrency(fixedAssetCardCostTotal) }}（1601 借方：¥{{ formatCurrency(acct1601Debit) }}）
            · 累计折旧卡片合计：¥{{ formatCurrency(fixedAssetCardDeprTotal) }}（1602 贷方：¥{{ formatCurrency(acct1602Credit) }}）
          </span>
        </div>
      </div>
      <div class="fa-notice-right">
        <span
          v-if="fixedAssetCostMatched && fixedAssetDeprMatched"
          class="fa-status-pill pill-match"
        >
          <i class="ri-checkbox-circle-line" />
          卡片与总账科目完全吻合
        </span>
        <span v-else class="fa-status-pill pill-mismatch">
          <i class="ri-error-warning-line" />
          {{ !fixedAssetCostMatched ? '原值与 1601 不一致' : '折旧与 1602 不一致' }}
        </span>
      </div>
    </div>

    <!-- 主工作台卡片：科目期初明细与年中损益切换 -->
    <NSpin :show="loading">
      <div class="surface-card ledger-table-card">
        <!-- 顶层账表切换（年中建账时开启双账页） -->
        <div class="sheet-tabs-bar">
          <div class="sheet-tabs">
            <button
              type="button"
              class="sheet-tab-btn"
              :class="{ active: activeTab === 'balance' }"
              @click="activeTab = 'balance'"
            >
              <i class="ri-scales-3-line" />
              <span>资产负债期初余额</span>
              <span class="tab-count">{{ filledBalanceCount }} / {{ balanceItems.length }}</span>
            </button>

            <button
              v-if="isMidYear || filledYtdCount > 0"
              type="button"
              class="sheet-tab-btn"
              :class="{ active: activeTab === 'ytd' }"
              @click="activeTab = 'ytd'"
            >
              <i class="ri-line-chart-line" />
              <span>损益本年累计发生额（{{ ytdStartMonth }}-{{ Math.max(1, startMonthNum - 1) }}月）</span>
              <span class="tab-count">{{ filledYtdCount }} / {{ ytdItems.length }}</span>
            </button>

            <button
              v-if="activeTrialBalanceRows.length > 0"
              type="button"
              class="sheet-tab-btn"
              :class="{ active: activeTab === 'trial' }"
              @click="activeTab = 'trial'"
            >
              <i class="ri-table-line" />
              <span>科目余额表（原表）</span>
              <span class="tab-count">{{ activeTrialBalanceRows.length }} 行</span>
            </button>

            <button
              v-if="reconciliation.rows.length > 0 && (isMidYear || yearStartItems.length > 0)"
              type="button"
              class="sheet-tab-btn"
              :class="{ active: activeTab === 'reconcile' }"
              @click="activeTab = 'reconcile'"
            >
              <i class="ri-search-eye-line" />
              <span>期初对账</span>
              <span class="tab-count">{{ reconciliation.movementCount }} / {{ reconciliation.rows.length }}</span>
            </button>
          </div>

          <div class="toolbar-right">
            <NButton
              v-if="importSummary && !isLocked"
              size="small"
              secondary
              :type="unmatchedRows.length > 0 ? 'warning' : 'default'"
              @click="importModalVisible = true"
            >
              <template #icon><i class="ri-file-list-3-line" /></template>
              {{ unmatchedRows.length > 0 ? `调整映射（${unmatchedRows.length}）` : '查看导入报告' }}
            </NButton>
            <NButton
              v-if="manualEntryExpanded && filledBalanceCount === 0 && filledYtdCount === 0 && !isLocked"
              size="small"
              quaternary
              @click="manualEntryExpanded = false"
            >
              返回导入引导
            </NButton>
            <NInput
              v-if="activeTab === 'balance' || activeTab === 'ytd'"
              v-model:value="searchQuery"
              placeholder="搜索科目代码、名称或客商..."
              clearable
              size="small"
              style="width: 220px"
            >
              <template #prefix><i class="ri-search-line" /></template>
            </NInput>

            <label v-if="activeTab === 'balance' || activeTab === 'ytd'" class="only-filled-toggle">
              <NSwitch v-model:value="onlyFilled" size="small" />
              <span>仅看已填项</span>
            </label>
          </div>
        </div>

        <!-- 二级分类筛选胶囊栏 -->
        <div class="sub-filter-bar">
          <template v-if="activeTab === 'balance'">
            <div class="category-pills">
              <div class="scope-switch" role="group" aria-label="科目范围">
                <button
                  type="button"
                  class="pill-btn"
                  :class="{ active: accountScope === 'common' }"
                  @click="accountScope = 'common'"
                >
                  常用科目（{{ commonBalanceCount }}）
                </button>
                <button
                  type="button"
                  class="pill-btn"
                  :class="{ active: accountScope === 'all' }"
                  @click="accountScope = 'all'"
                >
                  全部科目（{{ balanceItems.length }}）
                </button>
              </div>
              <span class="pill-divider" />
              <button
                type="button"
                class="pill-btn"
                :class="{ active: categoryFilter === 'all' }"
                @click="categoryFilter = 'all'"
              >
                全部分类
              </button>
              <button
                type="button"
                class="pill-btn"
                :class="{ active: categoryFilter === 'asset' }"
                @click="categoryFilter = 'asset'"
              >
                资产及成本类
              </button>
              <button
                type="button"
                class="pill-btn"
                :class="{ active: categoryFilter === 'liability' }"
                @click="categoryFilter = 'liability'"
              >
                负债类
              </button>
              <button
                type="button"
                class="pill-btn"
                :class="{ active: categoryFilter === 'equity' }"
                @click="categoryFilter = 'equity'"
              >
                所有者权益类
              </button>
            </div>
            <span class="rule-hint">
              <i class="ri-information-line" />
              {{
                viewStore.isBoss
                  ? '输入任意一侧金额即可；应收/应付可填写客户或供应商名称'
                  : '同行借贷自动互斥；应收/应付等往来科目可填写客商名称'
              }}
            </span>
          </template>

          <template v-else-if="activeTab === 'ytd'">
            <div class="category-pills">
              <button
                type="button"
                class="pill-btn"
                :class="{ active: ytdCategoryFilter === 'all' }"
                @click="ytdCategoryFilter = 'all'"
              >
                全部损益科目（{{ ytdItems.length }}）
              </button>
              <button
                type="button"
                class="pill-btn"
                :class="{ active: ytdCategoryFilter === 'revenue' }"
                @click="ytdCategoryFilter = 'revenue'"
              >
                收入收益类
              </button>
              <button
                type="button"
                class="pill-btn"
                :class="{ active: ytdCategoryFilter === 'expense' }"
                @click="ytdCategoryFilter = 'expense'"
              >
                成本费用类
              </button>
            </div>
            <span class="rule-hint">
              <i class="ri-information-line" />
              年中建账保存时，系统自动将 {{ ytdStartMonth }}-{{ Math.max(1, startMonthNum - 1) }} 月累计损益差额配平至「3103 本年利润」
            </span>
          </template>

          <template v-else-if="activeTab === 'trial'">
            <span class="rule-hint">
              <i class="ri-information-line" />
              原表逐行核对快照（只读，不随手工调整变化，也不参与凭证与报表取数）。
            </span>
          </template>

          <template v-else>
            <span class="rule-hint">
              <i class="ri-information-line" />
              年初余额取自导入快照；本年净变动 = 期初 − 年初，只反映净额，不还原年内借贷毛额。年初、期初两列可直接与原科目余额表逐行对照。
            </span>
          </template>
        </div>

        <!-- 表格 1：资产负债期初明细表 -->
        <div v-if="activeTab === 'balance'" class="table-container">
          <table class="balance-table">
            <thead>
              <tr>
                <th class="col-code">科目代码</th>
                <th class="col-name">科目名称</th>
                <th class="col-dir">默认方向</th>
                <th class="col-partner">往来客商（选填）</th>
                <th class="col-amount">
                  期初借方余额（元）
                  <SoloFieldHelp term="期初余额" />
                </th>
                <th class="col-amount">期初贷方余额（元）</th>
              </tr>
            </thead>
            <tbody v-if="filteredBalanceItems.length > 0">
              <tr
                v-for="item in filteredBalanceItems"
                :key="item.account_code + '|' + (item.partner_name || '')"
                :class="{ 'row-filled': (item.debit_balance || 0) > 0 || (item.credit_balance || 0) > 0 }"
              >
                <td class="col-code">
                  <span>{{ item.account_code }}</span>
                  <span v-if="item.originalCode && item.originalCode !== item.account_code" class="col-code-orig">
                    原 {{ item.originalCode }}
                  </span>
                </td>
                <td class="col-name">
                  <span class="acct-name-text">{{ item.account_name || item.account_code }}</span>
                  <span class="acct-cat-tag">{{ getAccountCategoryLabel(item.account_code) }}</span>
                </td>
                <td class="col-dir">
                  <span
                    class="dir-badge"
                    :class="isCreditNormal(item.account_code) ? 'dir-credit' : 'dir-debit'"
                  >
                    {{ isCreditNormal(item.account_code) ? '贷' : '借' }}
                  </span>
                </td>
                <td class="col-partner" :title="item.partner_name || ''">
                  <NInput
                    v-if="supportsPartner(item.account_code)"
                    v-model:value="item.partner_name"
                    :disabled="isLocked"
                    :input-props="{ title: item.partner_name || '' }"
                    placeholder="客户或供应商名称"
                    size="small"
                  />
                  <span v-else class="no-partner">—</span>
                </td>
                <td class="col-amount">
                  <NInputNumber
                    :disabled="isLocked"
                    :value="item.debit_balance || null"
                    :min="0"
                    :precision="2"
                    :show-button="false"
                    placeholder="0.00"
                    size="small"
                    class="amount-input"
                    @update:value="(v) => handleBalanceDebitChange(item, v)"
                  />
                </td>
                <td class="col-amount">
                  <NInputNumber
                    :disabled="isLocked"
                    :value="item.credit_balance || null"
                    :min="0"
                    :precision="2"
                    :show-button="false"
                    placeholder="0.00"
                    size="small"
                    class="amount-input"
                    @update:value="(v) => handleBalanceCreditChange(item, v)"
                  />
                </td>
              </tr>
            </tbody>
            <tbody v-else>
              <tr>
                <td colspan="6" class="empty-table-cell">
                  <i class="ri-inbox-line" />
                  <span>当前筛选条件下暂无匹配科目</span>
                </td>
              </tr>
            </tbody>
            <tfoot>
              <tr>
                <td colspan="4" class="col-total">资产负债期初借贷合计</td>
                <td class="col-amount total">¥ {{ formatCurrency(totalDebit) }}</td>
                <td class="col-amount total">¥ {{ formatCurrency(totalCredit) }}</td>
              </tr>
            </tfoot>
          </table>
        </div>

        <!-- 表格 2：损益本年累计发生额（年中建账） -->
        <div v-else-if="activeTab === 'ytd'" class="table-container">
          <table class="balance-table">
            <thead>
              <tr>
                <th class="col-code">科目代码</th>
                <th class="col-name">损益科目名称</th>
                <th class="col-dir">科目性质</th>
                <th class="col-amount">本年累计借方发生额（成本/费用）</th>
                <th class="col-amount">本年累计贷方发生额（收入/收益）</th>
              </tr>
            </thead>
            <tbody v-if="filteredYtdItems.length > 0">
              <tr
                v-for="item in filteredYtdItems"
                :key="item.account_code"
                :class="{ 'row-filled': (item.debit_balance || 0) > 0 || (item.credit_balance || 0) > 0 }"
              >
                <td class="col-code">
                  <span>{{ item.account_code }}</span>
                  <span v-if="item.originalCode && item.originalCode !== item.account_code" class="col-code-orig">
                    原 {{ item.originalCode }}
                  </span>
                </td>
                <td class="col-name">
                  <span class="acct-name-text">{{ item.account_name || item.account_code }}</span>
                  <span class="acct-cat-tag">{{ getAccountCategoryLabel(item.account_code) }}</span>
                </td>
                <td class="col-dir">
                  <span
                    class="dir-badge"
                    :class="isCreditNormal(item.account_code) ? 'dir-credit' : 'dir-debit'"
                  >
                    {{ isCreditNormal(item.account_code) ? '贷（收入）' : '借（费用）' }}
                  </span>
                </td>
                <td class="col-amount">
                  <NInputNumber
                    :disabled="isLocked"
                    :value="item.debit_balance || null"
                    :min="0"
                    :precision="2"
                    :show-button="false"
                    placeholder="0.00"
                    size="small"
                    class="amount-input"
                    @update:value="(v) => handleYtdDebitChange(item, v)"
                  />
                </td>
                <td class="col-amount">
                  <NInputNumber
                    :disabled="isLocked"
                    :value="item.credit_balance || null"
                    :min="0"
                    :precision="2"
                    :show-button="false"
                    placeholder="0.00"
                    size="small"
                    class="amount-input"
                    @update:value="(v) => handleYtdCreditChange(item, v)"
                  />
                </td>
              </tr>
            </tbody>
            <tbody v-else>
              <tr>
                <td colspan="5" class="empty-table-cell">
                  <i class="ri-inbox-line" />
                  <span>当前筛选条件下暂无匹配损益科目</span>
                </td>
              </tr>
            </tbody>
            <tfoot>
              <tr>
                <td colspan="3" class="col-total">
                  本年累计损益合计（差额 ¥{{ formatCurrency(Math.abs(ytdNetProfit)) }} 自动转入 3103 本年利润）
                </td>
                <td class="col-amount total">¥ {{ formatCurrency(ytdTotalDebit) }}</td>
                <td class="col-amount total">¥ {{ formatCurrency(ytdTotalCredit) }}</td>
              </tr>
            </tfoot>
          </table>
        </div>

        <!-- 表格 3：科目余额表原表快照（只读，不参与凭证/报表取数） -->
        <div v-else-if="activeTab === 'trial'" class="table-container">
          <TrialBalanceTable
            :rows="activeTrialBalanceRows"
            :columns="activeTrialBalanceColumns"
            max-height="62vh"
          />
        </div>

        <!-- 表格 4：期初对账（只读，不参与报表取数） -->
        <div v-else class="table-container">
          <div v-if="!reconciliation.yearStartKnown" class="recon-warning">
            <i class="ri-error-warning-line" />
            <span>
              年初余额未导入：年中建账且账套里没有年初快照，无法核对「年初 → 期初」的变动。
              请重新导入含「年初余额」或「本年累计发生额」列的科目余额表。
            </span>
          </div>

          <div class="recon-summary">
            <div class="recon-card">
              <span class="recon-label">年初余额合计</span>
              <span class="recon-value">
                {{ reconciliation.yearStartKnown
                  ? `借 ¥ ${formatCurrency(reconciliation.totals.yearStartDebit)} / 贷 ¥ ${formatCurrency(reconciliation.totals.yearStartCredit)}`
                  : '—' }}
              </span>
              <span
                v-if="reconciliation.yearStartKnown"
                class="recon-state"
                :class="Math.abs(reconciliation.totals.yearStartDiff) <= 0.01 ? 'ok' : 'diff'"
              >
                {{ Math.abs(reconciliation.totals.yearStartDiff) <= 0.01
                  ? '借贷平衡'
                  : `差额 ¥ ${formatCurrency(Math.abs(reconciliation.totals.yearStartDiff))}` }}
              </span>
            </div>

            <div class="recon-card">
              <span class="recon-label">本年净变动合计（推算）</span>
              <span class="recon-value">
                {{ reconciliation.yearStartKnown
                  ? `借 ¥ ${formatCurrency(reconciliation.totals.movementDebit)} / 贷 ¥ ${formatCurrency(reconciliation.totals.movementCredit)}`
                  : '—' }}
              </span>
              <span
                v-if="reconciliation.yearStartKnown"
                class="recon-state"
                :class="Math.abs(reconciliation.totals.movementDebit - reconciliation.totals.movementCredit) <= 0.01 ? 'ok' : 'diff'"
              >
                {{ Math.abs(reconciliation.totals.movementDebit - reconciliation.totals.movementCredit) <= 0.01
                  ? '借贷净额相抵'
                  : `差额 ¥ ${formatCurrency(Math.abs(reconciliation.totals.movementDebit - reconciliation.totals.movementCredit))}` }}
              </span>
            </div>

            <div class="recon-card">
              <span class="recon-label">期初余额合计</span>
              <span class="recon-value">
                借 ¥ {{ formatCurrency(reconciliation.totals.openingDebit) }} / 贷 ¥ {{ formatCurrency(reconciliation.totals.openingCredit) }}
              </span>
              <span
                class="recon-state"
                :class="Math.abs(reconciliation.totals.openingDiff) <= 0.01 ? 'ok' : 'diff'"
              >
                {{ Math.abs(reconciliation.totals.openingDiff) <= 0.01
                  ? '借贷平衡'
                  : `差额 ¥ ${formatCurrency(Math.abs(reconciliation.totals.openingDiff))}` }}
              </span>
            </div>
          </div>

          <table class="balance-table recon-table">
            <thead>
              <tr>
                <th class="col-code">科目代码</th>
                <th class="col-name">科目名称</th>
                <th class="col-amount">年初借方</th>
                <th class="col-amount">年初贷方</th>
                <th class="col-amount">本年净变动借方</th>
                <th class="col-amount">本年净变动贷方</th>
                <th class="col-amount">期初借方</th>
                <th class="col-amount">期初贷方</th>
              </tr>
            </thead>
            <tbody v-if="filteredReconciliationRows.length > 0">
              <tr
                v-for="row in filteredReconciliationRows"
                :key="row.account_code"
                :class="{ 'row-filled': row.hasMovement }"
              >
                <td class="col-code">{{ row.account_code }}</td>
                <td class="col-name">
                  <span class="acct-name-text">{{ row.account_name }}</span>
                  <span class="acct-cat-tag">{{ getAccountCategoryLabel(row.account_code) }}</span>
                </td>
                <td class="col-amount recon-amount">
                  {{ reconciliation.yearStartKnown ? amountOrDash(row.yearStartDebit) : '—' }}
                </td>
                <td class="col-amount recon-amount">
                  {{ reconciliation.yearStartKnown ? amountOrDash(row.yearStartCredit) : '—' }}
                </td>
                <td class="col-amount recon-amount">
                  {{ reconciliation.yearStartKnown ? amountOrDash(row.movementDebit) : '—' }}
                </td>
                <td class="col-amount recon-amount">
                  {{ reconciliation.yearStartKnown ? amountOrDash(row.movementCredit) : '—' }}
                </td>
                <td class="col-amount recon-amount">{{ amountOrDash(row.openingDebit) }}</td>
                <td class="col-amount recon-amount">{{ amountOrDash(row.openingCredit) }}</td>
              </tr>
            </tbody>
            <tbody v-else>
              <tr>
                <td colspan="8" class="empty-table-cell">
                  <i class="ri-inbox-line" />
                  <span>当前筛选条件下暂无可核对科目</span>
                </td>
              </tr>
            </tbody>
            <tfoot>
              <tr>
                <td colspan="2" class="col-total">合计（{{ reconciliation.rows.length }} 个有余额科目）</td>
                <td class="col-amount total">
                  {{ reconciliation.yearStartKnown ? amountOrDash(reconciliation.totals.yearStartDebit) : '—' }}
                </td>
                <td class="col-amount total">
                  {{ reconciliation.yearStartKnown ? amountOrDash(reconciliation.totals.yearStartCredit) : '—' }}
                </td>
                <td class="col-amount total">
                  {{ reconciliation.yearStartKnown ? amountOrDash(reconciliation.totals.movementDebit) : '—' }}
                </td>
                <td class="col-amount total">
                  {{ reconciliation.yearStartKnown ? amountOrDash(reconciliation.totals.movementCredit) : '—' }}
                </td>
                <td class="col-amount total">{{ amountOrDash(reconciliation.totals.openingDebit) }}</td>
                <td class="col-amount total">{{ amountOrDash(reconciliation.totals.openingCredit) }}</td>
              </tr>
            </tfoot>
          </table>
        </div>
      </div>
    </NSpin>

    <!-- 手工模式下的零余额逃生口（沉底展示） -->
    <div v-if="isMigrationCompany && !isLocked && filledBalanceCount === 0 && filledYtdCount === 0" class="migration-zero-bar">
      <div class="migration-zero-text">
        <i class="ri-information-line" aria-hidden="true" />
        <span>如果原代账此前一直零申报、确实无历史科目余额，或建账时误选了从其他代账转入，可转为从零起账。</span>
      </div>
      <NPopconfirm @positive-click="switchToZeroStart">
        <template #trigger>
          <NButton size="small" secondary :loading="switchingToZeroStart">
            无历史余额，转为从零起账
          </NButton>
        </template>
        确认该账套无历史科目余额并转为「从零起账」？切换后将解除导入与记账限制（系统会记录本次变更审计日志）。
      </NPopconfirm>
    </div>
    </template>

    <!-- 导入诊断反馈弹窗 -->
    <NModal
      v-model:show="importModalVisible"
      preset="card"
      :auto-focus="false"
      :title="periodCheck && !periodCheck.ok ? '导入期间需要确认' : 'Excel 科目余额表导入结果'"
      style="max-width: min(1120px, 94vw)"
    >
      <!-- 期间不符：先确认期间，金额不写入表格 -->
      <div v-if="periodCheck && !periodCheck.ok" class="import-period-gate">
        <NAlert :type="zeroOpening ? 'info' : 'error'" :bordered="false">
          {{ periodCheck.message }}
        </NAlert>
        <div class="period-meta">
          <span v-if="statementPeriod">已识别表格期间：<strong>{{ statementPeriod.label }}</strong></span>
          <span v-else>
            未能从表头识别期间，
            <NButton text size="tiny" type="primary" @click="handleDownloadTemplate">下载模板</NButton>
            整理后重导。
          </span>
        </div>
        <!-- 表头已识别出期间时不给下拉：否则 1-9 月的表能手选 7 月绕过校验 -->
        <div v-if="!statementPeriod && cutoffOptions.length > 0" class="period-cutoff-row">
          <span>这份表截止到：</span>
          <NSelect v-model:value="cutoffMonth" :options="cutoffOptions" size="small" style="width: 130px;" />
          <NButton size="small" type="primary" secondary @click="applyCutoffSelection">
            按所选月份重新校验
          </NButton>
        </div>
        <p v-else-if="!zeroOpening" class="period-help">请重新导出期间相符的科目余额表，或换一份表。</p>
        <p v-if="zeroOpening" class="period-help">
          {{ zeroOpeningHint }}
        </p>
        <p v-else class="period-help">
          建账期间 {{ startPeriod }} 需要 {{ expectedPeriodLabel }} 的科目余额表：资产负债取期末余额，损益取本年累计发生额。
        </p>
      </div>

      <div v-else-if="importSummary" class="import-report-content">
        <NAlert :type="importSummary.isBalanced ? 'success' : 'warning'" :bordered="false">
          {{
            importSummary.isBalanced
              ? '导入成功且借贷已平衡！核对无误后即可生成期初凭证。'
              : '已识别的数据已填入主表；请优先在「待确认映射」指定未匹配科目并重算，也可随时去主表手工调整。'
          }}
        </NAlert>

        <div class="import-kpi-grid">
          <div class="import-kpi-card">
            <span class="kpi-label">期初借方导入</span>
            <strong class="kpi-value">¥ {{ formatCurrency(importSummary.totalDebit) }}</strong>
          </div>
          <div class="import-kpi-card">
            <span class="kpi-label">期初贷方导入</span>
            <strong class="kpi-value">¥ {{ formatCurrency(importSummary.totalCredit) }}</strong>
          </div>
          <div class="import-kpi-card">
            <span class="kpi-label">试算差额</span>
            <strong
              class="kpi-value"
              :class="importSummary.isBalanced ? 'text-income' : 'text-expense'"
            >
              ¥ {{ formatCurrency(Math.abs(importSummary.totalDebit - importSummary.totalCredit)) }}
            </strong>
            <span class="kpi-sub">
              {{
                importSummary.isBalanced
                  ? '借贷已平衡'
                  : diffExplainedByUnmatched
                    ? `全部来自 ${unmatchedBalanceImpact.count} 项待映射`
                    : unmatchedBalanceImpact.count > 0
                      ? `含 ${unmatchedBalanceImpact.count} 项未匹配科目`
                      : '请核对原表借贷'
              }}
            </span>
          </div>
          <div class="import-kpi-card">
            <span class="kpi-label">匹配与入账</span>
            <strong class="kpi-value">已入账 {{ importSummary.matchedCount }} 个</strong>
            <span class="kpi-sub">
              {{
                [
                  importSummary.ytdMatchedCount > 0 ? `损益 ${importSummary.ytdMatchedCount}` : '',
                  unmatchedRows.length > 0 ? `待映射 ${unmatchedRows.length}` : '',
                  importSummary.fixedAssetCount > 0 ? `固资 ${importSummary.fixedAssetCount}` : '',
                ].filter(Boolean).join(' · ') || '科目全部匹配'
              }}
            </span>
          </div>
        </div>

        <div
          v-if="unmatchedRows.length > 0 || importPreviewRows.length > 0"
          class="import-modal-tabs"
          role="tablist"
          aria-label="导入结果核对视图"
        >
          <button
            v-if="unmatchedRows.length > 0"
            type="button"
            role="tab"
            :aria-selected="importModalTab === 'mapping'"
            class="import-modal-tab"
            :class="{ active: importModalTab === 'mapping' }"
            @click="importModalTab = 'mapping'"
          >
            <span>待确认映射</span>
            <span class="tab-badge tone-warning">{{ unmatchedRows.length }}</span>
          </button>
          <button
            v-if="importPreviewRows.length > 0"
            type="button"
            role="tab"
            :aria-selected="importModalTab === 'preview'"
            class="import-modal-tab"
            :class="{ active: importModalTab === 'preview' }"
            @click="importModalTab = 'preview'"
          >
            <span>原表逐行核对</span>
            <span class="tab-badge">{{ importPreviewRows.length }}</span>
          </button>
        </div>

        <!-- Tab 1: 未匹配科目在线映射（影响平衡的资产/负债优先） -->
        <div v-if="importModalTab === 'mapping' && unmatchedRows.length > 0" class="mapping-box">
          <div class="mapping-box-header">
            <span class="mapping-tip">
              标注「系统建议」的已按科目名称预填，请核对或补选后点击底部「按当前映射重新计算」；支持直接搜索系统科目。
            </span>
            <span
              v-if="unmatchedBalanceRows.length > 0"
              class="mapping-preview-pill"
              :class="{ balanced: Math.abs(projectedDiffAfterMapping) <= 0.01 }"
            >
              已指定 {{ mappedBalanceSelectedCount }} / {{ unmatchedBalanceRows.length }} 项资产负债映射 · 预计重算后差额 ¥ {{ formatCurrency(Math.abs(projectedDiffAfterMapping)) }}
            </span>
          </div>
          <div class="mapping-list">
            <div v-for="group in unmatchedMappingGroups" :key="group.key" class="mapping-group">
              <div class="mapping-group-title">{{ group.title }}</div>
              <div
                v-for="row in group.rows"
                :key="mappingKeyOf(row)"
                class="mapping-item"
                :class="{ 'mapping-item-highlighted': highlightedMappingKey === mappingKeyOf(row) }"
              >
                <div class="mapping-source">
                  <span class="source-code">{{ row.originalCode || '—' }}</span>
                  <span class="source-name" :title="row.originalName">{{ row.originalName || '—' }}</span>
                  <span class="source-amount">
                    ({{ row.debit > 0 ? `借 ¥${formatCurrency(row.debit)}` : `贷 ¥${formatCurrency(row.credit)}` }})
                  </span>
                </div>
                <span class="mapping-badge" :class="`tone-${mappingSourceBadge(row).tone}`">
                  {{ mappingSourceBadge(row).label }}
                </span>
                <i class="ri-arrow-right-line mapping-arrow" />
                <NSelect
                  v-model:value="mappingOverrides[mappingKeyOf(row)]"
                  :options="leafAccountOptions"
                  filterable
                  clearable
                  placeholder="搜索或指定本账套科目"
                  size="small"
                  style="width: 260px;"
                />
              </div>
            </div>
          </div>
        </div>

        <!-- Tab 2: 原表逐行核对（默认隐藏 0.00 行，支持按状态筛选与搜索） -->
        <div v-else-if="importPreviewRows.length > 0" class="import-preview-section">
          <TrialBalanceTable
            :rows="importPreviewRows"
            :columns="importColumns"
            :actionable-unmatched="unmatchedRows.length > 0"
            max-height="46vh"
            @select-unmatched="jumpToUnmatchedMapping"
          />
        </div>

        <div v-if="nonDuplicateImportWarnings.length" class="warnings-box">
          <span class="warning-title">其他提示（{{ nonDuplicateImportWarnings.length }}）：</span>
          <ul class="warning-list">
            <li v-for="w in nonDuplicateImportWarnings.slice(0, 5)" :key="w">{{ w }}</li>
            <li v-if="nonDuplicateImportWarnings.length > 5">...其余 {{ nonDuplicateImportWarnings.length - 5 }} 项</li>
          </ul>
        </div>
      </div>
      <template #footer>
        <NSpace justify="end" align="center">
          <template v-if="periodCheck && !periodCheck.ok">
            <NButton @click="importModalVisible = false">放弃本次导入</NButton>
          </template>
          <template v-else-if="importSummary && !importSummary.isBalanced">
            <NButton @click="importModalVisible = false">保留已填数据，去主表手工调整</NButton>
            <NButton
              v-if="unmatchedRows.length > 0"
              type="primary"
              :loading="loading"
              @click="reapplyMapping"
            >
              按当前映射重新计算
            </NButton>
          </template>
          <template v-else>
            <NButton
              v-if="unmatchedRows.length > 0"
              secondary
              :loading="loading"
              @click="reapplyMapping"
            >
              按当前映射重新计算
            </NButton>
            <NButton type="primary" :disabled="!importSummary?.isBalanced" @click="importModalVisible = false">
              完成核对
            </NButton>
          </template>
        </NSpace>
      </template>
    </NModal>

    <!-- 短信二次认证弹窗（兼容生成期初凭证 & 冲销重设期初） -->
    <NModal
      v-model:show="stepUpVisible"
      preset="card"
      :title="stepUpMode === 'reset' ? '短信验证：冲销并重设期初凭证' : '短信确认：生成并锁定期初凭证'"
      style="max-width: 440px"
    >
      <NFormItem v-if="stepUpMode === 'reset'" label="冲销重设原因">
        <NInput v-model:value="resetReason" placeholder="例如：补录往来期初或修正借贷余额" />
      </NFormItem>
      <NFormItem label="已绑定手机号">
        <NInput v-model:value="phone" placeholder="请输入当前账号手机号" />
      </NFormItem>
      <NFormItem label="短信验证码">
        <NSpace :wrap="false" style="width: 100%">
          <NInput v-model:value="code" placeholder="请输入验证码" />
          <NButton :loading="sendingCode" @click="sendCode">发送验证码</NButton>
        </NSpace>
      </NFormItem>
      <p class="step-up-note">
        <template v-if="stepUpMode === 'reset'">
          确认后系统将对当前已过账的<strong>期初凭证（含年中累计损益过渡凭证）</strong>生成红字冲销分录，并解除期初锁定以便重新编辑。
        </template>
        <template v-else>
          确认后将生成系统不可直接篡改的<strong>期初凭证（Opening Journal）</strong>并过账锁定；如需更正，可通过「冲销并重设期初」留痕调整。
        </template>
      </p>
      <NSpace justify="end">
        <NButton @click="stepUpVisible = false">取消</NButton>
        <NButton
          :type="stepUpMode === 'reset' ? 'error' : 'primary'"
          :loading="saving"
          @click="confirmStepUpAction"
        >
          {{ stepUpMode === 'reset' ? '确认冲销并解锁' : '确认生成并过账' }}
        </NButton>
      </NSpace>
    </NModal>
    </div>
  </div>
</template>

<style scoped>
.opening-page {
  max-width: var(--page-width);
  margin: 0 auto;
  padding-bottom: 40px;
  width: 100%;
  box-sizing: border-box;
}

.legacy-tab-pane {
  animation: fadeIn 0.15s ease-in-out;
}
@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to { opacity: 1; transform: translateY(0); }
}

/* 顶部导航与主标题栏（对齐全局 layout.css .page-header 规范） */
.page-header {
  flex-wrap: wrap;
}
.back-btn {
  padding: 0;
  border: none;
  background: transparent;
  cursor: pointer;
}
.title-section {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.title-with-badge {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 3px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
}
.pill-locked {
  background: var(--solo-income-bg);
  color: var(--solo-income);
}
.pill-midyear {
  background: var(--solo-info-bg);
  color: var(--solo-primary);
}
.page-header-right {
  flex-wrap: wrap;
}
.period-selectors {
  display: flex;
  align-items: center;
  gap: 8px;
}
.header-divider {
  width: 1px;
  height: 22px;
  background: var(--solo-border);
  margin: 0 2px;
}
.action-buttons {
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 空白态聚焦引导卡 */
.blank-onboarding-card {
  padding: 20px 24px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.blank-onboarding-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}
.blank-onboarding-head h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--solo-text-primary);
}
.blank-onboarding-head p {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--solo-text-secondary);
  line-height: 1.5;
}
.agency-request-box {
  padding: 12px 14px;
  border-radius: var(--solo-radius);
  background: var(--solo-bg-soft);
  border: 1px solid var(--solo-border-light);
}
.agency-request-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.agency-request-title-group {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.agency-scene-switch {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border-radius: var(--solo-radius-sm);
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
}
.agency-scene-btn {
  border: none;
  background: transparent;
  padding: 3px 9px;
  border-radius: calc(var(--solo-radius-sm) - 2px);
  font-size: 12px;
  line-height: 1.3;
  color: var(--solo-text-secondary);
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease;
}
.agency-scene-btn:hover {
  color: var(--solo-text-primary);
}
.agency-scene-btn.active {
  background: var(--solo-bg-blue-soft);
  color: var(--solo-primary);
  font-weight: 600;
}
.agency-request-label {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 12.5px;
  font-weight: 600;
  color: var(--solo-text-primary);
}
.agency-request-label i {
  color: var(--solo-primary);
}
.agency-request-preview {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.55;
  color: var(--solo-text-secondary);
  white-space: pre-line;
}
.agency-request-tip-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 8px;
  padding-top: 8px;
  border-top: 1px dashed var(--solo-border-light);
}
.agency-request-tip {
  font-size: 12px;
  line-height: 1.45;
  color: var(--solo-text-muted);
}
.agency-help-trigger {
  border: none;
  background: transparent;
  padding: 0;
  font-size: 12px;
  color: var(--solo-primary);
  cursor: pointer;
  white-space: nowrap;
}
.agency-help-trigger:hover {
  text-decoration: underline;
}
.agency-help-popover {
  margin: 0;
  padding-left: 16px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 12.5px;
  line-height: 1.5;
  color: var(--solo-text-secondary);
}
.agency-help-popover strong {
  color: var(--solo-text-primary);
}
.blank-onboarding-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding-top: 8px;
  border-top: 1px solid var(--solo-border-light);
}

/* 吸顶试算平衡监控栏 */
.balance-summary-bar {
  position: sticky;
  top: 12px;
  z-index: 20;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  flex-wrap: wrap;
  padding: 14px 20px;
  border-radius: var(--solo-radius-lg);
  margin-bottom: 16px;
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  box-shadow: var(--solo-shadow-sm);
  transition: border-color 0.2s ease, background-color 0.2s ease;
}
.balance-summary-bar.is-balanced,
.balance-summary-bar.is-locked {
  border-color: var(--solo-income);
  background: var(--solo-income-bg);
}
.balance-summary-bar.is-unbalanced {
  border-color: var(--solo-expense);
  background: var(--solo-expense-bg);
}

.summary-metrics {
  display: flex;
  align-items: center;
  gap: 20px;
  flex-wrap: wrap;
}
.metric-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.metric-label {
  font-size: 12px;
  color: var(--solo-text-secondary);
}
.metric-value {
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-primary);
}
.diff-side-hint {
  font-size: 12px;
  font-weight: 500;
}
.metric-sub-note {
  margin-left: 6px;
  font-size: 12px;
  font-weight: 500;
  color: var(--solo-text-secondary);
}
.metric-divider {
  width: 1px;
  height: 28px;
  background: var(--solo-border-light);
}

.summary-verdict {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.text-income {
  color: var(--solo-income) !important;
}
.text-expense {
  color: var(--solo-expense) !important;
}

.verdict-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: var(--solo-radius-pill);
  font-size: 12px;
  font-weight: 600;
}
.badge-balanced,
.badge-locked {
  background: var(--solo-income-bg);
  color: var(--solo-income);
}
.badge-unbalanced {
  background: var(--solo-expense-bg);
  color: var(--solo-expense);
}
.unmatched-impact-hint {
  flex-basis: 100%;
  display: flex;
  align-items: flex-start;
  gap: 6px;
  padding: 8px 12px;
  border-radius: var(--solo-radius);
  background: var(--solo-bg-page);
  color: var(--solo-text-secondary);
  font-size: 12px;
  line-height: 1.5;
}
.unmatched-impact-hint.is-exact {
  background: var(--solo-bg-blue-soft);
  color: var(--solo-primary);
}
.badge-empty {
  background: var(--solo-bg-soft);
  color: var(--solo-text-secondary);
}

/* 通用平面卡片（遵循 DESIGN.md Flat-at-Rest 规范） */
.surface-card {
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  margin-bottom: 16px;
}

.migration-zero-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  padding: 10px 16px;
  margin-bottom: 16px;
  background: var(--solo-bg-soft);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius);
}
.migration-zero-text {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: var(--solo-text-secondary);
}
.migration-zero-text i {
  font-size: 16px;
  color: var(--solo-primary);
  flex-shrink: 0;
}

/* 固定资产卡片对账提示条 */
.fixed-asset-notice-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  background: var(--solo-bg-card);
  border-left: 3px solid var(--solo-primary);
}
.fa-notice-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.fa-notice-icon {
  font-size: 18px;
  color: var(--solo-primary);
  background: var(--solo-bg-blue-soft);
  width: 32px;
  height: 32px;
  border-radius: var(--solo-radius);
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.fa-notice-content {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.fa-notice-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary);
  display: flex;
  align-items: center;
}
.fa-notice-desc {
  font-size: 12px;
  color: var(--solo-text-secondary);
}
.fa-status-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: var(--solo-radius-pill);
  font-size: 12px;
  font-weight: 500;
}
.fa-status-pill.pill-match {
  background: var(--solo-income-bg);
  color: var(--solo-income);
}
.fa-status-pill.pill-mismatch {
  background: var(--solo-expense-bg);
  color: var(--solo-expense);
}

/* 科目明细主表工具栏 */
.ledger-table-card {
  overflow: hidden;
}
.sheet-tabs-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 14px 20px;
  border-bottom: 1px solid var(--solo-border-light);
  flex-wrap: wrap;
}
.sheet-tabs {
  display: flex;
  align-items: center;
  gap: 8px;
}
.sheet-tab-btn {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 8px 14px;
  border-radius: var(--solo-radius);
  border: 1px solid transparent;
  background: var(--solo-bg-soft);
  color: var(--solo-text-secondary);
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}
.sheet-tab-btn.active {
  background: var(--solo-bg-blue-soft);
  color: var(--solo-primary);
  border-color: var(--solo-border);
}
.tab-count {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: var(--solo-radius-pill);
  background: var(--solo-bg-card);
  font-variant-numeric: tabular-nums;
}
.sheet-tab-btn.active .tab-count {
  background: var(--solo-primary-glow);
  color: var(--solo-primary);
}

.toolbar-right {
  display: flex;
  align-items: center;
  gap: 14px;
}
.only-filled-toggle {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--solo-text-secondary);
  cursor: pointer;
  user-select: none;
}

.sub-filter-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 10px 20px;
  background: var(--solo-bg-page);
  border-bottom: 1px solid var(--solo-border-light);
  flex-wrap: wrap;
}
.category-pills {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.scope-switch {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 2px;
  border-radius: var(--solo-radius-pill);
  background: var(--solo-bg-soft);
  border: 1px solid var(--solo-border);
}
.pill-divider {
  width: 1px;
  height: 16px;
  background: var(--solo-border);
  margin: 0 4px;
}
.pill-btn {
  padding: 4px 12px;
  border-radius: var(--solo-radius-pill);
  border: 1px solid transparent;
  background: transparent;
  color: var(--solo-text-secondary);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}
.pill-btn:hover {
  background: var(--solo-bg-card);
  color: var(--solo-text-primary);
}
.pill-btn.active {
  background: var(--solo-bg-card);
  color: var(--solo-primary);
  border-color: var(--solo-border);
  font-weight: 600;
}
.rule-hint {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--solo-text-muted);
}

/* 表格与金额右对齐 */
.table-container {
  overflow-x: auto;
}
.balance-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.balance-table th {
  text-align: left;
  padding: 10px 16px;
  font-weight: 600;
  color: var(--solo-text-secondary);
  background: var(--solo-bg-card);
  border-bottom: 1px solid var(--solo-border);
  white-space: nowrap;
}
.balance-table td {
  padding: 7px 16px;
  border-bottom: 1px solid var(--solo-border-light);
  vertical-align: middle;
}
.balance-table tbody tr:hover {
  background: var(--solo-bg-page);
}
.balance-table tbody tr.row-filled {
  background: var(--solo-bg-blue-soft);
}
.balance-table tbody tr.row-filled .acct-name-text {
  font-weight: 600;
}
.balance-table tbody tr.row-filled .amount-input :deep(.n-input__input-el) {
  font-weight: 600;
  color: var(--solo-text-primary);
}
.balance-table tfoot td {
  border-top: 2px solid var(--solo-border);
  font-weight: 600;
  padding: 12px 16px;
  background: var(--solo-bg-page);
}

.col-code {
  width: 110px;
  font-variant-numeric: tabular-nums;
  font-weight: 500;
  color: var(--solo-text-secondary);
}
.col-code-orig {
  display: block;
  margin-top: 2px;
  font-size: 11px;
  font-weight: 400;
  color: var(--solo-text-muted);
}
.col-name {
  min-width: 200px;
}
.acct-name-text {
  color: var(--solo-text-primary);
  font-weight: 500;
}
.acct-cat-tag {
  margin-left: 8px;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: var(--solo-radius-sm);
  background: var(--solo-bg-soft);
  color: var(--solo-text-muted);
}
.col-dir {
  width: 84px;
}
.dir-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: var(--solo-radius-pill);
  font-size: 11px;
  font-weight: 600;
}
.dir-debit {
  background: var(--solo-bg-blue-soft);
  color: var(--solo-primary);
}
.dir-credit {
  background: var(--solo-warning-bg);
  color: var(--solo-warning);
}
.col-partner {
  width: 240px;
}
.no-partner {
  /* 占位破折号不是装饰性的空，它表示"这一行没有辅助核算项" */
  color: var(--solo-text-muted);
  font-size: 12px;
}
.col-amount {
  width: 175px;
  text-align: right !important;
}
.col-total {
  text-align: right;
  padding-right: 16px;
  font-size: 13px;
  color: var(--solo-text-secondary);
}
.total {
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-primary);
}

.amount-input {
  width: 100%;
}
.amount-input :deep(.n-input__input-el) {
  text-align: right !important;
  font-variant-numeric: tabular-nums;
}
.amount-input :deep(.n-input__placeholder) {
  text-align: right;
  color: var(--solo-text-muted);
  opacity: 0.55;
}

.empty-table-cell {
  text-align: center;
  padding: 36px 16px !important;
  color: var(--solo-text-muted);
  font-size: 13px;
}
.empty-table-cell i {
  font-size: 20px;
  vertical-align: middle;
  margin-right: 6px;
}

/* 期初对账（只读） */
.recon-warning {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  margin: 14px 20px 0;
  padding: 10px 12px;
  border-radius: var(--solo-radius);
  border: 1px solid var(--solo-border);
  background: var(--solo-warning-bg);
  color: var(--solo-warning);
  font-size: 13px;
  line-height: 1.5;
}
.recon-summary {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  padding: 14px 20px;
}
.recon-card {
  flex: 1 1 210px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 14px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius);
  background: var(--solo-bg-soft);
}
.recon-label {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.recon-value {
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary);
  font-variant-numeric: tabular-nums;
}
.recon-state {
  font-size: 12px;
  font-weight: 500;
}
.recon-state.ok {
  color: var(--solo-income);
}
.recon-state.diff {
  color: var(--solo-expense);
}
.recon-amount {
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-primary);
}
/* 八列并排时压缩金额列宽，避免横向滚动把「期初贷方」挤出卡片 */
.recon-table .col-amount {
  width: 118px;
}
.recon-table .col-name {
  min-width: 170px;
}

.step-up-note {
  color: var(--solo-text-secondary);
  font-size: 12px;
  line-height: 1.6;
  margin: 4px 0 16px;
}

.subtitle.period-hint {
  color: var(--solo-text-muted);
  font-size: 12px;
  margin-top: 2px;
}

/* 导入结果弹窗 */
.import-period-gate {
  display: flex;
  flex-direction: column;
  gap: 12px;
  font-size: 13px;
}
.period-meta {
  color: var(--solo-text-secondary);
}
.period-cutoff-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.period-help {
  margin: 0;
  font-size: 12px;
  line-height: 1.6;
  color: var(--solo-text-muted);
}
.import-report-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.import-kpi-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 10px;
}
.import-kpi-card {
  background: var(--solo-bg-page);
  border: 1px solid var(--solo-border-light);
  border-radius: var(--solo-radius);
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  font-variant-numeric: tabular-nums;
}
.kpi-label {
  font-size: 11px;
  color: var(--solo-text-muted);
}
.kpi-value {
  font-size: 15px;
  font-weight: 700;
  color: var(--solo-text-primary);
}
.kpi-sub {
  font-size: 11px;
  color: var(--solo-text-secondary);
}
.import-modal-tabs {
  display: flex;
  align-items: center;
  gap: 8px;
  border-bottom: 1px solid var(--solo-border-light);
  padding-bottom: 8px;
}
.import-modal-tab {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border: 1px solid transparent;
  border-radius: var(--solo-radius);
  background: transparent;
  color: var(--solo-text-secondary);
  font-size: 13px;
  font-weight: 500;
  cursor: pointer;
  transition: background-color 0.15s ease, color 0.15s ease, border-color 0.15s ease;
}
.import-modal-tab:hover {
  background: var(--solo-bg-page);
  color: var(--solo-text-primary);
}
.import-modal-tab.active {
  background: var(--solo-bg-blue-soft);
  border-color: var(--solo-primary);
  color: var(--solo-primary);
  font-weight: 600;
}
.tab-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  min-width: 20px;
  padding: 0 6px;
  border-radius: var(--solo-radius-pill);
  font-size: 11px;
  background: var(--solo-bg-page);
  color: var(--solo-text-secondary);
}
.tab-badge.tone-warning {
  background: var(--solo-warning-bg);
  color: var(--solo-warning);
}
.import-preview-section {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.warnings-box {
  font-size: 12px;
  padding: 8px 12px;
  border-radius: var(--solo-radius);
  background: var(--solo-warning-bg);
}
.warning-title {
  color: var(--solo-text-secondary);
  font-weight: 600;
}
.warning-list {
  margin: 6px 0 0;
  padding-left: 18px;
  color: var(--solo-text-muted);
  line-height: 1.6;
}

.mapping-box {
  padding: 12px 14px;
  background: var(--solo-bg-page);
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.mapping-box-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  flex-wrap: wrap;
}
.mapping-tip {
  font-size: 11.5px;
  color: var(--solo-text-muted);
}
.mapping-preview-pill {
  padding: 3px 10px;
  border-radius: var(--solo-radius-pill);
  font-size: 11.5px;
  font-weight: 600;
  background: var(--solo-warning-bg);
  color: var(--solo-warning);
  font-variant-numeric: tabular-nums;
}
.mapping-preview-pill.balanced {
  background: var(--solo-income-bg);
  color: var(--solo-income);
}
.mapping-list {
  max-height: 36vh;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding-right: 4px;
}
.mapping-group {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.mapping-group-title {
  font-size: 12px;
  font-weight: 600;
  color: var(--solo-text-secondary);
  padding-bottom: 4px;
  border-bottom: 1px dashed var(--solo-border-light);
}
.mapping-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 8px;
  border-radius: var(--solo-radius-sm);
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-light);
  font-size: 12px;
}
.mapping-item-highlighted {
  border-color: var(--solo-primary);
  background: var(--solo-bg-blue-soft);
}
.mapping-source {
  flex: 1;
  min-width: 0;
  display: flex;
  align-items: center;
  gap: 6px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.source-code {
  font-family: var(--solo-font-mono);
  font-weight: 600;
  color: var(--solo-text-primary);
}
.source-name {
  color: var(--solo-text-secondary);
  overflow: hidden;
  text-overflow: ellipsis;
}
.source-amount {
  color: var(--solo-text-muted);
  font-size: 11px;
  flex-shrink: 0;
}
.mapping-badge {
  padding: 1px 6px;
  border-radius: 4px;
  font-size: 11px;
  white-space: nowrap;
  flex-shrink: 0;
}
.mapping-badge.tone-info {
  background: var(--solo-bg-blue-soft);
  color: var(--solo-primary);
}
.mapping-badge.tone-warning {
  background: var(--solo-warning-bg);
  color: var(--solo-warning);
}
.mapping-badge.tone-success {
  background: var(--solo-income-bg);
  color: var(--solo-income);
}
.mapping-arrow {
  color: var(--solo-text-muted);
  font-size: 14px;
  flex-shrink: 0;
}
@media (max-width: 760px) {
  .import-kpi-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
</style>
