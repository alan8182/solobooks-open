<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
// 归集维度选择器。5 个子页共用同一份「维度选择 + meta 标签」逻辑，
// 拆独立组件是为了避免某一页忘了接 store（设计文档 §5「为什么这么拆」）。
//
// 铁律（设计文档 §9 #14）：**不设 clearable**，维度恒有值。
// 后端 aggregation_service.py:829-831 在 dimensionId 为空且 state='all' 时会跳过
// allocated 行，用户看到的清单语义不直观，所以前端根本不提供「不选维度」这个选项；
// 由 store.bootstrap() 默认选中第一个维度并持久化到 localStorage。
import { computed } from 'vue'
import { NButton, NSelect, NSpace, NText } from 'naive-ui'
import type { SelectOption } from 'naive-ui'
import { useAggregationStore } from '@/stores/aggregation'

const store = useAggregationStore()

/**
 * option label 形如 `RD · 研发项目（项目）`。
 * kind 的中文标签来自 GET /meta（store.labelOf），前端不硬编码「项目/部门」这类文案。
 */
const options = computed<SelectOption[]>(() =>
  store.dimensions.map((dimension) => ({
    label: `${dimension.code} · ${dimension.name}（${store.labelOf('kind', dimension.kind)}）`,
    value: dimension.id,
  })),
)

const hasDimensions = computed(() => store.dimensions.length > 0)

/** v-model 走 store action，顺带把选择持久化到 localStorage */
const selectedId = computed<string>({
  get: () => store.currentDimensionId,
  set: (value) => store.setCurrentDimension(value),
})

/** 刷新：强制重拉维度列表（刚在另一个标签页建了维度时用） */
async function handleRefresh(): Promise<void> {
  await store.loadDimensions(true)
}
</script>

<template>
  <NSpace align="center" :size="8">
    <NText depth="2" style="font-size: 13px">归集维度</NText>
    <NSelect
      :value="selectedId"
      :options="options"
      :disabled="!hasDimensions"
      :placeholder="hasDimensions ? '选择维度' : '请先建立维度'"
      filterable
      :consistent-menu-width="false"
      style="min-width: 260px"
    />
    <NButton size="small" quaternary :loading="store.loading" @click="handleRefresh">刷新</NButton>
    <NText v-if="!hasDimensions" depth="3" style="font-size: 12px">
      请先建立维度
    </NText>
  </NSpace>
</template>