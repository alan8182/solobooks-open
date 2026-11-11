<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import {
  NButton,
  NCheckbox,
  NInput,
  NInputNumber,
  NModal,
  NSelect,
  NSpin,
  NSwitch,
  NTag,
  useDialog,
  useMessage,
} from 'naive-ui'
import apiClient from '@/api/client'
import { getCategories, type CategoryItem } from '@/api/category'
import { accountingErrorText } from '@/utils/errors'
import { SoloDrawer, SoloEmpty } from '@/components/ui'

const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ 'update:show': [value: boolean] }>()

const message = useMessage()
const dialog = useDialog()

const loading = ref(false)
const rules = ref<any[]>([])
const categories = ref<CategoryItem[]>([])
const total = ref(0)
const keyword = ref('')
const direction = ref<'' | 'income' | 'expense'>('')
const includeDisabled = ref(true)
const categoryOptionsLoaded = ref(false)

const directionOptions = [
  { label: '全部收支', value: '' },
  { label: '收入', value: 'income' },
  { label: '支出', value: 'expense' },
]

async function loadRules() {
  loading.value = true
  try {
    const result: any = await apiClient.get('/api/bank/category-rules', {
      params: {
        q: keyword.value.trim(),
        direction: direction.value,
        ...(includeDisabled.value ? {} : { enabled: true }),
        limit: 200,
      },
    })
    rules.value = result.items || []
    total.value = Number(result.total || 0)
  } catch (error) {
    message.error(accountingErrorText(error, '分类规则加载失败'))
  } finally {
    loading.value = false
  }
}

async function ensureCategories() {
  if (categoryOptionsLoaded.value) return
  try {
    categories.value = await getCategories()
    categoryOptionsLoaded.value = true
  } catch {
    message.warning('分类选项加载失败，暂时不能修改分类')
  }
}

watch(
  () => props.show,
  (show) => {
    if (show) {
      void Promise.all([loadRules(), ensureCategories()])
    }
  },
  { immediate: true },
)

const editVisible = ref(false)
const editingRule = ref<any>(null)
const editSaving = ref(false)
const editForm = ref({
  category: '',
  purposeKey: '',
  confidence: 0.98,
})

const editCategoryOptions = computed(() => {
  const ruleDirection = String(editingRule.value?.direction || '')
  return categories.value
    .filter(item => (
      ruleDirection === 'income'
        ? item.direction === 'income' || item.direction === 'owner_transfer'
        : item.direction === 'expense' || item.direction === 'owner_transfer'
    ))
    .map(item => ({ label: item.name, value: item.name }))
})

function openEdit(rule: any) {
  editingRule.value = rule
  editForm.value = {
    category: rule.category || '',
    purposeKey: rule.purposeKey || '',
    confidence: Number(rule.storedConfidence || rule.confidence || 0.98),
  }
  editVisible.value = true
}

async function saveEdit() {
  if (!editingRule.value?.id || !editForm.value.category) return
  editSaving.value = true
  try {
    await apiClient.patch(`/api/bank/category-rules/${encodeURIComponent(editingRule.value.id)}`, {
      category: editForm.value.category,
      purposeKey: editForm.value.purposeKey.trim(),
      confidence: Number(editForm.value.confidence || 0.98),
    })
    message.success('规则已更新')
    editVisible.value = false
    await loadRules()
  } catch (error) {
    message.error(accountingErrorText(error, '规则更新失败'))
  } finally {
    editSaving.value = false
  }
}

async function toggleEnabled(rule: any, enabled: boolean) {
  const previous = rule.enabled
  rule.enabled = enabled
  try {
    await apiClient.patch(`/api/bank/category-rules/${encodeURIComponent(rule.id)}`, { enabled })
    message.success(enabled ? '规则已启用' : '规则已停用，后续流水不会自动命中')
  } catch (error) {
    rule.enabled = previous
    message.error(accountingErrorText(error, '规则状态更新失败'))
  }
}

