<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { reportErrorToast } from '@/utils/errorPresenter'
import {
  NAlert, NButton, NCard, NCheckbox, NFormItem, NInput, NModal, NSelect, NSpin, NTabPane, NTabs, useMessage,
} from 'naive-ui'
import * as XLSX from 'xlsx'
import apiClient from '@/api/client'
import { requestStepUp, sendPhoneCode } from '@/api/auth'
import {
  confirmLegacyBalance, confirmLegacyVouchers, parseLegacyBalance, parseLegacyVouchers,
} from '@/api/legacy'
import type { BalanceParseResponse, MappingSuggestion, ParseIssue, VoucherParseResponse } from '@/api/legacy'
import { SoloMonthPicker } from '@/components/ui'
import { formatMoney } from '@/utils/format'
import { saveWorkbook } from '@/utils/export'

type Mode = 'balance' | 'voucher'

const props = withDefaults(defineProps<{ embedded?: boolean }>(), { embedded: false })
const emit = defineEmits<{ imported: [] }>()

const router = useRouter()
const message = useMessage()

const mode = ref<Mode>('balance')
const fileName = ref('')
const rawRows = ref<Record<string, unknown>[]>([])
const parsing = ref(false)
const confirming = ref(false)
const balanceResult = ref<BalanceParseResponse | null>(null)
const voucherResult = ref<VoucherParseResponse | null>(null)
const fiscalYear = ref(new Date().getFullYear())
const startPeriod = ref<string | null>(null)
const postDirectly = ref(false)
const mappingOverrides = ref<Record<string, string>>({})
const accountOptions = ref<{ label: string; value: string }[]>([])

const stepUpVisible = ref(false)
const phone = ref('')
const code = ref('')
const bizId = ref('')
const sendingCode = ref(false)

const parsed = computed(() => (mode.value === 'balance' ? balanceResult.value : voucherResult.value))
const issues = computed<ParseIssue[]>(() => parsed.value?.errors || [])
const warnings = computed<ParseIssue[]>(() => parsed.value?.warnings || [])
const suggestions = computed<MappingSuggestion[]>(() => parsed.value?.suggestions || [])

const balanceTotals = computed(() => (mode.value === 'balance' ? balanceResult.value?.totals : null))
const voucherTotals = computed(() => (mode.value === 'voucher' ? voucherResult.value?.totals : null))

function downloadTemplate() {
  const headers = mode.value === 'balance'
    ? ['科目编码', '科目名称', '方向', '期初余额(借)', '期初余额(贷)', '年初余额', '本年累计借方', '本年累计贷方', '往来单位']
    : ['凭证号', '日期', '摘要', '科目编码', '科目名称', '借方金额', '贷方金额', '数量', '单价', '单位', '往来单位', '部门', '项目']
  const worksheet = XLSX.utils.aoa_to_sheet([headers])
  worksheet['!cols'] = headers.map(() => ({ wch: 16 }))
  const workbook = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(workbook, worksheet, mode.value === 'balance' ? '科目余额表' : '凭证分录')
  saveWorkbook(workbook, mode.value === 'balance' ? '科目余额表导入模板.xlsx' : '凭证分录导入模板.xlsx')
}

async function onFilePicked(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  fileName.value = file.name
  try {
    const buffer = await file.arrayBuffer()
    const workbook = XLSX.read(buffer, { type: 'array' })
    const sheet = workbook.Sheets[workbook.SheetNames[0]]
    rawRows.value = XLSX.utils.sheet_to_json(sheet, { defval: '', raw: false })
    if (rawRows.value.length === 0) {
      message.warning('文件里没有数据行')
      return
    }
    await runParse()
  } catch {
    message.error('无法读取该文件，请确认是 Excel（.xlsx/.xls）')
  } finally {
    input.value = ''
  }
}

async function runParse() {
  if (rawRows.value.length === 0) return
  parsing.value = true
  try {
    const mapping = Object.keys(mappingOverrides.value).length > 0 ? mappingOverrides.value : undefined
    if (mode.value === 'balance') {
      balanceResult.value = await parseLegacyBalance(rawRows.value, mapping)
      voucherResult.value = null
    } else {
      voucherResult.value = await parseLegacyVouchers(rawRows.value, mapping)
      balanceResult.value = null
    }
  } catch {
    message.error('解析失败，请检查文件格式')
  } finally {
    parsing.value = false
  }
}

