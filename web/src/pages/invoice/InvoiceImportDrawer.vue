<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref, h, computed, watch } from 'vue'
import {
  NButton, NDataTable, NInput, NSelect, NSpace, NSpin, NTag, useMessage,
} from 'naive-ui'
import * as XLSX from 'xlsx'
import { parseInvoiceRows, importInvoices } from '@/api/invoice'
import { getRdExpenseTypes, getRdProjects } from '@/api/rd'
import type { RdExpenseTypeOption, RdProject } from '@/api/types'
import { formatMoney } from '@/utils/format'
import { SoloDrawer } from '@/components/ui'
import FileDropzone from '@/components/business/FileDropzone.vue'

const props = defineProps<{
  show: boolean
}>()

const emit = defineEmits<{
  'update:show': [val: boolean]
  success: []
}>()

const message = useMessage()

type Step = 'upload' | 'mapping' | 'preview'
const step = ref<Step>('upload')
const loading = ref(false)
const importing = ref(false)

// Excel raw data
const excelRows = ref<any[]>([])
const excelColumns = ref<string[]>([])
const columnMapping = ref<Record<string, string>>({})

// Validated rows
const classifiedRows = ref<any[]>([])

// RD options
const rdProjects = ref<RdProject[]>([])
const rdExpenseTypes = ref<RdExpenseTypeOption[]>([])

const directionOptions = [
  { label: '进项 (支出)', value: 'expense' },
  { label: '销项 (收入)', value: 'income' },
  { label: '资本与往来', value: 'owner_transfer' },
]

const SYSTEM_FIELDS = [
  { label: '忽略', value: '' },
  { label: '发票号码', value: 'invoice_number' },
  { label: '发票代码', value: 'invoice_code' },
  { label: '开票日期', value: 'invoice_date' },
  { label: '销售方名称', value: 'seller_name' },
  { label: '销售方税号', value: 'seller_tax_id' },
  { label: '购买方名称', value: 'buyer_name' },
  { label: '购买方税号', value: 'buyer_tax_id' },
  { label: '金额 (不含税)', value: 'amount' },
  { label: '税额', value: 'tax_amount' },
  { label: '税率', value: 'tax_rate' },
  { label: '价税合计', value: 'total_amount' },
  { label: '货物或应税劳务', value: 'item_name' },
  { label: '规格型号', value: 'specification' },
  { label: '单位', value: 'unit' },
  { label: '数量', value: 'quantity' },
  { label: '单价', value: 'unit_price' },
  { label: '备注', value: 'remark' },
]

// 税务局全量发票查询导出标准列名（精确匹配）
const TAX_BUREAU_MAP: Record<string, string> = {
  '序号': '',
  '发票代码': 'invoice_code',
  '发票号码': 'invoice_number',
  '数电发票号码': 'invoice_number',
  '销售方识别号': 'seller_tax_id',
  '销售方名称': 'seller_name',
  '购买方识别号': 'buyer_tax_id',
  '购买方名称': 'buyer_name',
  '开票日期': 'invoice_date',
  '税收分类编码': '',
  '特定业务类型': '',
  '货物或应税劳务名称': 'item_name',
  '规格型号': 'specification',
  '单位': 'unit',
  '数量': 'quantity',
  '单价': 'unit_price',
  '金额': 'amount',
  '税率': 'tax_rate',
  '税额': 'tax_amount',
  '价税合计': 'total_amount',
  '发票来源': 'invoice_source',
  '发票票种': 'invoice_type',
  '发票状态': 'invoice_status',
  '是否正数发票': '',
  '发票风险等级': '',
  '开票人': 'drawer',
  '备注': 'remark',
}

async function loadRdOptions() {
  try {
    const [projects, expenseTypes] = await Promise.all([getRdProjects(), getRdExpenseTypes()])
    rdProjects.value = projects.filter(project => project.status === 'active')
    rdExpenseTypes.value = expenseTypes.items
  } catch {
    // 研发项目非强制
  }
}

watch(() => props.show, (val) => {
  if (val) {
    reset()
    loadRdOptions()
  }
})

function reset() {
  step.value = 'upload'
  excelRows.value = []
  excelColumns.value = []
  columnMapping.value = {}
  classifiedRows.value = []
  loading.value = false
  importing.value = false
}

