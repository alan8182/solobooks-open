<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NInput, NButton, NCard, useMessage, NSpace, NAlert, NTag, NCheckbox, NSelect } from 'naive-ui'
import { recordBatch, recordConfirm } from '@/api/record'
import { useEntryStore } from '@/stores/entry'
import { formatMoney } from '@/utils/format'
import { getEntryNaturePresentation } from '@/utils/entryNature'
import type { BatchEntry, RdExpenseType, RdProject } from '@/api/types'
import { getRdExpenseTypes, getRdProjects } from '@/api/rd'

const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()

const text = ref('')
const splitting = ref(false)
const entries = ref<BatchEntry[]>([])
const error = ref('')
const selectedIndices = ref<number[]>([])
const confirmingBatch = ref(false)
const rdProjects = ref<RdProject[]>([])
const rdExpenseTypeOptions = ref<{ label: string; value: RdExpenseType }[]>([])
const rdSelections = ref<Record<number, { projectId?: string; expenseType?: RdExpenseType }>>({})

onMounted(async () => {
  try {
    const [projects, types] = await Promise.all([getRdProjects(), getRdExpenseTypes()])
    rdProjects.value = projects
    rdExpenseTypeOptions.value = types.items.map((item) => ({ label: item.label, value: item.value }))
  } catch {
    rdProjects.value = []
  }
})

const rdProjectOptions = () => rdProjects.value
  .filter((project) => project.status === 'active')
  .map((project) => ({ label: `${project.code} ${project.name}`, value: project.id }))

const suggestion = `今天业务：
1. 收到 A 公司货款 30000 元
2. 支付办公室房租 8000 元
3. 购买办公用品 450 元
4. 员工报销差旅费 1200 元`

async function handleSplit() {
  if (!text.value.trim()) {
    message.warning('请输入要批量记账的内容')
    return
  }
  error.value = ''
  entries.value = []
  splitting.value = true
  try {
    const resp = await recordBatch(text.value)
    entries.value = resp.entries || []
    rdSelections.value = Object.fromEntries(entries.value.map((_, index) => [index, {}]))
    selectedIndices.value = entries.value.map((_, i) => i)
    if (entries.value.length === 0) {
      message.warning('未能识别出任何条目，请调整描述')
    }
  } catch (err: unknown) {
    const resp = err as { response?: { data?: { detail?: string } } }
    error.value = resp?.response?.data?.detail || '拆分失败，请重试'
  } finally {
    splitting.value = false
  }
}

function directionPresentation(dir: string, category = '') {
  return getEntryNaturePresentation(dir, category)
}

function toggleAll() {
  if (selectedIndices.value.length === entries.value.length) {
    selectedIndices.value = []
  } else {
    selectedIndices.value = entries.value.map((_, i) => i)
  }
}

async function handleConfirmAll() {
  const selected = selectedIndices.value.map((i) => entries.value[i]).filter(Boolean)
  if (selected.length === 0) {
    message.warning('请选择要确认的条目')
    return
  }
  const missingRd = selectedIndices.value.some((index) => {
    const entry = entries.value[index]
    const selection = rdSelections.value[index]
    return entry?.category === '研发支出-费用化支出' && (!selection?.projectId || !selection?.expenseType)
  })
  if (missingRd) {
    message.warning('研发费用条目必须选择研发项目和费用类别')
    return
  }
  confirmingBatch.value = true
  let success = 0
  let fail = 0
  for (const index of selectedIndices.value) {
    const entry = entries.value[index]
    if (!entry) continue
    try {
      await recordConfirm({
        direction: entry.direction,
        amount: entry.amount,
        title: entry.title,
        category: entry.category,
        accountCode: entry.accountCode,
        accountName: entry.accountName,
        payer: entry.payer || 'company_account',
        occurDate: new Date().toISOString().split('T')[0],
        sourceType: 'web_batch',
        ...(entry.category === '研发支出-费用化支出' ? {
          rdProjectId: rdSelections.value[index]?.projectId,
          rdExpenseType: rdSelections.value[index]?.expenseType,
          rdOriginalAmount: entry.amount,
          rdAllocationMethod: 'direct' as const,
        } : {}),
      })
      success++
    } catch {
      fail++
    }
  }
  // Refresh store
  await entryStore.fetchEntries()
  confirmingBatch.value = false
  message.success(`确认完成：成功 ${success} 条${fail ? `，失败 ${fail} 条` : ''}`)
  router.push('/ledger')
}
</script>

