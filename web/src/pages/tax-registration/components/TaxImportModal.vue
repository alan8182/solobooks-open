<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import {
  NButton,
  NInput,
  NModal,
  NRadioButton,
  NRadioGroup,
  NSpace,
  NTabPane,
  NTabs,
  useMessage,
} from 'naive-ui'
import {
  recognizeTaxRegistration,
  type TaxRegistrationRow,
} from '@/api/tax-registration'
import { SoloEmpty } from '@/components/ui'
import { accountingErrorText } from '@/utils/errors'
import {
  TAX_REGISTRATION_COLUMNS,
  cycleLabel,
  formatRate,
  formatTaxRate,
  parsePastedTable,
} from '@/utils/taxRegistration'

const props = defineProps<{
  show: boolean
  importing: boolean
}>()

const emit = defineEmits<{
  'update:show': [value: boolean]
  import: [
    payload: {
      rows: Array<Record<string, unknown>>
      mode: 'merge' | 'replace'
      source: 'paste' | 'ocr'
    },
  ]
}>()

const message = useMessage()

const importTab = ref<'paste' | 'ocr'>('paste')
const importMode = ref<'merge' | 'replace'>('merge')
const pasteText = ref('')
const recognizedRows = ref<TaxRegistrationRow[]>([])
const ocrLoading = ref(false)
const fileInput = ref<HTMLInputElement | null>(null)

const parsedPaste = computed(() => parsePastedTable(pasteText.value))

const importPreviewRows = computed<Array<Record<string, string>>>(() => {
  if (importTab.value === 'paste') return parsedPaste.value.rows
  return recognizedRows.value.map((row) => ({
    征收项目: row.levyItem,
    征收品目: row.levyCategory,
    征收子目: row.levySubItem,
    纳税期限: cycleLabel(row.filingCycle),
    申报期限: row.deadlineText || row.deadlineRuleLabel,
    征收率: row.levyRate === null ? '—' : formatRate(row.levyRate),
    税率或单位税额: formatTaxRate(row),
    主附税标志: row.mainFlag === 'vat_attachment' ? '增值税附税' : '主税',
    征收代理方式: row.collectionMethod === 'withholding' ? '代扣代缴' : '自行申报',
    归属申报表: row.returnGroupLabel,
  }))
})

async function handleFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  ocrLoading.value = true
  try {
    const result = await recognizeTaxRegistration(file)
    recognizedRows.value = result.items
    importTab.value = 'ocr'
    if (result.errors.length) {
      message.warning(`识别到 ${result.items.length} 行，${result.errors.length} 行需手工补全`)
    } else {
      message.success(`识别到 ${result.items.length} 行，请核对小数位后导入`)
    }
  } catch (error) {
    message.error(accountingErrorText(error, '识别失败，请改用复制粘贴'))
  } finally {
    ocrLoading.value = false
    input.value = ''
  }
}

function handleConfirm() {
  const payload =
    importTab.value === 'paste'
      ? (parsedPaste.value.rows as unknown as Array<Record<string, unknown>>)
      : (recognizedRows.value as unknown as Array<Record<string, unknown>>)
  if (!payload.length) {
    message.warning('没有可导入的行')
    return
  }
  emit('import', {
    rows: payload,
    mode: importMode.value,
    source: importTab.value,
  })
}

function handleClose() {
  emit('update:show', false)
}

function reset() {
  pasteText.value = ''
  recognizedRows.value = []
  importTab.value = 'paste'
  importMode.value = 'merge'
}

defineExpose({
  reset,
})
</script>

