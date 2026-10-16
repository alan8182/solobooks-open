<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import {
  NButton, NTag, NSpace, useMessage, NInput, NSpin, NSelect, NModal, NFormItem,
  NAlert, NInputNumber, NSwitch,
} from 'naive-ui'
import * as XLSX from 'xlsx'
import apiClient from '@/api/client'
import { getAuthMe, requestStepUp, sendPhoneCode } from '@/api/auth'
import {
  exportOpeningTemplate,
  parseOpeningWorkbook,
  type FixedAssetImportItem,
} from '@/utils/openingBalanceImport'

interface BalanceItem {
  id?: string
  company_id?: string
  account_code: string
  account_name?: string
  debit_balance: number
  credit_balance: number
  partner_name?: string
  fiscal_year?: number
  voucherStatus?: string | null
  voucherRef?: string | null
}

interface TaxOpeningMemory {
  priorYearLoss: number
  prepaidCitTax: number
  migrationNote: string
}

type CategoryFilter = 'all' | 'asset' | 'liability' | 'equity'
type YtdCategoryFilter = 'all' | 'revenue' | 'expense'
type ActiveSheetTab = 'balance' | 'ytd'
type StepUpMode = 'save' | 'reset'

const message = useMessage()

const loading = ref(false)
const saving = ref(false)
const items = ref<BalanceItem[]>([])
const ytdItems = ref<BalanceItem[]>([])
const fixedAssets = ref<FixedAssetImportItem[]>([])
const fiscalYear = ref(new Date().getFullYear())
const startMonth = ref('01')
const voucherNo = ref<string | null>(null)
const serverLocked = ref(false)

// 筛选与视图状态
const activeTab = ref<ActiveSheetTab>('balance')
const categoryFilter = ref<CategoryFilter>('all')
const ytdCategoryFilter = ref<YtdCategoryFilter>('all')
const searchQuery = ref('')
const onlyFilled = ref(false)
const taxPanelExpanded = ref(false)

// 短信二次验证与冲销重置状态
const stepUpVisible = ref(false)
const stepUpMode = ref<StepUpMode>('save')
const resetReason = ref('期初余额核对重设')
const phone = ref('')
const code = ref('')
const bizId = ref('')
const sendingCode = ref(false)

const isLocked = computed(() =>
  serverLocked.value || items.value.some(i => ['posted', 'reversed'].includes(i.voucherStatus || ''))
)

const startPeriod = computed(() => `${fiscalYear.value}-${startMonth.value}`)
const startMonthNum = computed(() => parseInt(startMonth.value, 10) || 1)
const isMidYear = computed(() => startMonthNum.value > 1)

// 税务期初与接续备忘
const taxMemory = ref<TaxOpeningMemory>({
  priorYearLoss: 0,
  prepaidCitTax: 0,
  migrationNote: '',
})

function getTaxMemoryStorageKey(year: number) {
  return `solo_tax_opening_memory_${year}`
}

function loadLocalTaxMemoryFallback(year: number) {
  try {
    const raw = localStorage.getItem(getTaxMemoryStorageKey(year))
    if (raw) {
      const parsed = JSON.parse(raw)
      taxMemory.value = {
        priorYearLoss: Number(parsed.priorYearLoss) || 0,
        prepaidCitTax: Number(parsed.prepaidCitTax) || 0,
        migrationNote: String(parsed.migrationNote || ''),
      }
    }
  } catch {
    // 忽略本地存储格式异常
  }
}

function saveLocalTaxMemory(year: number) {
  try {
    localStorage.setItem(getTaxMemoryStorageKey(year), JSON.stringify(taxMemory.value))
  } catch {
    // 忽略异常
  }
}

watch(startMonth, (m) => {
  if (m === '01' && activeTab.value === 'ytd') {
    activeTab.value = 'balance'
  }
})

const expenseRevenueCodes = new Set([
  '5001', '5051', '5111', '5301', '5401', '5401001', '5401002', '5402', '5403',
  '5601', '5601001', '5601006', '5601011', '5601015', '5601016',
  '5602', '5602001', '5602002', '5602004', '5602005', '5602006', '5602007',
  '5602011', '5602013', '5602014', '5602015', '5602999',
  '5603', '5603001', '5603002', '5603003', '5603004', '5603005',
  '5711', '5801', '4301',
])

