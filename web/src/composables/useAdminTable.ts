/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { getCurrentInstance, onMounted, ref, watch, type Ref } from 'vue'
import { useMessage } from 'naive-ui'
import { useAdminStore } from '@/stores/admin'

export interface AdminTablePagination {
  page: number
  pageSize: number
  showSizePicker?: boolean
  pageSizes?: number[]
  itemCount?: number
  onChange?: (page: number) => void
  onUpdatePageSize?: (pageSize: number) => void
}

export interface UseAdminTableOptions<T> {
  defaultPageSize?: number
  immediate?: boolean
  loadData: (params: { page: number; pageSize: number; query: string }) => Promise<{ items: T[]; total: number } | T[]>
}

export function useAdminTable<T>(options: UseAdminTableOptions<T>) {
  const adminStore = useAdminStore()
  let message: ReturnType<typeof useMessage> | null = null
  if (getCurrentInstance()) {
    try {
      message = useMessage()
    } catch {
      // 单元测试或非组件上下文环境
    }
  }

  const loading = ref(false)
  const items = ref<T[]>([]) as Ref<T[]>
  const total = ref(0)
  const page = ref(1)
  const pageSize = ref(options.defaultPageSize ?? 20)
  const query = ref('')

  async function load(): Promise<void> {
    loading.value = true
    try {
      const result = await options.loadData({
        page: page.value,
        pageSize: pageSize.value,
        query: query.value.trim(),
      })
      if (Array.isArray(result)) {
        items.value = result
        total.value = result.length
      } else {
        items.value = result.items
        total.value = result.total
      }
    } catch (err: any) {
      message?.error(err?.response?.data?.detail || '数据加载失败，请重试')
    } finally {
      loading.value = false
    }
  }

  function onSearch(): void {
    page.value = 1
    void load()
  }

  function onReset(): void {
    query.value = ''
    page.value = 1
    void load()
  }

  function onPageChange(newPage: number): void {
    page.value = newPage
    void load()
  }

  function onPageSizeChange(newSize: number): void {
    pageSize.value = newSize
    page.value = 1
    void load()
  }

  // 监听管理台全局刷新信号
  watch(
    () => adminStore.refreshSignal,
    () => {
      void load()
    },
  )

  if (options.immediate !== false) {
    onMounted(() => {
      void load()
    })
  }

  return {
    loading,
    items,
    total,
    page,
    pageSize,
    query,
    load,
    onSearch,
    onReset,
    onPageChange,
    onPageSizeChange,
  }
}

/** 统一日期时间格式化工具，替换原有 5 个组件中重复的代码 */
export function formatAdminDateTime(value: string | null | undefined): string {
  if (!value) return '—'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 格式化数值 */
export function formatAdminNumber(value: number | null | undefined): string {
  return typeof value === 'number' ? value.toLocaleString('zh-CN') : '—'
}