<template>
  <NModal
    :show="show"
    preset="card"
    style="width: 900px; max-width: 95vw"
    title="从电子税务局同步认定信息"
    @update:show="emit('update:show', $event)"
  >
    <div class="route-banner">
      <i class="ri-compass-3-line banner-icon" />
      <span>
        <strong>电子税务局快捷路径：</strong>
        登录电子税务局 ➔【热门服务】➔【税务数字账户】➔【账户查询】➔【税费种认定信息】
      </span>
    </div>

    <NTabs v-model:value="importTab" type="line" animated>
      <NTabPane name="paste" tab="方式一：复制粘贴表格（推荐）">
        <p class="tab-hint">
          在电子税务局「税费种认定信息」页面框选整张表格（按住鼠标左键全选并 Ctrl+C），直接粘贴到下方文本框。列顺序与页面一致即可，表头与空行会自动过滤。
        </p>
        <NInput
          v-model:value="pasteText"
          type="textarea"
          :rows="6"
          placeholder="征收项目&#9;征收品目&#9;征收子目&#9;纳税期限&#9;申报期限&#9;征收率&#9;税率或单位税额&#9;主附税标志&#9;征收代理方式"
          class="paste-area"
        />
      </NTabPane>

      <NTabPane name="ocr" tab="方式二：上传页面截图（AI 识别）">
        <p class="tab-hint">
          上传电子税务局税费种认定清晰截图（支持 PNG/JPG）。识别后请核对小数位（如 3% 与 0.03），确认无误后导入。
        </p>
        <div class="ocr-upload-box">
          <input
            ref="fileInput"
            type="file"
            accept="image/*"
            class="file-input"
            @change="handleFile"
          >
          <NButton size="small" :loading="ocrLoading" @click="fileInput?.click()">
            <template #icon><i class="ri-image-add-line" /></template>
            选择截图识别
          </NButton>
          <span v-if="recognizedRows.length" class="ocr-status">
            ✓ 已识别出 {{ recognizedRows.length }} 行税种认定数据
          </span>
        </div>
      </NTabPane>
    </NTabs>

    <div class="import-preview">
      <div class="import-preview-head">
        <span class="preview-count-title">
          数据预览：解析到 <strong>{{ importPreviewRows.length }}</strong> 行认定
          <template v-if="importTab === 'paste' && parsedPaste.skipped">
            （已自动跳过 {{ parsedPaste.skipped }} 行表头或无效行）
          </template>
        </span>
      </div>

      <div v-if="importPreviewRows.length" class="mini-table-wrap">
        <table class="mini-table">
          <thead>
            <tr>
              <th v-for="column in [...TAX_REGISTRATION_COLUMNS, '归属申报表']" :key="column">
                {{ column }}
              </th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="(row, index) in importPreviewRows.slice(0, 12)" :key="index">
              <td v-for="column in [...TAX_REGISTRATION_COLUMNS, '归属申报表']" :key="column">
                {{ row[column] || '—' }}
              </td>
            </tr>
          </tbody>
        </table>
        <p v-if="importPreviewRows.length > 12" class="more-hint">
          仅预览前 12 行，共 {{ importPreviewRows.length }} 行将被导入。
        </p>
      </div>

      <SoloEmpty
        v-else
        variant="hint"
        title="等待粘贴或上传数据"
        description="粘贴表格文本或上传截图后，解析结果会即时呈现在此预览区。"
        class="empty-preview-box"
      />
    </div>

    <template #footer>
      <div class="import-footer">
        <NRadioGroup v-model:value="importMode" size="small">
          <NRadioButton value="merge">合并更新（仅更新出现税种）</NRadioButton>
          <NRadioButton value="replace">全量替换（未出现税种置为停用）</NRadioButton>
        </NRadioGroup>
        <NSpace :size="8">
          <NButton size="small" @click="handleClose">取消</NButton>
          <NButton
            size="small"
            type="primary"
            :loading="importing"
            :disabled="!importPreviewRows.length"
            @click="handleConfirm"
          >
            确认导入认定数据
          </NButton>
        </NSpace>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.route-banner {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--solo-bg-blue-soft);
  color: var(--solo-primary);
  border: 1px solid color-mix(in srgb, var(--solo-primary) 20%, transparent);
  border-radius: 6px;
  padding: 8px 12px;
  font-size: 12px;
  margin-bottom: 14px;
}
.banner-icon {
  font-size: 16px;
}
.tab-hint {
  font-size: 12px;
  color: var(--solo-text-secondary);
  line-height: 1.6;
  margin: 0 0 10px;
}
.paste-area {
  font-family: var(--solo-font-mono, monospace);
  font-size: 12px;
}
.ocr-upload-box {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 16px 0;
}
.file-input {
  display: none;
}
.ocr-status {
  font-size: 12px;
  color: var(--solo-income);
  font-weight: 500;
}
.import-preview {
  margin-top: 14px;
  border-top: 1px solid var(--solo-border);
  padding-top: 12px;
}
.import-preview-head {
  display: flex;
  justify-content: space-between;
  font-size: 12px;
  color: var(--solo-text-secondary);
  margin-bottom: 8px;
}
.preview-count-title strong {
  color: var(--solo-primary);
}
.mini-table-wrap {
  max-height: 280px;
  overflow: auto;
  border: 1px solid var(--solo-border);
  border-radius: 6px;
}
.mini-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
}
.mini-table th,
.mini-table td {
  padding: 6px 10px;
  border-bottom: 1px solid var(--solo-border);
  text-align: left;
  white-space: nowrap;
  color: var(--solo-text-primary);
}
.mini-table th {
  position: sticky;
  top: 0;
  background: var(--solo-bg-soft);
  color: var(--solo-text-secondary);
  font-weight: 600;
}
.more-hint {
  font-size: 11px;
  color: var(--solo-text-muted);
  margin: 6px 8px;
}
.empty-preview-box {
  padding: 24px 0;
}
.import-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
</style>