// 支持挂接往来客商辅助核算的科目
const partnerAccountCodes = new Set([
  '1121', '1122', '1123', '1221',
  '2201', '2202', '2203', '2241',
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
  return partnerAccountCodes.has(code)
}

function isBalanceAccount(code: string): boolean {
  return !expenseRevenueCodes.has(code) && !code.startsWith('5')
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

const filteredBalanceItems = computed(() => {
  const q = searchQuery.value.trim().toLowerCase()
  return balanceItems.value.filter(item => {
    if (categoryFilter.value !== 'all' && getAccountCategory(item.account_code) !== categoryFilter.value) {
      return false
    }
    if (onlyFilled.value && !(item.debit_balance > 0 || item.credit_balance > 0)) {
      return false
    }
    if (q) {
      const codeMatch = item.account_code.toLowerCase().includes(q)
      const nameMatch = (item.account_name || '').toLowerCase().includes(q)
      const partnerMatch = (item.partner_name || '').toLowerCase().includes(q)
      if (!codeMatch && !nameMatch && !partnerMatch) return false
    }
    return true
  })
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

const hasTaxMemoryConfigured = computed(() =>
  (taxMemory.value.priorYearLoss || 0) > 0 ||
  (taxMemory.value.prepaidCitTax || 0) > 0 ||
  Boolean(taxMemory.value.migrationNote?.trim())
)

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

async function onFileSelected(e: Event) {
  const target = e.target as HTMLInputElement
  const file = target.files?.[0]
  target.value = ''
  if (!file) return

  loading.value = true
  try {
    const data = await file.arrayBuffer()
    const workbook = XLSX.read(data, { type: 'array' })
    const allAccounts = [...balanceItems.value, ...ytdItems.value]
    const parseResult = parseOpeningWorkbook(workbook, allAccounts)

    // 1. 写入资产负债期初明细
    const balanceMap = new Map(parseResult.items.map(i => [i.account_code, i]))
    items.value = items.value.map(item => {
      const parsed = balanceMap.get(item.account_code)
      if (parsed && (parsed.debit_balance > 0 || parsed.credit_balance > 0)) {
        return {
          ...item,
          debit_balance: parsed.debit_balance,
          credit_balance: parsed.credit_balance,
          partner_name: parsed.partner_name || item.partner_name || '',
        }
      }
      return item
    })

    // 2. 写入年中损益本年累计发生额
    let ytdMatched = 0
    const ytdMap = new Map(parseResult.ytdItems.map(i => [i.account_code, i]))
    ytdItems.value = ytdItems.value.map(item => {
      const parsed = ytdMap.get(item.account_code)
      if (parsed && (parsed.debit_balance > 0 || parsed.credit_balance > 0)) {
        ytdMatched++
        return {
          ...item,
          debit_balance: parsed.debit_balance,
          credit_balance: parsed.credit_balance,
        }
      }
      return item
    })

    // 3. 写入固定资产期初卡片
    if (parseResult.fixedAssets && parseResult.fixedAssets.length > 0) {
      fixedAssets.value = parseResult.fixedAssets
    }

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
    importModalVisible.value = true

    if (parseResult.isBalanced) {
      message.success(`成功导入 ${parseResult.matchedCount} 个科目，借贷双方已平衡`)
    } else {
      message.warning(`导入完成，尚有借贷差额 ¥${Math.abs(parseResult.totalDebit - parseResult.totalCredit).toFixed(2)}，请核对调整`)
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : 'Excel 解析失败，请检查文件格式')
  } finally {
    loading.value = false
  }
}

function normalizeRowNumbers(rawList: any[]): BalanceItem[] {
  return (rawList || []).map((row: any) => ({
    ...row,
    debit_balance: parseFloat(String(row.debit_balance ?? 0)) || 0,
    credit_balance: parseFloat(String(row.credit_balance ?? 0)) || 0,
    partner_name: String(row.partner_name || ''),
  }))
}

async function load() {
  loading.value = true
  try {
    const data: any = await apiClient.get('/api/opening/balances', {
      params: { fiscal_year: fiscalYear.value },
    })
    items.value = normalizeRowNumbers(data.items || [])
    ytdItems.value = normalizeRowNumbers(data.ytd_items || [])
    serverLocked.value = Boolean(data.is_locked)
    voucherNo.value = data.voucher_no || items.value.find(i => i.voucherRef)?.voucherRef || null

    if (typeof data.start_period === 'string' && data.start_period.includes('-')) {
      const parts = data.start_period.split('-')
      if (parts[1]) {
        startMonth.value = parts[1].padStart(2, '0')
      }
    } else {
      startMonth.value = '01'
    }

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

    if (data.tax_memory && typeof data.tax_memory === 'object') {
      const tm = data.tax_memory
      const hasServerTax =
        Number(tm.priorYearLoss || tm.prior_year_loss || 0) > 0 ||
        Number(tm.prepaidCitTax || tm.prepaid_cit_tax || 0) > 0 ||
        Boolean(tm.migrationNote || tm.migration_note)
      if (hasServerTax) {
        taxMemory.value = {
          priorYearLoss: Number(tm.priorYearLoss ?? tm.prior_year_loss ?? 0) || 0,
          prepaidCitTax: Number(tm.prepaidCitTax ?? tm.prepaid_cit_tax ?? 0) || 0,
          migrationNote: String(tm.migrationNote ?? tm.migration_note ?? ''),
        }
      } else {
        loadLocalTaxMemoryFallback(fiscalYear.value)
      }
    } else {
      loadLocalTaxMemoryFallback(fiscalYear.value)
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
  saveLocalTaxMemory(fiscalYear.value)
  stepUpMode.value = 'save'
  stepUpVisible.value = true
}

function openResetModal() {
  stepUpMode.value = 'reset'
  stepUpVisible.value = true
}

async function sendCode() {
  if (!phone.value) {
    message.warning('请输入当前账号已绑定的手机号')
    return
  }
  sendingCode.value = true
  try {
    const result = await sendPhoneCode({ phone: phone.value })
    bizId.value = result.biz_id || ''
    message.success('验证码已发送')
  } catch {
    message.error('验证码发送失败')
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
        tax_memory: {
          prior_year_loss: Number(taxMemory.value.priorYearLoss) || 0,
          prepaid_cit_tax: Number(taxMemory.value.prepaidCitTax) || 0,
          migration_note: taxMemory.value.migrationNote?.trim() || '',
        },
        fixed_assets: fixedAssets.value.length > 0 ? fixedAssets.value : undefined,
      },
      {
        headers: {
          'Step-Up-Token': auth.stepUpToken,
          'Idempotency-Key': `opening-${fiscalYear.value}-${startMonth.value}-${Date.now()}`,
        },
      }
    )
    saveLocalTaxMemory(fiscalYear.value)
    stepUpVisible.value = false
    code.value = ''
    message.success('期初凭证已生成并过账锁定')
    await load()
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : detail?.message || '操作失败，请稍后重试')
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

onMounted(async () => {
  await load()
  try {
    const profile: any = await getAuthMe()
    phone.value = profile?.user?.phone || ''
  } catch {
    // 绑定手机号会在提交时由后端再次校验
  }
})
</script>

<template>
  <div class="opening-page">
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
          <p class="subtitle">
            将原账套或手工账的科目余额迁入系统，借贷试算平衡后生成不可篡改的系统期初凭证（《小企业会计准则》）。
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
          <NButton size="medium" secondary @click="handleDownloadTemplate">
            <template #icon><i class="ri-download-2-line" /></template>
            下载模板
          </NButton>
          <NButton size="medium" secondary :disabled="isLocked" @click="triggerUpload">
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
            type="primary"
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
          <span class="metric-label">资产负债借方合计</span>
          <span class="metric-value">¥ {{ formatCurrency(totalDebit) }}</span>
        </div>
        <div class="metric-divider" />
        <div class="metric-item">
          <span class="metric-label">资产负债贷方合计</span>
          <span class="metric-value">¥ {{ formatCurrency(totalCredit) }}</span>
        </div>
        <div class="metric-divider" />
        <div class="metric-item">
          <span class="metric-label">借贷试算差额</span>
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
            <span class="metric-label">1-{{ startMonthNum - 1 }}月累计净损益（自动转入 3103）</span>
            <span
              class="metric-value"
              :class="ytdNetProfit >= 0 ? 'text-income' : 'text-expense'"
            >
              {{ ytdNetProfit >= 0 ? '+' : '' }}¥ {{ formatCurrency(ytdNetProfit) }}
            </span>
          </div>
        </template>
      </div>

      <div class="summary-verdict">
        <span v-if="isLocked" class="verdict-badge badge-locked">
          <i class="ri-shield-check-line" />
          期初已过账锁定
        </span>
        <span v-else-if="isBalanced" class="verdict-badge badge-balanced">
          <i class="ri-checkbox-circle-line" />
          借贷平衡 · 满足建账标准
        </span>
        <span v-else-if="totalDebit === 0 && totalCredit === 0" class="verdict-badge badge-empty">
          <i class="ri-information-line" />
          待录入或导入期初余额
        </span>
        <span v-else class="verdict-badge badge-unbalanced">
          <i class="ri-error-warning-line" />
          借贷未平 · 差额 ¥{{ formatCurrency(Math.abs(balanceDiff)) }}
        </span>
      </div>
    </div>

    <!-- 税务期初与申报衔接备忘（可折叠工作台卡片） -->
    <div class="surface-card tax-memory-section">
      <button
        type="button"
        class="tax-header-toggle"
        @click="taxPanelExpanded = !taxPanelExpanded"
      >
        <div class="tax-header-left">
          <i class="ri-bank-line tax-icon" />
          <div>
            <span class="tax-title">税务期初与申报衔接备忘</span>
            <span class="tax-subtitle">以前年度可弥补亏损 · 本年已预缴企业所得税 · 换账套抵扣依据</span>
          </div>
        </div>
        <div class="tax-header-right">
          <span v-if="hasTaxMemoryConfigured" class="tax-status-chip chip-active">
            <i class="ri-check-line" />
            已配置抵扣备忘
          </span>
          <span v-else class="tax-status-chip">选填</span>
          <i :class="taxPanelExpanded ? 'ri-arrow-up-s-line' : 'ri-arrow-down-s-line'" class="collapse-arrow" />
        </div>
      </button>

      <div v-show="taxPanelExpanded" class="tax-body">
        <div class="tax-memory-grid">
          <div class="tax-field">
            <label class="field-title">以前年度可弥补亏损额（元）</label>
            <NInputNumber
              v-model:value="taxMemory.priorYearLoss"
              :disabled="isLocked"
              :min="0"
              :precision="2"
              :show-button="false"
              placeholder="0.00"
              size="medium"
              @update:value="saveLocalTaxMemory(fiscalYear)"
            >
              <template #prefix>¥</template>
            </NInputNumber>
            <span class="field-desc">用于企业所得税预缴及汇算清缴时抵扣应纳税所得额（A200000 第 24 行）。</span>
          </div>

          <div class="tax-field">
            <label class="field-title">本年已预缴企业所得税额（元）</label>
            <NInputNumber
              v-model:value="taxMemory.prepaidCitTax"
              :disabled="isLocked"
              :min="0"
              :precision="2"
              :show-button="false"
              placeholder="0.00"
              size="medium"
              @update:value="saveLocalTaxMemory(fiscalYear)"
            >
              <template #prefix>¥</template>
            </NInputNumber>
            <span class="field-desc">年中迁入时前序季度已在税务局实际预缴的所得税额（A200000 第 30 行）。</span>
          </div>

          <div class="tax-field">
            <label class="field-title">账套迁入备忘说明（选填）</label>
            <NInput
              v-model:value="taxMemory.migrationNote"
              :disabled="isLocked"
              placeholder="例如：2026年6月由代账公司/金蝶云迁入"
              size="medium"
              @update:value="saveLocalTaxMemory(fiscalYear)"
            />
            <span class="field-desc">随期初配置一并持久化至企业档案，供后续结账与审计备查。</span>
          </div>
        </div>
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
              <span>损益本年累计发生额（1-{{ Math.max(1, startMonthNum - 1) }}月）</span>
              <span class="tab-count">{{ filledYtdCount }} / {{ ytdItems.length }}</span>
            </button>
          </div>

          <div class="toolbar-right">
            <NInput
              v-model:value="searchQuery"
              placeholder="搜索科目代码、名称或客商..."
              clearable
              size="small"
              style="width: 220px"
            >
              <template #prefix><i class="ri-search-line" /></template>
            </NInput>

            <label class="only-filled-toggle">
              <NSwitch v-model:value="onlyFilled" size="small" />
              <span>仅看已填项</span>
            </label>
          </div>
        </div>

        <!-- 二级分类筛选胶囊栏 -->
        <div class="sub-filter-bar">
          <template v-if="activeTab === 'balance'">
            <div class="category-pills">
              <button
                type="button"
                class="pill-btn"
                :class="{ active: categoryFilter === 'all' }"
                @click="categoryFilter = 'all'"
              >
                全部（{{ balanceItems.length }}）
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
              同行借贷自动互斥；应收/应付等往来科目可填写客商名称
            </span>
          </template>

          <template v-else>
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
              <i class="ri-sparkling-line" />
              年中建账保存时，系统自动将 1-{{ Math.max(1, startMonthNum - 1) }} 月累计损益差额配平至「3103 本年利润」
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
                <th class="col-amount">期初借方余额（元）</th>
                <th class="col-amount">期初贷方余额（元）</th>
              </tr>
            </thead>
            <tbody v-if="filteredBalanceItems.length > 0">
              <tr
                v-for="item in filteredBalanceItems"
                :key="item.account_code"
                :class="{ 'row-filled': (item.debit_balance || 0) > 0 || (item.credit_balance || 0) > 0 }"
              >
                <td class="col-code">{{ item.account_code }}</td>
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
                <td class="col-partner">
                  <NInput
                    v-if="supportsPartner(item.account_code)"
                    v-model:value="item.partner_name"
                    :disabled="isLocked"
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
        <div v-else class="table-container">
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
                <td class="col-code">{{ item.account_code }}</td>
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
      </div>
    </NSpin>

    <!-- 导入诊断反馈弹窗 -->
    <NModal
      v-model:show="importModalVisible"
      preset="card"
      title="Excel 科目余额表导入结果"
      style="max-width: 520px"
    >
      <div v-if="importSummary" class="import-report-content">
        <NAlert :type="importSummary.isBalanced ? 'success' : 'warning'" :bordered="false">
          {{ importSummary.isBalanced ? '导入成功且借贷已平衡！核对无误后即可生成期初凭证。' : '数据已填入，但资产负债期初尚有借贷差额，请在表格中核对调整。' }}
        </NAlert>

        <div class="import-stats-box">
          <div class="stat-line">
            <span>成功匹配并填入科目：</span>
            <strong>{{ importSummary.matchedCount }} 个</strong>
          </div>
          <div v-if="importSummary.ytdMatchedCount > 0" class="stat-line">
            <span>其中年中损益累计科目：</span>
            <strong>{{ importSummary.ytdMatchedCount }} 个</strong>
          </div>
          <div v-if="importSummary.fixedAssetCount > 0" class="stat-line">
            <span>成功解析固定资产卡片：</span>
            <strong>{{ importSummary.fixedAssetCount }} 张</strong>
          </div>
          <div class="stat-line">
            <span>期初借方导入合计：</span>
            <strong>¥ {{ formatCurrency(importSummary.totalDebit) }}</strong>
          </div>
          <div class="stat-line">
            <span>期初贷方导入合计：</span>
            <strong>¥ {{ formatCurrency(importSummary.totalCredit) }}</strong>
          </div>
          <div v-if="!importSummary.isBalanced" class="stat-line">
            <span>未平差额：</span>
            <strong class="text-expense">¥ {{ formatCurrency(Math.abs(importSummary.totalDebit - importSummary.totalCredit)) }}</strong>
          </div>
        </div>

        <div v-if="importSummary.warnings.length" class="warnings-box">
          <span class="warning-title">未匹配或跳过项（{{ importSummary.warnings.length }}）：</span>
          <ul class="warning-list">
            <li v-for="w in importSummary.warnings.slice(0, 5)" :key="w">{{ w }}</li>
            <li v-if="importSummary.warnings.length > 5">...其余 {{ importSummary.warnings.length - 5 }} 项已略过</li>
          </ul>
        </div>
      </div>
      <template #footer>
        <NSpace justify="end">
          <NButton type="primary" @click="importModalVisible = false">完成核对</NButton>
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
</template>

<style scoped>
.opening-page {
  max-width: 1200px;
  margin: 0 auto;
  padding-bottom: 40px;
  width: 100%;
  box-sizing: border-box;
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
  background: #eaf7f1;
  color: #12805c;
}
.pill-midyear {
  background: #edf3ff;
  color: #165dff;
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
  background: #dde3ea;
  margin: 0 2px;
}
.action-buttons {
  display: flex;
  align-items: center;
  gap: 8px;
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
  border-radius: 14px;
  margin-bottom: 16px;
  background: #ffffff;
  border: 1px solid #dde3ea;
  box-shadow: 0 4px 16px rgba(24, 34, 48, 0.05);
  transition: border-color 0.2s ease, background-color 0.2s ease;
}
.balance-summary-bar.is-balanced,
.balance-summary-bar.is-locked {
  border-color: rgba(18, 128, 92, 0.35);
  background: #fcfefd;
}
.balance-summary-bar.is-unbalanced {
  border-color: rgba(199, 68, 68, 0.38);
  background: #fffcfc;
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
  color: var(--solo-text-secondary, #586474);
}
.metric-value {
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-primary, #182230);
}
.diff-side-hint {
  font-size: 12px;
  font-weight: 500;
}
.metric-divider {
  width: 1px;
  height: 28px;
  background: #edf0f4;
}

.text-income {
  color: #12805c !important;
}
.text-expense {
  color: #c74444 !important;
}

.verdict-badge {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
}
.badge-balanced,
.badge-locked {
  background: #eaf7f1;
  color: #12805c;
}
.badge-unbalanced {
  background: #fbeeee;
  color: #c74444;
}
.badge-empty {
  background: #f1f4f8;
  color: #586474;
}

/* 通用平面卡片（遵循 DESIGN.md Flat-at-Rest 规范） */
.surface-card {
  background: #ffffff;
  border: 1px solid #dde3ea;
  border-radius: 14px;
  margin-bottom: 16px;
}

/* 税务期初备忘折叠卡 */
.tax-header-toggle {
  width: 100%;
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 20px;
  border: none;
  background: transparent;
  cursor: pointer;
  text-align: left;
}
.tax-header-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.tax-icon {
  font-size: 18px;
  color: #165dff;
  background: #edf3ff;
  width: 34px;
  height: 34px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.tax-title {
  display: block;
  font-size: 14px;
  font-weight: 600;
  color: #182230;
}
.tax-subtitle {
  display: block;
  font-size: 12px;
  color: #8a95a3;
  margin-top: 2px;
}
.tax-header-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.tax-status-chip {
  font-size: 12px;
  padding: 3px 8px;
  border-radius: 999px;
  background: #f1f4f8;
  color: #586474;
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.tax-status-chip.chip-active {
  background: #edf3ff;
  color: #165dff;
}
.collapse-arrow {
  font-size: 18px;
  color: #8a95a3;
}
.tax-body {
  padding: 4px 20px 18px;
  border-top: 1px solid #edf0f4;
}
.tax-memory-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 16px;
  margin-top: 12px;
}
@media (max-width: 900px) {
  .tax-memory-grid {
    grid-template-columns: 1fr;
  }
}
.tax-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.field-title {
  font-size: 13px;
  font-weight: 600;
  color: #182230;
}
.field-desc {
  font-size: 12px;
  color: #8a95a3;
  line-height: 1.45;
}

/* 固定资产卡片对账提示条 */
.fixed-asset-notice-card {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 20px;
  background: #fbfcfe;
  border-left: 3px solid #165dff;
}
.fa-notice-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.fa-notice-icon {
  font-size: 18px;
  color: #165dff;
  background: #edf3ff;
  width: 32px;
  height: 32px;
  border-radius: 8px;
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
  color: #182230;
  display: flex;
  align-items: center;
}
.fa-notice-desc {
  font-size: 12px;
  color: #586474;
}
.fa-status-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 500;
}
.fa-status-pill.pill-match {
  background: #eaf7f1;
  color: #12805c;
}
.fa-status-pill.pill-mismatch {
  background: #fbeeee;
  color: #c74444;
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
  border-bottom: 1px solid #edf0f4;
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
  border-radius: 8px;
  border: 1px solid transparent;
  background: #f1f4f8;
  color: #586474;
  font-size: 13px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.15s ease;
}
.sheet-tab-btn.active {
  background: #edf3ff;
  color: #165dff;
  border-color: rgba(22, 93, 255, 0.25);
}
.tab-count {
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 999px;
  background: rgba(24, 34, 48, 0.06);
  font-variant-numeric: tabular-nums;
}
.sheet-tab-btn.active .tab-count {
  background: rgba(22, 93, 255, 0.14);
  color: #165dff;
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
  color: #586474;
  cursor: pointer;
  user-select: none;
}

.sub-filter-bar {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  padding: 10px 20px;
  background: #f6f7f9;
  border-bottom: 1px solid #edf0f4;
  flex-wrap: wrap;
}
.category-pills {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.pill-btn {
  padding: 4px 12px;
  border-radius: 999px;
  border: 1px solid transparent;
  background: transparent;
  color: #586474;
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  transition: all 0.15s ease;
}
.pill-btn:hover {
  background: #ffffff;
  color: #182230;
}
.pill-btn.active {
  background: #ffffff;
  color: #165dff;
  border-color: #dde3ea;
  font-weight: 600;
}
.rule-hint {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: #8a95a3;
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
  color: #586474;
  background: #ffffff;
  border-bottom: 1px solid #dde3ea;
  white-space: nowrap;
}
.balance-table td {
  padding: 7px 16px;
  border-bottom: 1px solid #edf0f4;
  vertical-align: middle;
}
.balance-table tbody tr:hover {
  background: #f6f7f9;
}
.balance-table tbody tr.row-filled {
  background: rgba(22, 93, 255, 0.025);
}
.balance-table tfoot td {
  border-top: 2px solid #dde3ea;
  font-weight: 600;
  padding: 12px 16px;
  background: #f6f7f9;
}

.col-code {
  width: 110px;
  font-variant-numeric: tabular-nums;
  font-weight: 500;
  color: #586474;
}
.col-name {
  min-width: 200px;
}
.acct-name-text {
  color: #182230;
  font-weight: 500;
}
.acct-cat-tag {
  margin-left: 8px;
  font-size: 11px;
  padding: 1px 6px;
  border-radius: 4px;
  background: #f1f4f8;
  color: #8a95a3;
}
.col-dir {
  width: 90px;
}
.dir-badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 600;
}
.dir-debit {
  background: #edf3ff;
  color: #165dff;
}
.dir-credit {
  background: #fff5e8;
  color: #b46b17;
}
.col-partner {
  width: 190px;
}
.no-partner {
  color: #c9d1d9;
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
  color: #586474;
}
.total {
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: #182230;
}

.amount-input {
  width: 100%;
}
.amount-input :deep(.n-input__input-el) {
  text-align: right !important;
  font-variant-numeric: tabular-nums;
}

.empty-table-cell {
  text-align: center;
  padding: 36px 16px !important;
  color: #8a95a3;
  font-size: 13px;
}
.empty-table-cell i {
  font-size: 20px;
  vertical-align: middle;
  margin-right: 6px;
}

.step-up-note {
  color: #586474;
  font-size: 12px;
  line-height: 1.6;
  margin: 4px 0 16px;
}

/* 导入结果弹窗 */
.import-report-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.import-stats-box {
  background: #f6f7f9;
  padding: 12px 14px;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
  font-variant-numeric: tabular-nums;
}
.stat-line {
  display: flex;
  justify-content: space-between;
}
.warnings-box {
  font-size: 12px;
}
.warning-title {
  color: #586474;
  font-weight: 600;
}
.warning-list {
  margin: 6px 0 0;
  padding-left: 18px;
  color: #8a95a3;
  line-height: 1.6;
}
</style>
