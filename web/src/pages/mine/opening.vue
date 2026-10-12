<script setup lang="ts">
import { ref, computed, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import {
  NCard, NButton, NTag, NSpace, useMessage, NInput, NSpin, NSelect, NModal, NFormItem,
  NAlert, NInputNumber, NCollapse, NCollapseItem,
} from 'naive-ui'
import * as XLSX from 'xlsx'
import apiClient from '@/api/client'
import { getAuthMe, requestStepUp, sendPhoneCode } from '@/api/auth'
import { exportOpeningTemplate, parseOpeningRows } from '@/utils/openingBalanceImport'

interface BalanceItem {
  id?: string
  company_id?: string
  account_code: string
  account_name?: string
  debit_balance: number
  credit_balance: number
  fiscal_year: number
  voucherStatus?: string | null
  voucherRef?: string | null
}

interface TaxOpeningMemory {
  priorYearLoss: number
  prepaidCitTax: number
  migrationNote: string
}

const router = useRouter()
const message = useMessage()

const loading = ref(false)
const saving = ref(false)
const items = ref<BalanceItem[]>([])
const fiscalYear = ref(new Date().getFullYear())
const stepUpVisible = ref(false)
const phone = ref('')
const code = ref('')
const bizId = ref('')
const sendingCode = ref(false)
const isLocked = computed(() => items.value.some(i => ['posted', 'reversed'].includes(i.voucherStatus || '')))

// 税务期初与接续备忘
const taxMemory = ref<TaxOpeningMemory>({
  priorYearLoss: 0,
  prepaidCitTax: 0,
  migrationNote: '',
})

function getTaxMemoryStorageKey(year: number) {
  return `solo_tax_opening_memory_${year}`
}

function loadTaxMemory(year: number) {
  try {
    const raw = localStorage.getItem(getTaxMemoryStorageKey(year))
    if (raw) {
      taxMemory.value = { ...taxMemory.value, ...JSON.parse(raw) }
    } else {
      taxMemory.value = { priorYearLoss: 0, prepaidCitTax: 0, migrationNote: '' }
    }
  } catch {
    // 忽略本地存储格式异常
  }
}

function saveTaxMemory(year: number) {
  try {
    localStorage.setItem(getTaxMemoryStorageKey(year), JSON.stringify(taxMemory.value))
  } catch {
    // 忽略异常
  }
}

watch(fiscalYear, (newYear) => {
  loadTaxMemory(newYear)
})

const expenseAssetCodes = new Set([
  '5001', '5601', '5602', '5603', '5604', '5605', '5606', '5607', '5608', '5609',
  '5699', '6601', '6401', '6711', '6001', '6051', '6301',
])

function isBalanceAccount(code: string): boolean {
  return !expenseAssetCodes.has(code)
}

const balanceItems = computed(() => items.value.filter(i => isBalanceAccount(i.account_code)))

const totalDebit = computed(() =>
  Math.round(balanceItems.value.reduce((s, i) => s + (i.debit_balance || 0), 0) * 100) / 100
)
const totalCredit = computed(() =>
  Math.round(balanceItems.value.reduce((s, i) => s + (i.credit_balance || 0), 0) * 100) / 100
)
const isBalanced = computed(() => Math.abs(totalDebit.value - totalCredit.value) <= 0.01 && totalDebit.value > 0)

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
} | null>(null)

function triggerUpload() {
  if (isLocked.value) {
    message.warning('期初凭证已过账锁定，无法再次导入')
    return
  }
  fileInputRef.value?.click()
}

function handleDownloadTemplate() {
  exportOpeningTemplate(balanceItems.value, fiscalYear.value)
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
    const sheetName = workbook.SheetNames[0]
    const sheet = workbook.Sheets[sheetName]
    const rows = XLSX.utils.sheet_to_json(sheet, { defval: '' }) as Record<string, unknown>[]

    if (!rows.length) {
      message.warning('Excel 文件为空')
      return
    }

    const parseResult = parseOpeningRows(rows, balanceItems.value)

    // 应用到当前表格数据中
    const resultMap = new Map(parseResult.items.map(i => [i.account_code, i]))
    items.value = items.value.map(item => {
      const parsed = resultMap.get(item.account_code)
      if (parsed && (parsed.debit_balance > 0 || parsed.credit_balance > 0)) {
        return {
          ...item,
          debit_balance: parsed.debit_balance,
          credit_balance: parsed.credit_balance,
        }
      }
      return item
    })

    importSummary.value = {
      matchedCount: parseResult.matchedCount,
      unmatchedCount: parseResult.unmatchedCount,
      warnings: parseResult.warnings,
      totalDebit: parseResult.totalDebit,
      totalCredit: parseResult.totalCredit,
      isBalanced: parseResult.isBalanced,
    }
    importModalVisible.value = true

    if (parseResult.isBalanced) {
      message.success(`成功导入 ${parseResult.matchedCount} 个科目，借贷双方已平衡`)
    } else {
      message.warning(`导入完成，但借贷差额 ¥${Math.abs(parseResult.totalDebit - parseResult.totalCredit).toFixed(2)}，请核对`)
    }
  } catch (err) {
    message.error(err instanceof Error ? err.message : 'Excel 解析失败，请检查文件格式')
  } finally {
    loading.value = false
  }
}

