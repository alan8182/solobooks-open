<script setup lang="ts">
import { ref, watch, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { NModal, NInput, NTag, NSpin, NEmpty } from 'naive-ui'
import apiClient from '@/api/client'
import { formatMoney } from '@/utils/format'

const emit = defineEmits<{ close: [] }>()

const router = useRouter()
const query = ref('')
const loading = ref(false)
const results = ref<{ entries: any[]; accounts: any[] }>({ entries: [], accounts: [] })
const selectedIndex = ref(-1)
const flatResults = ref<{ type: string; item: any }[]>([])

let debounceTimer: ReturnType<typeof setTimeout> | null = null

watch(query, (val) => {
  if (debounceTimer) clearTimeout(debounceTimer)
  selectedIndex.value = -1
  if (!val.trim()) {
    results.value = { entries: [], accounts: [] }
    flatResults.value = []
    return
  }
  debounceTimer = setTimeout(() => doSearch(val.trim()), 300)
})

function doSearch(q: string) {
  loading.value = true
  apiClient.get('/api/search', { params: { q } })
    .then((data: any) => {
      results.value = data
      buildFlatList()
    })
    .catch(() => {
      results.value = { entries: [], accounts: [] }
      flatResults.value = []
    })
    .finally(() => { loading.value = false })
}

function buildFlatList() {
  const list: { type: string; item: any }[] = []
  if (results.value.accounts.length > 0) {
    for (const a of results.value.accounts) {
      list.push({ type: 'account', item: a })
    }
  }
  if (results.value.entries.length > 0) {
    for (const e of results.value.entries) {
      list.push({ type: 'entry', item: e })
    }
  }
  flatResults.value = list
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'ArrowDown') {
    e.preventDefault()
    selectedIndex.value = Math.min(selectedIndex.value + 1, flatResults.value.length - 1)
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    selectedIndex.value = Math.max(selectedIndex.value - 1, 0)
  } else if (e.key === 'Enter' && selectedIndex.value >= 0) {
    e.preventDefault()
    navigateTo(flatResults.value[selectedIndex.value])
  } else if (e.key === 'Escape') {
    emit('close')
  }
}

function navigateTo(item: { type: string; item: any }) {
  if (item.type === 'account') {
    router.push('/mine/accounts')
  } else if (item.type === 'entry') {
    router.push(`/ledger/${item.item.id}`)
  }
  emit('close')
}

onMounted(() => {
  nextTick(() => {
    const input = document.querySelector('.global-search-input input')
    if (input) (input as HTMLInputElement).focus()
  })
})
</script>

<template>
  <NModal
    :show="true"
    @update:show="(v: boolean) => { if (!v) emit('close') }"
    preset="card"
    style="width: 520px; max-width: 90vw;"
    ::bordered="false"
    :mask-closable="true"
    :close-on-esc="false"
  >
    <div class="global-search" @keydown="handleKeydown">
      <NInput
        ref="searchInput"
        v-model:value="query"
        placeholder="搜索记账条目、科目…  (↑↓ 选择, Enter 跳转)"
        size="large"
        clearable
        class="global-search-input"
      />

      <NSpin :show="loading" size="small" class="search-spinner">
        <div v-if="flatResults.length > 0" class="search-results">
          <div
            v-for="(r, idx) in flatResults"
            :key="idx"
            class="result-item"
            :class="{ active: idx === selectedIndex }"
            @click="navigateTo(r)"
            @mouseenter="selectedIndex = idx"
          >
            <template v-if="r.type === 'account'">
              <span class="result-icon">📊</span>
              <span class="result-label">{{ r.item.code }} {{ r.item.name }}</span>
              <NTag size="tiny" :bordered="false" type="info">科目</NTag>
            </template>
            <template v-else>
              <span class="result-icon">📝</span>
              <span class="result-label">{{ r.item.title || '无标题' }}</span>
              <span class="result-amount">{{ r.item.amount ? formatMoney(r.item.amount) : '' }}</span>
              <NTag size="tiny" :bordered="false" :type="r.item.direction === 'income' ? 'success' : 'error'">
                {{ r.item.direction === 'income' ? '收入' : '支出' }}
              </NTag>
            </template>
          </div>
        </div>

        <NEmpty v-else-if="query && !loading" description="未找到相关结果" class="search-empty" />
      </NSpin>
    </div>
  </NModal>
</template>

<style scoped>
.global-search {
  min-height: 100px;
}
.global-search-input :deep(input) {
  font-size: 16px;
}
.search-spinner {
  margin-top: 16px;
}
.search-results {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.result-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: var(--solo-radius);
  cursor: pointer;
  transition: background 0.15s;
}
.result-item:hover,
.result-item.active {
  background: var(--solo-border-light);
}
.result-icon {
  font-size: 16px;
}
.result-label {
  flex: 1;
  font-size: 14px;
  color: var(--solo-text-primary);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.result-amount {
  font-size: 13px;
  font-weight: 600;
  color: var(--solo-text-secondary);
  white-space: nowrap;
}
.search-empty {
  padding: 24px;
}
</style>