function handleFile(file: File) {
  loading.value = true
  const reader = new FileReader()
  reader.onload = async (ev) => {
    try {
      const data = new Uint8Array(ev.target?.result as ArrayBuffer)
      const workbook = XLSX.read(data, { type: 'array' })
      const sheet = workbook.Sheets[workbook.SheetNames[0]]
      const json = XLSX.utils.sheet_to_json(sheet, { defval: '' })
      if (json.length === 0) {
        message.warning('Excel 文件为空')
        return
      }
      excelColumns.value = Object.keys(json[0] as any)
      excelRows.value = json as any[]

      const map: Record<string, string> = {}
      for (const col of excelColumns.value) {
        if (TAX_BUREAU_MAP[col] !== undefined) {
          map[col] = TAX_BUREAU_MAP[col]
        } else {
          const lc = col.toLowerCase()
          if (/数电发票号码|数电号码|发票号码|发票代码|invoice.*no|invoice.*num|发票编号/.test(lc)) map[col] = 'invoice_number'
          else if (/开票日期|发票日期|date|开票时间/.test(lc)) map[col] = 'invoice_date'
          else if (/销.*识别号|销.*税号|销.*纳税人|seller.*tax/.test(lc)) map[col] = 'seller_tax_id'
          else if (/购.*识别号|购.*税号|购.*纳税人|buyer.*tax/.test(lc)) map[col] = 'buyer_tax_id'
          else if (/销售方|销方|卖方|seller|供应商|供货方/.test(lc)) map[col] = 'seller_name'
          else if (/购买方|购方|买方|buyer|客户/.test(lc)) map[col] = 'buyer_name'
          else if (/价税合计|总金额|total.*amount|含税金额|合计/.test(lc)) map[col] = 'total_amount'
          else if (/金额|amount|不含税/.test(lc) && !/税额/.test(lc)) map[col] = 'amount'
          else if (/税额|tax/.test(lc)) map[col] = 'tax_amount'
          else if (/税率/.test(lc)) map[col] = 'tax_rate'
          else if (/名称|货物|item|商品/.test(lc)) map[col] = 'item_name'
          else map[col] = ''
        }
      }
      columnMapping.value = map
      step.value = 'mapping'
    } catch {
      message.error('解析 Excel 文件失败')
    } finally {
      loading.value = false
    }
  }
  reader.readAsArrayBuffer(file)
}

function safeFloat(val: any): number {
  if (val === null || val === undefined || val === '') return 0
  if (typeof val === 'number') return val
  const cleaned = String(val).replace(/,/g, '').trim()
  const parsed = parseFloat(cleaned)
  return isNaN(parsed) ? 0 : parsed
}

async function proceedToPreview() {
  const mapped = excelRows.value.map(row => {
    const out: Record<string, any> = {}
    for (const [col, field] of Object.entries(columnMapping.value)) {
      if (field) {
        const val = (row as any)[col]
        if (val !== undefined && val !== null && String(val).trim() !== '') {
          out[field] = val
        } else if (out[field] === undefined) {
          out[field] = val
        }
      }
    }

    const isNegative = Object.entries(row as any).some(([k, v]) => {
      const key = String(k).trim()
      const val = String(v).trim()
      return (key === '是否正数发票' && val === '否') || (key === '发票状态' && val === '红冲')
    })

    if (isNegative) {
      if (out.amount !== undefined) out.amount = -Math.abs(safeFloat(out.amount))
      if (out.total_amount !== undefined) out.total_amount = -Math.abs(safeFloat(out.total_amount))
      if (out.tax_amount !== undefined) out.tax_amount = -Math.abs(safeFloat(out.tax_amount))
    }

    return out
  })

  loading.value = true
  try {
    const result = await parseInvoiceRows(mapped)
    classifiedRows.value = result.valid.map((r: any) => ({
      ...r,
      _direction: r.direction || 'expense',
      _category: r.category || '其他支出',
    }))
    if (result.errors.length > 0) {
      message.warning(`${result.errors.length} 行数据无效已跳过`)
    }
    if (result.valid.length === 0) {
      message.error('没有有效发票数据')
      return
    }
    step.value = 'preview'
  } catch {
    message.error('解析发票数据失败')
  } finally {
    loading.value = false
  }
}