async function load() {
  loading.value = true
  try {
    const data: any = await apiClient.get('/api/opening/balances', {
      params: { fiscal_year: fiscalYear.value },
    })
    items.value = data.items || []
    loadTaxMemory(fiscalYear.value)
  } catch {
    message.error('加载期初余额失败')
  } finally {
    loading.value = false
  }
}

async function save() {
  if (!isBalanced.value) {
    message.warning('借方合计与贷方合计不相等（或金额为0），请检查')
    return
  }
  if (isLocked.value) {
    message.warning('期初凭证已过账；如需调整，请先冲销并创建调整凭证')
    return
  }
  saveTaxMemory(fiscalYear.value)
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

async function confirmSave() {
  if (!phone.value || !code.value) {
    message.warning('请输入手机号和验证码')
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
    await apiClient.put('/api/opening/balances', {
      fiscal_year: fiscalYear.value,
      items: items.value.map(i => ({
        account_code: i.account_code,
        account_name: i.account_name,
        debit_balance: i.debit_balance || 0,
        credit_balance: i.credit_balance || 0,
      })),
    }, {
      headers: {
        'Step-Up-Token': auth.stepUpToken,
        'Idempotency-Key': `opening-${fiscalYear.value}-${Date.now()}`,
      },
    })
    saveTaxMemory(fiscalYear.value)
    stepUpVisible.value = false
    code.value = ''
    message.success('期初凭证已生成并过账锁定')
    await load()
  } catch (e: any) {
    const detail = e?.response?.data?.detail
    message.error(typeof detail === 'string' ? detail : detail?.message || '保存失败')
  } finally {
    saving.value = false
  }
}

const currentYear = new Date().getFullYear()
const yearOptions = [
  { label: `${currentYear - 2} 年`, value: currentYear - 2 },
  { label: `${currentYear - 1} 年`, value: currentYear - 1 },
  { label: `${currentYear} 年`, value: currentYear },
  { label: `${currentYear + 1} 年`, value: currentYear + 1 },
]

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

    <!-- 顶部导航与主操作 -->
    <div class="page-header">
      <NButton text @click="router.push('/mine')">← 返回设置</NButton>
      <div class="header-row">
        <div>
          <h2>期初余额设置</h2>
          <span class="header-sub">存量老账接续 · 导入科目余额表 · 生成系统不可篡改期初凭证</span>
        </div>
        <div class="header-actions">
          <NSelect
            v-model:value="fiscalYear"
            :options="yearOptions"
            style="width: 110px;"
            size="small"
            @update:value="load"
          />
          <NButton size="small" secondary @click="handleDownloadTemplate">
            📥 下载模板
          </NButton>
          <NButton size="small" type="primary" secondary :disabled="isLocked" @click="triggerUpload">
            📤 导入 Excel
          </NButton>
          <NButton
            size="small"
            type="primary"
            :loading="saving"
            :disabled="isLocked || !isBalanced"
            @click="save"
          >
            {{ isLocked ? '已过账锁定' : '生成期初凭证' }}
          </NButton>
        </div>
      </div>
    </div>

    <!-- 平衡状态监控条 -->
    <div class="balance-summary" :class="{ balanced: isBalanced, unbalanced: !isBalanced }">
      <div class="summary-stat">
        <span class="label">借方合计：</span>
        <span class="value">¥ {{ totalDebit.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</span>
      </div>
      <div class="summary-stat">
        <span class="label">贷方合计：</span>
        <span class="value">¥ {{ totalCredit.toLocaleString('zh-CN', { minimumFractionDigits: 2 }) }}</span>
      </div>
      <div class="summary-status">
        <NTag v-if="isBalanced" size="small" type="success" :bordered="false">
          ✓ 借贷平衡，满足建账标准
        </NTag>
        <NTag v-else size="small" type="error" :bordered="false">
          ✗ {{ totalDebit === 0 ? '尚未填写期初余额' : `差额 ¥${Math.abs(totalDebit - totalCredit).toFixed(2)}` }}
        </NTag>
      </div>
    </div>

    <!-- 税务期初与接续备忘卡片 -->
    <NCard :bordered="false" class="block-card tax-memory-card">
      <NCollapse default-expanded-names="tax-memory">
        <NCollapseItem title="🏛️ 税务期初与申报衔接备忘（企业所得税累计抵扣）" name="tax-memory">
          <div class="tax-memory-grid">
            <div class="tax-field">
              <span class="field-title">以前年度可弥补亏损额（元）</span>
              <NInputNumber
                v-model:value="taxMemory.priorYearLoss"
                :disabled="isLocked"
                :min="0"
                placeholder="0.00"
                size="small"
                @update:value="saveTaxMemory(fiscalYear)"
              />
              <span class="field-desc">用于企业所得税预缴及汇算清缴时抵扣实际利润（A200000 第 24 行）。</span>
            </div>

            <div class="tax-field">
              <span class="field-title">本年已预缴企业所得税额（元）</span>
              <NInputNumber
                v-model:value="taxMemory.prepaidCitTax"
                :disabled="isLocked"
                :min="0"
                placeholder="0.00"
                size="small"
                @update:value="saveTaxMemory(fiscalYear)"
              />
              <span class="field-desc">年中迁入时前序季度已在税局缴纳的所得税额（A200000 第 30 行）。</span>
            </div>
          </div>
          <p class="tax-memory-hint">
            💡 提示：该税务备忘将在「税报作业包」与「企业所得税测算」中作为抵扣事实依据自动关联，避免年中换系统多缴税。
          </p>
        </NCollapseItem>
      </NCollapse>
    </NCard>

    <!-- 资产负债科目期初明细表 -->
    <NSpin :show="loading">
      <NCard :bordered="false" class="table-card">
        <div class="table-header-tip">
          <span>期初资产负债科目明细（仅末级科目，已过滤损益类科目）：</span>
          <span class="muted-note">支持直接在线编辑调整，也可导出或导入 Excel。</span>
        </div>
        <table class="balance-table">
          <thead>
            <tr>
              <th class="col-code">科目代码</th>
              <th class="col-name">科目名称</th>
              <th class="col-amount">期初借方余额</th>
              <th class="col-amount">期初贷方余额</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in balanceItems" :key="item.account_code">
              <td class="col-code">{{ item.account_code }}</td>
              <td class="col-name">{{ item.account_name || item.account_code }}</td>
              <td class="col-amount">
                <NInput
                  :disabled="isLocked"
                  :value="item.debit_balance ? String(item.debit_balance) : ''"
                  @update:value="(v: any) => { item.debit_balance = parseFloat(v) || 0 }"
                  placeholder="0.00"
                  size="small"
                  class="amount-input"
                />
              </td>
              <td class="col-amount">
                <NInput
                  :disabled="isLocked"
                  :value="item.credit_balance ? String(item.credit_balance) : ''"
                  @update:value="(v: any) => { item.credit_balance = parseFloat(v) || 0 }"
                  placeholder="0.00"
                  size="small"
                  class="amount-input"
                />
              </td>
            </tr>
          </tbody>
          <tfoot>
            <tr>
              <td colspan="2" class="col-total">借贷方合计</td>
              <td class="col-amount total">¥ {{ totalDebit.toFixed(2) }}</td>
              <td class="col-amount total">¥ {{ totalCredit.toFixed(2) }}</td>
            </tr>
          </tfoot>
        </table>
      </NCard>
    </NSpin>

    <!-- 导入诊断反馈弹窗 -->
    <NModal
      v-model:show="importModalVisible"
      preset="card"
      title="Excel 科目余额表导入结果"
      style="max-width: 500px"
    >
      <div v-if="importSummary" class="import-report-content">
        <NAlert :type="importSummary.isBalanced ? 'success' : 'warning'" :bordered="false" class="import-alert">
          {{ importSummary.isBalanced ? '导入成功且借贷已平衡！可确认后生成期初凭证。' : '数据已填入，但借贷两端尚有差额，请在表格中核对调整。' }}
        </NAlert>

        <div class="import-stats-box">
          <div class="stat-line">
            <span>成功匹配并填入科目：</span>
            <strong>{{ importSummary.matchedCount }} 个</strong>
          </div>
          <div class="stat-line">
            <span>借方导入合计：</span>
            <strong>¥ {{ importSummary.totalDebit.toFixed(2) }}</strong>
          </div>
          <div class="stat-line">
            <span>贷方导入合计：</span>
            <strong>¥ {{ importSummary.totalCredit.toFixed(2) }}</strong>
          </div>
          <div class="stat-line" v-if="!importSummary.isBalanced">
            <span>未平差额：</span>
            <strong style="color: #d03050">¥ {{ Math.abs(importSummary.totalDebit - importSummary.totalCredit).toFixed(2) }}</strong>
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

    <!-- 短信确认与过账锁定弹窗 -->
    <NModal v-model:show="stepUpVisible" preset="card" title="短信确认期初凭证" style="max-width: 420px">
      <NFormItem label="已绑定手机号">
        <NInput v-model:value="phone" placeholder="请输入当前账号手机号" />
      </NFormItem>
      <NFormItem label="短信验证码">
        <NSpace :wrap="false" style="width: 100%">
          <NInput v-model:value="code" placeholder="验证码" />
          <NButton :loading="sendingCode" @click="sendCode">发送验证码</NButton>
        </NSpace>
      </NFormItem>
      <p class="step-up-note">
        提交后将生成系统不可修改的<strong>期初凭证（Opening Journal）</strong>并过账锁定；如需更正，必须冲销后重新录入。
      </p>
      <NSpace justify="end">
        <NButton @click="stepUpVisible = false">取消</NButton>
        <NButton type="primary" :loading="saving" @click="confirmSave">确认生成并过账</NButton>
      </NSpace>
    </NModal>
  </div>
</template>

<style scoped>
.opening-page {
  max-width: 1200px;
  margin: 0 auto;
  padding-bottom: 32px;
  width: 100%;
  box-sizing: border-box;
}
.page-header {
  margin-bottom: 16px;
}
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  margin-top: 8px;
}
.header-row h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  color: var(--solo-text-primary, #1d2129);
}
.header-sub {
  font-size: 13px;
  color: var(--solo-text-muted, #86909c);
  margin-top: 4px;
  display: block;
}
.header-actions {
  display: flex;
  align-items: center;
  gap: 8px;
}

.balance-summary {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 18px;
  border-radius: var(--solo-radius-lg, 10px);
  margin-bottom: 14px;
  background: var(--solo-bg-card, #ffffff);
  border: 1px solid var(--solo-border, #f2f3f5);
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.04);
}
.summary-stat {
  display: flex;
  align-items: baseline;
  gap: 6px;
}
.summary-stat .label {
  font-size: 13px;
  color: var(--solo-text-secondary, #4e5969);
}
.summary-stat .value {
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-primary, #1d2129);
}

.tax-memory-card {
  margin-bottom: 14px;
  border-radius: var(--solo-radius-lg, 10px);
  border: 1px dashed rgba(22, 93, 255, 0.25);
  background: rgba(22, 93, 255, 0.02);
}
.tax-memory-grid {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 16px;
  margin-top: 8px;
}
.tax-field {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.field-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-primary, #1d2129);
}
.field-desc {
  font-size: 11px;
  color: var(--solo-text-muted, #86909c);
  line-height: 1.4;
}
.tax-memory-hint {
  margin: 12px 0 0;
  font-size: 12px;
  color: var(--solo-ai, #165dff);
}

.table-card {
  border-radius: var(--solo-radius-lg, 10px);
  overflow-x: auto;
}
.table-header-tip {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
  font-size: 13px;
  font-weight: 500;
  color: var(--solo-text-secondary, #4e5969);
}
.muted-note {
  font-size: 12px;
  color: var(--solo-text-muted, #86909c);
  font-weight: normal;
}
.balance-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}
.balance-table th {
  text-align: left;
  padding: 10px 12px;
  font-weight: 600;
  color: var(--solo-text-secondary, #4e5969);
  background: var(--solo-bg-page, #f7f8fa);
  border-bottom: 1px solid var(--solo-border, #f2f3f5);
}
.balance-table td {
  padding: 6px 12px;
  border-bottom: 1px solid var(--solo-border, #f2f3f5);
}
.balance-table tfoot td {
  border-top: 2px solid var(--solo-border, #f2f3f5);
  font-weight: 600;
  padding: 10px 12px;
  background: var(--solo-bg-page, #f7f8fa);
}
.col-code { width: 100px; font-variant-numeric: tabular-nums; }
.col-name { width: 180px; }
.col-amount { width: 140px; text-align: right; }
.col-total { text-align: right; padding-right: 16px; font-size: 13px; }
.amount-input { text-align: right; }
.total {
  font-weight: 700;
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-primary, #1d2129);
}

.step-up-note {
  color: var(--solo-text-secondary, #4e5969);
  font-size: 12px;
  line-height: 1.6;
}

/* 导入结果弹窗 */
.import-report-content {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.import-stats-box {
  background: var(--solo-bg-page, #f7f8fa);
  padding: 12px 14px;
  border-radius: 8px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  font-size: 13px;
}
.stat-line {
  display: flex;
  justify-content: space-between;
}
.warnings-box {
  font-size: 12px;
}
.warning-title {
  color: var(--solo-text-secondary, #4e5969);
  font-weight: 600;
}
.warning-list {
  margin: 6px 0 0;
  padding-left: 18px;
  color: var(--solo-text-muted, #86909c);
  line-height: 1.6;
}
</style>