function confirmDeleteRule(rule: any) {
  dialog.warning({
    title: '删除流水分类规则',
    content: `删除后，后续「${rule.counterpartyKey || rule.purposeKey}」流水将重新逐笔判断。已经入账的凭证不受影响。`,
    positiveText: '删除规则',
    negativeText: '取消',
    onPositiveClick: async () => {
      try {
        await apiClient.delete(`/api/bank/category-rules/${encodeURIComponent(rule.id)}`)
        message.success('规则已删除')
        await loadRules()
      } catch (error) {
        message.error(accountingErrorText(error, '规则删除失败'))
        throw error
      }
    },
  })
}

function ruleTitle(rule: any) {
  if (rule.counterpartyKey && rule.purposeKey) {
    return `${rule.counterpartyKey} · ${rule.purposeKey}`
  }
  return rule.counterpartyKey || rule.purposeKey || rule.matchPattern
}

function formatDate(value: unknown) {
  return value ? String(value).replace('T', ' ').slice(0, 16) : '尚未命中'
}
</script>

<template>
  <SoloDrawer
    :show="show"
    title="流水分类规则"
    :width="760"
    @update:show="emit('update:show', $event)"
  >
    <div class="rule-toolbar">
      <NInput
        v-model:value="keyword"
        clearable
        placeholder="搜索对方、用途、分类或科目"
        @keyup.enter="loadRules"
      >
        <template #prefix><i class="ri-search-line" /></template>
      </NInput>
      <NSelect
        v-model:value="direction"
        :options="directionOptions"
        @update:value="loadRules"
      />
      <NButton secondary :loading="loading" @click="loadRules">查询</NButton>
    </div>
    <div class="rule-guidance">
      <div>
        <strong>按“交易对方 + 业务用途”记忆</strong>
        <span>同一供应商的不同业务会分别命中；只记住对方的宽泛规则必须逐笔复核。</span>
      </div>
      <NCheckbox v-model:checked="includeDisabled" @update:checked="loadRules">
        显示已停用
      </NCheckbox>
    </div>

    <NSpin :show="loading">
      <SoloEmpty
        v-if="!loading && rules.length === 0"
        variant="hint"
        title="还没有流水分类规则"
        description="在月度结账中确认流水时勾选“记住此规则”，系统会在这里生成可管理的企业规则。"
      />
      <div v-else class="rule-list">
        <div class="rule-list-meta">共 {{ total }} 条规则</div>
        <article
          v-for="rule in rules"
          :key="rule.id"
          class="rule-card"
          :class="{ 'is-disabled': !rule.enabled }"
        >
          <div class="rule-card-main">
            <div class="rule-title-row">
              <strong>{{ ruleTitle(rule) }}</strong>
              <NTag size="small" :bordered="false" :type="rule.purposeKey ? 'info' : 'warning'">
                {{ rule.purposeKey ? '精确规则' : '仅对方，需复核' }}
              </NTag>
              <NTag
                size="small"
                :bordered="false"
                :type="rule.direction === 'income' ? 'success' : 'default'"
              >
                {{ rule.direction === 'income' ? '收入' : '支出' }}
              </NTag>
            </div>
            <div class="rule-target">
              <span>{{ rule.category }}</span>
              <code v-if="rule.accountCode">{{ rule.accountCode }}</code>
              <span class="confidence">{{ Math.round(Number(rule.confidence || 0) * 100) }}%</span>
            </div>
            <div class="rule-stats">
              <span>命中 {{ rule.hitCount }} 次</span>
              <span>最后命中 {{ formatDate(rule.lastHitAt) }}</span>
              <span v-if="rule.overrideCount">已覆盖修改 {{ rule.overrideCount }} 次</span>
              <span>来源流水 {{ rule.sourceStatementId || '—' }}</span>
            </div>
          </div>
          <div class="rule-actions">
            <NSwitch
              :value="rule.enabled"
              size="small"
              @update:value="(value: boolean) => toggleEnabled(rule, value)"
            >
              <template #checked>启用</template>
              <template #unchecked>停用</template>
            </NSwitch>
            <NButton size="tiny" quaternary @click="openEdit(rule)">编辑</NButton>
            <NButton size="tiny" quaternary type="error" @click="confirmDeleteRule(rule)">删除</NButton>
          </div>
        </article>
      </div>
    </NSpin>
  </SoloDrawer>

  <NModal
    v-model:show="editVisible"
    preset="card"
    title="编辑流水分类规则"
    style="width: 540px;"
  >
    <div class="edit-form">
      <label>
        <span>分类</span>
        <NSelect
          v-model:value="editForm.category"
          :options="editCategoryOptions"
          filterable
          placeholder="选择分类"
        />
      </label>
      <label>
        <span>业务用途</span>
        <NInput
          v-model:value="editForm.purposeKey"
          maxlength="160"
          placeholder="留空表示只按交易对方匹配，此类规则必须逐笔复核"
        />
      </label>
      <label>
        <span>规则置信度</span>
        <NInputNumber
          v-model:value="editForm.confidence"
          :min="0.5"
          :max="1"
          :step="0.01"
          style="width: 180px;"
        />
      </label>
    </div>
    <template #footer>
      <div class="edit-footer">
        <NButton @click="editVisible = false">取消</NButton>
        <NButton type="primary" :loading="editSaving" @click="saveEdit">保存</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.rule-toolbar {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) 130px auto;
  gap: 10px;
}