<template>
  <div class="batch-page">
    <div class="page-header">
      <h2>批量记账</h2>
      <p class="text-muted">一次性录入多笔交易，AI 智能拆分</p>
    </div>

    <NAlert v-if="error" type="error" closable class="mb-4" @close="error = ''">
      {{ error }}
    </NAlert>

    <!-- Input -->
    <NCard :bordered="false" class="input-card" v-if="entries.length === 0">
      <div class="suggestion-hint" @click="text = suggestion">
        <span class="hint-label">参考格式：</span>
        <pre class="suggestion-text">{{ suggestion }}</pre>
      </div>

      <NInput
        v-model:value="text"
        type="textarea"
        :rows="8"
        placeholder="输入多笔交易描述，每笔一行或编号..."
        :disabled="splitting"
        class="text-input"
      />

      <NSpace justify="end" class="mt-4">
        <NButton @click="router.push('/')">取消</NButton>
        <NButton type="primary" :loading="splitting" @click="handleSplit" size="large">
          AI 拆分
        </NButton>
      </NSpace>
    </NCard>

    <!-- Results -->
    <div v-if="entries.length > 0" class="results-section">
      <div class="results-header">
        <div>
          <h3>识别到 {{ entries.length }} 笔交易</h3>
        </div>
        <NSpace>
          <NButton text @click="() => { entries = []; text = '' }">重新输入</NButton>
        </NSpace>
      </div>

      <div class="batch-select-all">
        <NCheckbox :checked="selectedIndices.length === entries.length" @click="toggleAll">
          全选（{{ selectedIndices.length }}/{{ entries.length }}）
        </NCheckbox>
      </div>

      <div class="entry-list">
        <div
          v-for="(entry, index) in entries"
          :key="index"
          class="entry-card"
          :style="{ borderLeftColor: directionPresentation(entry.direction, entry.category).color }"
        >
          <div class="entry-header">
            <NCheckbox :checked="selectedIndices.includes(index)" @click="() => {
              const idx = selectedIndices.indexOf(index)
              if (idx >= 0) selectedIndices.splice(idx, 1)
              else selectedIndices.push(index)
            }" />
            <div class="entry-main">
              <div class="entry-title">{{ entry.title }}</div>
              <div class="entry-meta">
                <NTag size="tiny" :bordered="false" :color="{ textColor: directionPresentation(entry.direction, entry.category).color, borderColor: 'transparent', color: directionPresentation(entry.direction, entry.category).background }">
                  {{ directionPresentation(entry.direction, entry.category).detailLabel }}
                </NTag>
                <span class="category-tag">{{ entry.category }}</span>
              </div>
            </div>
            <span class="entry-amount" :style="{ color: directionPresentation(entry.direction, entry.category).color }">
              {{ entry.direction === 'income' ? '+' : '-' }}{{ formatMoney(entry.amount) }}
            </span>
          </div>
          <div v-if="entry.category === '研发支出-费用化支出'" class="rd-selectors">
            <NSelect v-model:value="rdSelections[index].projectId" :options="rdProjectOptions()" filterable placeholder="选择研发项目" />
            <NSelect v-model:value="rdSelections[index].expenseType" :options="rdExpenseTypeOptions" placeholder="选择费用类别" />
          </div>
        </div>
      </div>

      <NSpace justify="center" class="mt-6">
        <NButton
          type="primary"
          size="large"
          :loading="confirmingBatch"
          :disabled="selectedIndices.length === 0"
          @click="handleConfirmAll"
        >
          确认入账（{{ selectedIndices.length }} 条）
        </NButton>
      </NSpace>
    </div>
  </div>
</template>

<style scoped>
.batch-page {
  max-width: 640px;
  margin: 0 auto;
}
.page-header {
  margin-bottom: 20px;
}
.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.text-muted {
  color: var(--solo-text-secondary);
  font-size: 13px;
  margin-top: 4px;
}
.mb-4 {
  margin-bottom: 16px;
}
.mt-4 {
  margin-top: 16px;
}
.mt-6 {
  margin-top: 24px;
}
.input-card {
  border-radius: var(--solo-radius-lg);
}
.suggestion-hint {
  margin-bottom: 16px;
  padding: 12px;
  background: var(--solo-border-light);
  border-radius: var(--solo-radius);
  cursor: pointer;
  transition: background 0.15s;
}
.suggestion-hint:hover {
  background: #e2e8f0;
}
.hint-label {
  font-size: 12px;
  color: var(--solo-text-muted);
  display: block;
  margin-bottom: 4px;
}
.suggestion-text {
  margin: 0;
  font-size: 12px;
  color: var(--solo-text-secondary);
  line-height: 1.6;
  white-space: pre-wrap;
}
.text-input {
  font-size: 14px;
}
.results-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.results-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.results-header h3 {
  margin: 0;
  font-size: 16px;
  font-weight: 600;
}
.batch-select-all {
  padding: 8px 4px;
}
.entry-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.entry-card {
  background: #fff;
  border-radius: var(--solo-radius);
  border-left: 3px solid;
  padding: 16px;
  box-shadow: var(--solo-shadow-sm);
}
.rd-selectors {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px;
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--solo-border-light);
}
.entry-header {
  display: flex;
  align-items: center;
  gap: 12px;
}
.entry-main {
  flex: 1;
  min-width: 0;
}
.entry-title {
  font-size: 14px;
  font-weight: 500;
  color: var(--solo-text-primary);
  margin-bottom: 4px;
}
.entry-meta {
  display: flex;
  gap: 6px;
  align-items: center;
}
.category-tag {
  font-size: 12px;
  color: var(--solo-text-muted);
}
.entry-amount {
  font-size: 15px;
  font-weight: 600;
  white-space: nowrap;
}
</style>