function deletePreviewRow(index: number) {
  classifiedRows.value.splice(index, 1)
  if (classifiedRows.value.length === 0) {
    message.warning('预览列表已空，已返回上传步骤')
    step.value = 'upload'
  }
}

const previewColumns = computed(() => [
  {
    title: '发票号码',
    key: 'invoice_number',
    width: 200,
    render: (row: any) => {
      const children = [h('span', { class: 'font-mono' }, row.invoice_number || '—')]
      if (row.is_duplicate) {
        children.push(
          h(NTag, { size: 'tiny', type: 'warning', style: 'margin-left: 6px;' }, () => '已存在/重复')
        )
      }
      return h('div', { style: 'display: flex; align-items: center;' }, children)
    },
  },
  { title: '开票日期', key: 'invoice_date', width: 105 },
  {
    title: '对方单位',
    key: 'seller_name',
    render: (row: any) => row._direction === 'income' ? (row.buyer_name || '—') : (row.seller_name || '—'),
  },
  {
    title: '价税合计',
    key: 'total_amount',
    width: 105,
    render: (row: any) => formatMoney(row.total_amount || row.amount || 0),
  },
  {
    title: '方向',
    key: '_direction',
    width: 120,
    render: (row: any) => h(NSelect, {
      value: row._direction,
      'onUpdate:value': (v: string) => { row._direction = v },
      options: directionOptions,
      size: 'tiny',
    }),
  },
  {
    title: '分类',
    key: '_category',
    width: 130,
    render: (row: any) => h(NInput, {
      value: row._category,
      'onUpdate:value': (v: string) => { row._category = v },
      size: 'tiny',
    }),
  },
  {
    title: '操作',
    key: 'action',
    width: 65,
    align: 'center' as const,
    render: (_: any, index: number) => h(NButton, {
      size: 'tiny',
      quaternary: true,
      type: 'error',
      onClick: () => deletePreviewRow(index),
    }, () => '删除'),
  },
])

const validImportableRows = computed(() => classifiedRows.value.filter(r => !r.is_duplicate))
const duplicatesCount = computed(() => classifiedRows.value.filter(r => r.is_duplicate).length)

async function handleImport() {
  if (validImportableRows.value.length === 0) {
    message.warning('预览中的发票均已存在于系统中，无需重复导入')
    return
  }

  // 研发费用分类前置检查
  const incompleteRdRow = validImportableRows.value.find(row =>
    row._category === '研发支出-费用化支出' && (!row._rdProjectId || !row._rdExpenseType)
  )
  if (incompleteRdRow) {
    message.warning(`发票 ${incompleteRdRow.invoice_number || ''} 的研发项目或费用类别未选择`)
    return
  }

  importing.value = true
  try {
    const rows = validImportableRows.value.map(r => ({
      ...r,
      direction: r._direction,
      category: r._category,
      rdProjectId: r._rdProjectId,
      rdExpenseType: r._rdExpenseType,
      rdOriginalAmount: r.amount,
      rdAllocationMethod: 'direct',
    }))
    const result = await importInvoices(rows, false)
    if (result.imported > 0) {
      if (duplicatesCount.value > 0) {
        message.success(`成功导入 ${result.imported} 张发票，已自动剔除 ${duplicatesCount.value} 张重复发票`)
      } else {
        message.success(`成功导入 ${result.imported} 张发票`)
      }
      emit('success')
      emit('update:show', false)
    } else {
      message.warning('未能成功导入发票，请检查文件格式或重试')
    }
  } catch {
    message.error('导入发票失败')
  } finally {
    importing.value = false
  }
}
</script>