async function applyMapping() {
  if (!parsed.value) return
  const merged = { ...(parsed.value.mapping || {}), ...mappingOverrides.value }
  mappingOverrides.value = merged
  await runParse()
  message.success('已按映射重新校验')
}

function reset() {
  rawRows.value = []
  fileName.value = ''
  balanceResult.value = null
  voucherResult.value = null
  mappingOverrides.value = {}
  postDirectly.value = false
}

function transferToOpening() {
  if (!balanceResult.value || balanceResult.value.valid.length === 0) return
  const payload = {
    items: balanceResult.value.valid,
    ytdItems: balanceResult.value.valid
      .filter((it: any) => parseFloat(it.ytdDebit || 0) > 0 || parseFloat(it.ytdCredit || 0) > 0)
      .map((it: any) => ({
        accountCode: it.accountCode,
        accountName: it.accountName,
        debitBalance: it.ytdDebit,
        creditBalance: it.ytdCredit,
      })),
    fiscalYear: fiscalYear.value,
    startPeriod: startPeriod.value,
  }
  sessionStorage.setItem('solobooks:pending_opening_import', JSON.stringify(payload))
  message.success('已将解析数据带入期初草稿，正在前往期初设置...')
  emit('imported')
  if (!props.embedded) {
    router.push('/mine/opening')
  }
}

async function startConfirm() {
  if (!parsed.value) return
  if (issues.value.length > 0) {
    message.warning(`还有 ${issues.value.length} 行未通过校验，请先修正或排除后再导入`)
    return
  }
  if (mode.value === 'balance') {
    stepUpVisible.value = true
    return
  }
  await confirmVouchers()
}

async function confirmVouchers() {
  if (!voucherResult.value) return
  confirming.value = true
  try {
    const result = await confirmLegacyVouchers({
      vouchers: voucherResult.value.valid,
      post: postDirectly.value,
      mapping: Object.keys(mappingOverrides.value).length > 0 ? mappingOverrides.value : undefined,
    })
    const skipped = result.skipped.length ? `，跳过 ${result.skipped.length} 张（已导入过）` : ''
    message.success(`已导入 ${result.imported} 张凭证${result.posted ? `，过账 ${result.posted} 张` : '（待审）'}${skipped}`)
    router.push('/vouchers')
  } catch (error: unknown) {
    reportErrorToast(message, error, '导入失败')
  } finally {
    confirming.value = false
  }
}

