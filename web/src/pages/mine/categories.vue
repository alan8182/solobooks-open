<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NButton, NTag, NSpace, useMessage, NInput, NModal, NForm, NFormItem, NSelect, NSpin } from 'naive-ui'
import { useEntryStore } from '@/stores/entry'
import { getCategories, createCategory, updateCategory, deleteCategory } from '@/api/category'
import type { CategoryItem } from '@/api/category'
import { useDeleteConfirm } from '@/composables/useDeleteConfirm'

const router = useRouter()
const message = useMessage()
const entryStore = useEntryStore()
const { confirmDelete } = useDeleteConfirm()

const categories = ref<CategoryItem[]>([])
const loading = ref(false)
const showAddModal = ref(false)
const newCategory = ref({ name: '', direction: 'expense' as const })
const editingId = ref<string | null>(null)
const editValue = ref('')

const usedCategories = ref(new Set<string>())

function refreshUsedCategories() {
  const set = new Set<string>()
  entryStore.activeEntries.forEach((e) => {
    if (e.category) set.add(e.category)
  })
  usedCategories.value = set
}

function isCategoryUsed(cat: string) {
  return usedCategories.value.has(cat)
}

function startEdit(item: CategoryItem) {
  editingId.value = item.id
  editValue.value = item.name
}

async function saveEdit(item: CategoryItem) {
  const val = editValue.value.trim()
  if (!val) {
    message.warning('分类名称不能为空')
    return
  }
  try {
    await updateCategory(item.id, { name: val })
    message.success('已更新')
    editingId.value = null
    await load()
  } catch {
    message.error('更新失败')
  }
}

function cancelEdit() {
  editingId.value = null
}

async function addCategory() {
  const val = newCategory.value.name.trim()
  if (!val) {
    message.warning('请输入分类名称')
    return
  }
  try {
    await createCategory({
      name: val,
      direction: newCategory.value.direction,
    })
    newCategory.value = { name: '', direction: 'expense' }
    showAddModal.value = false
    message.success('分类已添加')
    await load()
  } catch {
    message.error('添加失败')
  }
}

async function removeCategory(item: CategoryItem) {
  if (isCategoryUsed(item.name)) {
    message.warning('该分类已被使用，无法删除')
    return
  }
  try {
    await deleteCategory(item.id)
    message.success('分类已删除')
    await load()
  } catch {
    message.error('删除失败')
  }
}

function requestRemoveCategory(item: CategoryItem) {
  if (isCategoryUsed(item.name)) {
    message.warning('该分类已被使用，无法删除')
    return
  }
  confirmDelete({
    content: `确定删除分类“${item.name}”吗？此操作不可撤销。`,
    onConfirm: () => removeCategory(item),
  })
}

const directionOptions = [
  { label: '支出', value: 'expense' },
  { label: '收入', value: 'income' },
  { label: '资本与往来', value: 'owner_transfer' },
]

const expenseCategories = computed(() =>
  categories.value.filter((c) => c.direction === 'expense'),
)
const incomeCategories = computed(() =>
  categories.value.filter((c) => c.direction === 'income'),
)
const ownerCategories = computed(() =>
  categories.value.filter((c) => c.direction === 'owner_transfer'),
)

async function load() {
  loading.value = true
  try {
    categories.value = await getCategories()
    refreshUsedCategories()
  } catch {
    message.error('加载分类失败')
  } finally {
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <div class="categories-page">
    <div class="page-header">
      <NButton text @click="router.push('/mine')">← 返回</NButton>
      <div class="header-row">
        <h2>分类管理</h2>
        <NButton size="small" @click="showAddModal = true">新增分类</NButton>
      </div>
    </div>

    <NSpin :show="loading">
      <!-- Expense categories -->
      <NCard :bordered="false" class="cat-card mb-4">
        <h3 class="cat-group-title">支出分类</h3>
        <div class="cat-list">
          <div v-for="cat in expenseCategories" :key="cat.id" class="cat-item">
            <template v-if="editingId === cat.id">
              <NInput v-model:value="editValue" size="small" style="flex:1;" @keydown.enter="saveEdit(cat)" />
              <NButton size="tiny" @click="saveEdit(cat)">保存</NButton>
              <NButton size="tiny" @click="cancelEdit">取消</NButton>
            </template>
            <template v-else>
              <span class="cat-name">
                {{ cat.name }}
                <NTag v-if="cat.is_system" size="tiny" :bordered="false" type="info" style="margin-left:6px;">系统</NTag>
              </span>
              <NSpace>
                <NTag v-if="isCategoryUsed(cat.name)" size="tiny" :bordered="false" type="warning">使用中</NTag>
                <NButton text size="tiny" @click="startEdit(cat)">编辑</NButton>
                <NButton
                  text
                  size="tiny"
                  type="error"
                  :disabled="cat.is_system"
                  @click="requestRemoveCategory(cat)"
                >删除</NButton>
              </NSpace>
            </template>
          </div>
        </div>
      </NCard>

      <!-- Income categories -->
      <NCard :bordered="false" class="cat-card mb-4">
        <h3 class="cat-group-title">收入分类</h3>
        <div class="cat-list">
          <div v-for="cat in incomeCategories" :key="cat.id" class="cat-item">
            <span class="cat-name">{{ cat.name }}</span>
            <NSpace>
              <NTag v-if="isCategoryUsed(cat.name)" size="tiny" :bordered="false" type="warning">使用中</NTag>
            </NSpace>
          </div>
        </div>
      </NCard>

      <!-- Owner categories -->
      <NCard :bordered="false" class="cat-card">
        <h3 class="cat-group-title">资本与往来分类</h3>
        <div class="cat-list">
          <div v-for="cat in ownerCategories" :key="cat.id" class="cat-item">
            <span class="cat-name">{{ cat.name }}</span>
            <NSpace>
              <NTag v-if="isCategoryUsed(cat.name)" size="tiny" :bordered="false" type="warning">使用中</NTag>
            </NSpace>
          </div>
        </div>
      </NCard>
    </NSpin>

    <!-- Add modal -->
    <NModal v-model:show="showAddModal" title="新增分类" preset="card" style="width: 400px;">
      <NForm>
        <NFormItem label="分类名称">
          <NInput v-model:value="newCategory.name" placeholder="输入新分类名称" />
        </NFormItem>
        <NFormItem label="方向">
          <NSelect v-model:value="newCategory.direction" :options="directionOptions" />
        </NFormItem>
      </NForm>
      <template #footer>
        <NSpace justify="end">
          <NButton @click="showAddModal = false">取消</NButton>
          <NButton type="primary" @click="addCategory">添加</NButton>
        </NSpace>
      </template>
    </NModal>
  </div>
</template>

<style scoped>
.categories-page {
  max-width: 640px;
  margin: 0 auto;
}
.page-header {
  margin-bottom: 20px;
}
.header-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-top: 8px;
}
.header-row h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.mb-4 { margin-bottom: 16px; }
.cat-card {
  border-radius: var(--solo-radius-lg);
}
.cat-group-title {
  margin: 0 0 12px;
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-secondary);
}
.cat-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.cat-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid var(--solo-border-light);
}
.cat-item:last-child {
  border-bottom: none;
}
.cat-name {
  font-size: 14px;
  color: var(--solo-text-primary);
  display: flex;
  align-items: center;
}
</style>