<template>
  <SoloDrawer
    :show="show"
    title="导入发票"
    :width="760"
    @update:show="emit('update:show', $event)"
  >
    <div class="import-container">
      <!-- Step 1: Upload -->
      <div v-if="step === 'upload'" class="step-upload">
        <FileDropzone
          accept=".xlsx,.xls"
          title="选择或拖拽税务局发票 Excel 文件"
          subtitle="支持全电发票导出文件（.xlsx / .xls），自动识别进销项与税额"
          :loading="loading"
          @file="handleFile"
          @error="message.error($event)"
        />
        <div class="upload-tip">
          <h4>💡 导入说明</h4>
          <ul>
            <li>请使用税务局电子发票服务平台直接导出的 Excel 文件。</li>
            <li>系统会自动通过购买方和销售方识别进项（支出）与销项（收入）。</li>
            <li>系统自动根据发票号码进行防重拦截，已录入发票不会重复写入。</li>
          </ul>
        </div>
      </div>

      <!-- Step 2: Mapping -->
      <div v-else-if="step === 'mapping'" class="step-mapping">
        <div class="step-header">
          <div>
            <h3>确认数据列映射</h3>
            <p class="step-sub">已自动匹配常见税务局字段，可根据需要调整映射关系</p>
          </div>
          <NSpace>
            <NButton @click="step = 'upload'">重新选择</NButton>
            <NButton type="primary" :loading="loading" @click="proceedToPreview">
              下一步：预览与验证
            </NButton>
          </NSpace>
        </div>

        <div class="mapping-table-wrap">
          <table class="mapping-table">
            <thead>
              <tr>
                <th style="width: 45%;">Excel 列名</th>
                <th style="width: 10%;"></th>
                <th style="width: 45%;">系统对应字段</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="col in excelColumns" :key="col">
                <td class="col-name font-mono">{{ col }}</td>
                <td class="arrow">→</td>
                <td>
                  <NSelect
                    :value="columnMapping[col]"
                    :options="SYSTEM_FIELDS"
                    size="small"
                    filterable
                    @update:value="(v: string) => columnMapping[col] = v"
                  />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Step 3: Preview -->
      <div v-else-if="step === 'preview'" class="step-preview">
        <div class="step-header">
          <div>
            <h3>发票导入预览</h3>
            <p class="step-sub">
              共 {{ classifiedRows.length }} 张发票
              <span v-if="duplicatesCount > 0" class="dup-text">
                （含 {{ duplicatesCount }} 张重复发票，入账时自动剔除）
              </span>
            </p>
          </div>
          <NSpace>
            <NButton @click="step = 'mapping'">返回上一步</NButton>
            <NButton
              type="primary"
              :loading="importing"
              :disabled="validImportableRows.length === 0"
              @click="handleImport"
            >
              确认导入 ({{ validImportableRows.length }} 张)
            </NButton>
          </NSpace>
        </div>

        <NSpin :show="loading">
          <NDataTable
            :columns="previewColumns"
            :data="classifiedRows"
            :bordered="false"
            :single-line="true"
            size="small"
            :max-height="480"
          />
        </NSpin>
      </div>
    </div>
  </SoloDrawer>
</template>

<style scoped>
.import-container {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.step-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 12px;
}
.step-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
  color: var(--solo-text, #1f2937);
}
.step-sub {
  margin: 4px 0 0;
  font-size: 13px;
  color: var(--solo-text-muted, #6b7280);
}
.dup-text {
  color: var(--solo-warning, #f59e0b);
}
.upload-tip {
  margin-top: 16px;
  padding: 14px 16px;
  background: #f9fafb;
  border-radius: 8px;
  border: 1px solid #e5e7eb;
}
.upload-tip h4 {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text, #374151);
}
.upload-tip ul {
  margin: 0;
  padding-left: 20px;
  font-size: 12px;
  color: var(--solo-text-muted, #4b5563);
  line-height: 1.8;
}
.mapping-table-wrap {
  max-height: 480px;
  overflow-y: auto;
  border: 1px solid #e5e7eb;
  border-radius: 6px;
}
.mapping-table {
  width: 100%;
  border-collapse: collapse;
}
.mapping-table th {
  position: sticky;
  top: 0;
  background: #f9fafb;
  padding: 8px 12px;
  font-size: 12px;
  font-weight: 600;
  text-align: left;
  border-bottom: 1px solid #e5e7eb;
  z-index: 1;
}
.mapping-table td {
  padding: 6px 12px;
  border-bottom: 1px solid #f3f4f6;
  font-size: 13px;
}
.col-name {
  color: var(--solo-text, #374151);
}
.arrow {
  text-align: center;
  color: var(--solo-text-muted, #9ca3af);
}
.font-mono {
  font-variant-numeric: tabular-nums;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
}
</style>
