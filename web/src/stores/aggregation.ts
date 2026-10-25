/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// 归集维度上下文。用 Pinia 而非 provide/inject 的理由：
//   1. 跨路由存活——6 个页面共享「当前维度」，从 pending 点到 report 应保持同一维度；
//      provide/inject 只活在组件树内，路由切换即销毁，会逼着每页各写一遍 fetch+选中。
//   2. 既有 stores/company.ts 就是这个模式（公司级配置 + 全局共享）。
//   3. meta 是静态枚举，出现在 5 个页面，store 让它天然只请求一次。
//   4. 需要「写」：执行规则后失效缓存、维度为空时跳引导，副作用用 action 表达最自然。

import { defineStore } from 'pinia'
import { computed, ref } from 'vue'
import type { AggregationDimension, AggregationMeta, LabelOption } from '@/api/types'
import { getAggregationMeta, listDimensions } from '@/api/aggregation'

/** 与 auth_token 同风格的 key 命名。不存公司维度——GET /dimensions 本身按 company_id 过滤 */
const STORAGE_KEY = 'aggregation_dimension_id'

function readStoredDimensionId(): string {
  try {
    return localStorage.getItem(STORAGE_KEY) ?? ''
  } catch {
    // 隐私模式等场景下 localStorage 可能抛异常，不能因此让整个页面崩
    return ''
  }
}

function writeStoredDimensionId(id: string): void {
  try {
    if (id) localStorage.setItem(STORAGE_KEY, id)
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    // 持久化失败不影响本次会话使用
  }
}

export const useAggregationStore = defineStore('aggregation', () => {
  const meta = ref<AggregationMeta | null>(null)
  const dimensions = ref<AggregationDimension[]>([])
  const currentDimensionId = ref<string>(readStoredDimensionId())
  const includeArchived = ref(false)
  const loaded = ref(false)
  const loading = ref(false)

  /** meta 的 value→label 映射。中文标签必须来自 GET /meta，前端禁止硬编码 */
  const metaMap = computed(() => ({
    kind: new Map<string, string>((meta.value?.kinds ?? []).map((o) => [o.value, o.label])),
    method: new Map<string, string>((meta.value?.methods ?? []).map((o) => [o.value, o.label])),
    memberStatus: new Map<string, string>(
      (meta.value?.memberStatuses ?? []).map((o) => [o.value, o.label]),
    ),
  }))

  const currentDimension = computed(
    () => dimensions.value.find((d) => d.id === currentDimensionId.value) ?? null,
  )

  const hasDimensions = computed(() => dimensions.value.length > 0)

  const kindOptions = computed<LabelOption[]>(() => meta.value?.kinds ?? [])
  const methodOptions = computed<LabelOption[]>(() => meta.value?.methods ?? [])
  const memberStatusOptions = computed<LabelOption[]>(() => meta.value?.memberStatuses ?? [])

  /** meta 取 value→label 标签；未加载或未收录时回落到原始值，不显示 undefined */
  function labelOf(group: 'kind' | 'method' | 'memberStatus', value: string): string {
    return metaMap.value[group].get(value) ?? value
  }

  /** meta 只拉一次 */
  async function ensureMeta(): Promise<void> {
    if (!meta.value) meta.value = await getAggregationMeta()
  }

  /**
   * 拉取维度列表。已加载且未 force 时跳过。
   * 读回的持久化 id 若不在列表里（换公司/维度被删），回退到第一个。
   */
  async function loadDimensions(force = false): Promise<void> {
    if (loaded.value && !force) return
    loading.value = true
    try {
      dimensions.value = await listDimensions(includeArchived.value)
      if (!dimensions.value.some((d) => d.id === currentDimensionId.value)) {
        currentDimensionId.value = dimensions.value[0]?.id ?? ''
      }
      loaded.value = true
    } finally {
      loading.value = false
    }
  }

  /** 页面 onMounted 的统一入口：meta 一次 + 维度列表一次 */
  async function bootstrap(): Promise<void> {
    await ensureMeta()
    await loadDimensions()
  }

  function setCurrentDimension(id: string): void {
    currentDimensionId.value = id
    writeStoredDimensionId(id)
  }

  /** 建/改维度或成员后调用，下次 loadDimensions 会重新拉 */
  function invalidate(): void {
    loaded.value = false
  }

  /** 切换公司时调用：维度列表按 company_id 过滤，必须整体丢弃 */
  function reset(): void {
    meta.value = null
    dimensions.value = []
    currentDimensionId.value = ''
    loaded.value = false
    loading.value = false
    writeStoredDimensionId('')
  }

  return {
    meta,
    dimensions,
    currentDimensionId,
    currentDimension,
    hasDimensions,
    metaMap,
    kindOptions,
    methodOptions,
    memberStatusOptions,
    includeArchived,
    loaded,
    loading,
    bootstrap,
    ensureMeta,
    loadDimensions,
    setCurrentDimension,
    invalidate,
    reset,
    labelOf,
  }
})