async function sendCode() {
  if (!phone.value) {
    message.warning('请输入账号绑定的手机号')
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

async function confirmBalanceWithStepUp() {
  if (!balanceResult.value) return
  if (!phone.value || !code.value) {
    message.warning('请输入手机号和短信验证码')
    return
  }
  confirming.value = true
  try {
    const auth = await requestStepUp({
      action: 'adjust_opening_balance',
      phone: phone.value,
      code: code.value,
      bizId: bizId.value || undefined,
    })
    await confirmLegacyBalance(
      {
        items: balanceResult.value.valid,
        fiscal_year: fiscalYear.value,
        start_period: startPeriod.value || undefined,
        mapping: Object.keys(mappingOverrides.value).length > 0 ? mappingOverrides.value : undefined,
      },
      auth.stepUpToken,
    )
    stepUpVisible.value = false
    message.success('期初余额已导入并生成期初凭证')
    emit('imported')
    if (!props.embedded) {
      router.push('/mine/opening')
    }
  } catch (error: unknown) {
    reportErrorToast(message, error, '导入失败')
  } finally {
    confirming.value = false
  }
}

async function loadAccounts() {
  try {
    const data: any = await apiClient.get('/api/ledger/accounts')
    accountOptions.value = (data.accounts || []).map((item: any) => ({
      label: `${item.code} ${item.name}`,
      value: item.code,
    }))
  } catch {
    accountOptions.value = []
  }
}

onMounted(loadAccounts)
</script>

<template>
  <div class="report-container">
    <div class="report-header">
      <div class="report-header-left">
        <NButton v-if="!props.embedded" text class="report-back-btn" @click="router.push('/mine')">
          <template #icon><i class="ri-arrow-left-line" /></template>
          返回设置
        </NButton>
        <div class="report-title-section">
          <h2>旧账导入</h2>
          <p class="report-subtitle">从金蝶 / 用友 / 柠檬云等迁移：支持凭证分录批量导入历史流水，或将科目余额表快速转入期初</p>
        </div>
      </div>
      <div class="report-header-right">
        <NButton secondary @click="downloadTemplate">
          <template #icon><i class="ri-file-download-line" /></template>
          下载模板
        </NButton>
      </div>
    </div>

    <NTabs v-model:value="mode" type="line" animated :disabled="rawRows.length > 0" @update:value="reset">
      <NTabPane name="balance" tab="科目余额表 → 期初" />
      <NTabPane name="voucher" tab="凭证分录 → 历史凭证" />
    </NTabs>

    <NAlert v-if="mode === 'balance'" type="info" :bordered="false" class="hint-alert">
      科目余额表导入将作为期初建账凭证。建议优先前往<b>「期初设置」</b>直接维护或导入（支持试算平衡、固定资产对账与年中损益校验）；在此处解析后，您也可以点击<b>「带入期初设置细调」</b>一键前往核算。
    </NAlert>
    <NAlert v-else type="info" :bordered="false" class="hint-alert">
      凭证导入默认生成<b>草稿待审</b>凭证，逐张校验借贷平衡；同一批次重复导入会自动跳过，原凭证号会保留。
    </NAlert>

    <!-- 上传 -->
    <NCard v-if="rawRows.length === 0" :bordered="false" class="report-card upload-card">
      <div class="upload-inner">
        <i class="ri-upload-cloud-2-line upload-icon" />
        <p class="upload-title">选择 Excel 文件（.xlsx / .xls）</p>
        <p class="upload-sub">列名支持常见中文表头；识别不到的科目会在预览页给出匹配建议</p>
        <label class="upload-button">
          选择文件
          <input type="file" accept=".xlsx,.xls" @change="onFilePicked" />
        </label>
      </div>
    </NCard>

    <!-- 预览 -->
    <template v-else>
      <NCard :bordered="false" class="report-card mb-4">
        <div class="file-row">
          <span class="file-name"><i class="ri-file-excel-2-line" /> {{ fileName }}</span>
          <span class="file-meta">共 {{ rawRows.length }} 行</span>
          <NButton size="small" text @click="reset">重新选择</NButton>
        </div>
      </NCard>

      <NSpin :show="parsing">
        <div class="stat-row">
          <NCard :bordered="false" class="stat-card">
            <span class="stat-label">校验通过</span>
            <span class="stat-value ok">{{ mode === 'balance' ? balanceTotals?.count : voucherTotals?.vouchers }} {{ mode === 'balance' ? '个科目' : '张凭证' }}</span>
          </NCard>
          <NCard :bordered="false" class="stat-card">
            <span class="stat-label">未通过</span>
            <span class="stat-value" :class="{ bad: issues.length > 0 }">{{ issues.length }} 行</span>
          </NCard>
          <NCard v-if="mode === 'balance'" :bordered="false" class="stat-card">
            <span class="stat-label">借贷合计</span>
            <span class="stat-value" :class="{ bad: !balanceTotals?.balanced }">
              {{ formatMoney(Number(balanceTotals?.debit || 0)) }} / {{ formatMoney(Number(balanceTotals?.credit || 0)) }}
            </span>
          </NCard>
          <NCard v-else :bordered="false" class="stat-card">
            <span class="stat-label">分录行数</span>
            <span class="stat-value">{{ voucherTotals?.lines || 0 }}</span>
          </NCard>
        </div>

        <NAlert v-if="issues.length > 0" type="error" :bordered="false" class="mb-4">
          <div class="alert-title">以下行未通过校验（逐行说明原因）</div>
          <ul class="issue-list">
            <li v-for="(issue, index) in issues.slice(0, 50)" :key="index">第 {{ issue.row }} 行：{{ issue.reason }}</li>
          </ul>
          <div v-if="issues.length > 50" class="issue-more">还有 {{ issues.length - 50 }} 行未显示</div>
        </NAlert>

        <NAlert v-if="warnings.length > 0" type="warning" :bordered="false" class="mb-4">
          <div class="alert-title">提醒</div>
          <ul class="issue-list">
            <li v-for="(warning, index) in warnings.slice(0, 20)" :key="index">第 {{ warning.row }} 行：{{ warning.reason }}</li>
          </ul>
        </NAlert>

        <!-- 科目映射 -->
        <NCard v-if="suggestions.length > 0" :bordered="false" class="report-card mb-4">
          <div class="section-head">
            <span class="section-title">科目映射</span>
            <span class="section-sub">按名称预填了建议，逐条确认后点「应用映射并重新校验」；映射会记住，下次导入同账套直接复用</span>
          </div>
          <table class="report-table">
            <thead>
              <tr>
                <th style="width: 80px;">行号</th>
                <th style="width: 160px;">来源编码</th>
                <th>来源名称</th>
                <th style="width: 320px;">映射到本账套科目</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in suggestions" :key="item.row">
                <td>{{ item.row }}</td>
                <td class="mono">{{ item.sourceCode || '—' }}</td>
                <td>{{ item.sourceName || '—' }}</td>
                <td>
                  <NSelect
                    v-model:value="mappingOverrides[item.sourceCode || item.sourceName]"
                    :options="accountOptions"
                    filterable
                    clearable
                    :placeholder="item.suggestedCode ? `建议 ${item.suggestedCode}` : '选择科目'"
                    size="small"
                  />
                </td>
              </tr>
            </tbody>
          </table>
          <div class="mapping-actions">
            <NButton size="small" type="primary" secondary @click="applyMapping">应用映射并重新校验</NButton>
          </div>
        </NCard>

        <!-- 通过的数据 -->
        <NCard :bordered="false" class="report-card">
          <div class="section-head">
            <span class="section-title">待导入数据预览</span>
            <span class="section-sub">仅显示前 30 条</span>
          </div>
          <table v-if="mode === 'balance'" class="report-table">
            <thead>
              <tr>
                <th style="width: 120px;">科目编码</th>
                <th>科目名称</th>
                <th style="width: 140px; text-align: right;">期初借方</th>
                <th style="width: 140px; text-align: right;">期初贷方</th>
                <th style="width: 140px; text-align: right;">本年累计借</th>
                <th style="width: 140px; text-align: right;">本年累计贷</th>
                <th style="width: 160px;">往来单位</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in balanceResult?.valid.slice(0, 30) || []" :key="row.row">
                <td class="mono">{{ row.accountCode }}</td>
                <td>{{ row.accountName }}</td>
                <td class="amount-cell">{{ formatMoney(Number(row.debitBalance)) }}</td>
                <td class="amount-cell">{{ formatMoney(Number(row.creditBalance)) }}</td>
                <td class="amount-cell">{{ formatMoney(Number(row.ytdDebit)) }}</td>
                <td class="amount-cell">{{ formatMoney(Number(row.ytdCredit)) }}</td>
                <td>{{ row.partnerName || '—' }}</td>
              </tr>
            </tbody>
          </table>
          <table v-else class="report-table">
            <thead>
              <tr>
                <th style="width: 110px;">凭证号</th>
                <th style="width: 110px;">日期</th>
                <th>摘要</th>
                <th style="width: 90px; text-align: right;">分录</th>
                <th style="width: 140px; text-align: right;">金额</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="row in voucherResult?.valid.slice(0, 30) || []" :key="row.key">
                <td class="mono">{{ row.voucherNo || '—' }}</td>
                <td>{{ row.date }}</td>
                <td>{{ row.summary }}</td>
                <td class="amount-cell">{{ row.lines.length }}</td>
                <td class="amount-cell">{{ formatMoney(Number(row.debitTotal)) }}</td>
              </tr>
            </tbody>
          </table>
        </NCard>

        <!-- 确认区 -->
        <NCard :bordered="false" class="report-card confirm-card">
          <div v-if="mode === 'balance'" class="confirm-row">
            <NFormItem label="会计年度" label-placement="left" :show-feedback="false">
              <NInput :value="String(fiscalYear)" style="width: 110px;" @update:value="(value: string) => (fiscalYear = Number(value) || fiscalYear)" />
            </NFormItem>
            <NFormItem label="启用期间" label-placement="left" :show-feedback="false">
              <SoloMonthPicker v-model:value="startPeriod" clearable style="width: 140px;" />
            </NFormItem>
            <span class="confirm-hint">可先带入期初设置细调，或直接验证生成期初凭证</span>
            <NButton secondary type="primary" :disabled="issues.length > 0" @click="transferToOpening">
              带入期初设置细调
            </NButton>
            <NButton type="primary" :loading="confirming" :disabled="issues.length > 0" @click="startConfirm">直接验证并导入</NButton>
          </div>
          <div v-else class="confirm-row">
            <NCheckbox v-model:checked="postDirectly">导入后直接过账（默认生成草稿待审）</NCheckbox>
            <NButton type="primary" :loading="confirming" :disabled="issues.length > 0" @click="startConfirm">
              确认导入 {{ voucherTotals?.vouchers || 0 }} 张凭证
            </NButton>
          </div>
        </NCard>
      </NSpin>
    </template>

    <NModal v-model:show="stepUpVisible" preset="card" title="短信二次验证" style="max-width: 420px;">
      <p class="stepup-hint">期初凭证写入后不可直接修改，需要短信验证确认是本人操作。</p>
      <NFormItem label="手机号">
        <NInput v-model:value="phone" placeholder="账号绑定的手机号" />
      </NFormItem>
      <NFormItem label="验证码">
        <div class="code-row">
          <NInput v-model:value="code" placeholder="6 位验证码" />
          <NButton :loading="sendingCode" @click="sendCode">获取验证码</NButton>
        </div>
      </NFormItem>
      <template #footer>
        <div class="modal-footer">
          <NButton @click="stepUpVisible = false">取消</NButton>
          <NButton type="primary" :loading="confirming" @click="confirmBalanceWithStepUp">验证并导入</NButton>
        </div>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.mb-4 { margin-bottom: 16px; }
.report-header { align-items: flex-start; }
.report-header-right { display: flex; gap: 8px; }
.hint-alert { margin: 12px 0 16px; }
.upload-card { padding: 8px; }
.upload-inner { display: flex; flex-direction: column; align-items: center; gap: 8px; padding: 48px 16px; }
.upload-icon { font-size: 44px; color: var(--solo-primary); }
.upload-title { margin: 0; font-size: 15px; font-weight: 600; }
.upload-sub { margin: 0; font-size: 12px; color: var(--solo-text-muted); }
.upload-button {
  margin-top: 8px;
  padding: 8px 20px;
  border-radius: 8px;
  background: var(--solo-primary);
  color: #fff;
  cursor: pointer;
  font-size: 13px;
}
.upload-button input { display: none; }
.file-row { display: flex; align-items: center; gap: 12px; }
.file-name { font-weight: 600; display: inline-flex; align-items: center; gap: 6px; }
.file-meta { font-size: 12px; color: var(--solo-text-muted); }
.stat-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-bottom: 16px; }
.stat-card { display: flex; flex-direction: column; gap: 6px; }
.stat-label { font-size: 12px; color: var(--solo-text-muted); }
.stat-value { font-size: 18px; font-weight: 700; font-variant-numeric: tabular-nums; }
/* 这两行原来把 token 用反了：.ok 取 --solo-expense（兜底却是绿）、.bad 取
   --solo-income（兜底是红），结果"正常"显示红色、"异常"显示绿色。 */
.stat-value.ok { color: var(--solo-income); }
.stat-value.bad { color: var(--solo-expense); }
.alert-title { font-weight: 600; margin-bottom: 6px; }
.issue-list { margin: 0; padding-left: 18px; line-height: 1.8; font-size: 12.5px; }
.issue-more { margin-top: 6px; font-size: 12px; color: var(--solo-text-muted); }
.section-head { display: flex; align-items: baseline; gap: 10px; margin-bottom: 10px; }
.section-title { font-weight: 700; }
.section-sub { font-size: 12px; color: var(--solo-text-muted); }
.mapping-actions { margin-top: 12px; display: flex; justify-content: flex-end; }
.mono { font-variant-numeric: tabular-nums; }
.amount-cell { text-align: right; font-variant-numeric: tabular-nums; }
.confirm-card { margin-top: 16px; }
.confirm-row { display: flex; align-items: center; flex-wrap: wrap; gap: 12px; }
.confirm-row :deep(.n-form-item) { margin-bottom: 0; }
.confirm-hint { font-size: 12px; color: var(--solo-text-muted); margin-left: auto; }
.stepup-hint { font-size: 12.5px; color: var(--solo-text-muted); margin: 0 0 12px; }
.code-row { display: flex; gap: 8px; width: 100%; }
.modal-footer { display: flex; justify-content: flex-end; gap: 8px; }
</style>