.rule-guidance {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 20px;
  margin-top: 14px;
  padding: 12px 14px;
  border: 1px solid var(--solo-border, #e5e8ee);
  border-radius: 10px;
  background: var(--solo-surface-soft, #f7f8fa);
}

.rule-guidance strong,
.rule-guidance span {
  display: block;
}

.rule-guidance strong {
  color: var(--solo-text-primary, #182230);
  font-size: 13px;
}

.rule-guidance span {
  margin-top: 3px;
  color: var(--solo-text-secondary, #667085);
  font-size: 12px;
  line-height: 1.6;
}

.rule-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-top: 14px;
}

.rule-list-meta {
  color: var(--solo-text-muted, #98a2b3);
  font-size: 12px;
}

.rule-card {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  padding: 14px;
  border: 1px solid var(--solo-border, #e5e8ee);
  border-radius: 12px;
  background: var(--solo-surface, #fff);
}

.rule-card.is-disabled {
  opacity: 0.62;
  background: var(--solo-surface-soft, #f7f8fa);
}

.rule-title-row,
.rule-target,
.rule-stats,
.rule-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.rule-title-row strong {
  color: var(--solo-text-primary, #182230);
  font-size: 14px;
}

.rule-target {
  margin-top: 8px;
  color: var(--solo-primary, #3157c8);
  font-size: 13px;
  font-weight: 600;
}

.rule-target code {
  padding: 1px 6px;
  border-radius: 4px;
  background: #eef2ff;
  color: #435c9d;
  font-size: 11px;
}

.rule-target .confidence {
  color: var(--solo-text-secondary, #667085);
  font-size: 12px;
  font-weight: 500;
}

.rule-stats {
  margin-top: 9px;
  color: var(--solo-text-muted, #98a2b3);
  font-size: 11px;
}

.rule-actions {
  align-content: flex-start;
  justify-content: flex-end;
  min-width: 120px;
}

.edit-form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.edit-form label {
  display: grid;
  gap: 7px;
}

.edit-form label > span {
  color: var(--solo-text-secondary, #667085);
  font-size: 12px;
}

.edit-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}

@media (max-width: 680px) {
  .rule-toolbar {
    grid-template-columns: 1fr;
  }

  .rule-guidance,
  .rule-card {
    grid-template-columns: 1fr;
    flex-direction: column;
  }

  .rule-card {
    display: flex;
  }

  .rule-actions {
    justify-content: flex-start;
  }
}
</style>
