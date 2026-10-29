<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { NDataTable } from 'naive-ui'
import type { DataTableColumns, PaginationProps } from 'naive-ui'
import SoloEmpty from './SoloEmpty.vue'
import type { EmptyVariant } from './SoloEmpty.vue'

// ponytail: 轻量包裹 NDataTable，抹平全站列表的样式分歧与样板代码（空状态/加载态/分页/表头防折行）。
// 上限与升级路径：当前依赖 Naive UI 内置的数据表格能力，若后续有更极端的财务跨行跨列排版（如资产负债表多级合计），
// 报表页应继续保持语义化原生 <table>，不要硬塞进通用 DataTable 中。

const props = withDefaults(
  defineProps<{
    columns: DataTableColumns<any>
    data: any[]
    loading?: boolean
    bordered?: boolean
    singleLine?: boolean
    size?: 'small' | 'medium' | 'large'
    rowKey?: (row: any) => string | number
    rowProps?: (row: any, index: number) => Record<string, any>
    scrollX?: number | string
    maxHeight?: number | string
    pagination?: PaginationProps | false
    clickable?: boolean
    emptyTitle?: string
    emptyDescription?: string
    emptyVariant?: EmptyVariant
    emptyIcon?: string
  }>(),
  {
    loading: false,
    bordered: false,
    singleLine: true,
    size: 'medium',
    rowKey: (row: any) => row.id ?? row.key,
    scrollX: undefined,
    maxHeight: undefined,
    pagination: false,
    clickable: false,
    emptyTitle: '暂无数据',
    emptyDescription: '',
    emptyVariant: 'hint',
    emptyIcon: undefined,
  },
)

const emit = defineEmits<{
  'row-click': [row: any, event: MouseEvent]
}>()

const computedRowProps = computed(() => {
  return (row: any, index: number) => {
    const userProps = props.rowProps ? props.rowProps(row, index) : {}
    if (!props.clickable && !userProps.onClick) {
      return userProps
    }
    return {
      ...userProps,
      class: [userProps.class, 'is-clickable'].filter(Boolean).join(' '),
      onClick: (e: MouseEvent) => {
        userProps.onClick?.(e)
        emit('row-click', row, e)
      },
    }
  }
})
</script>

<template>
  <div class="solo-table-container">
    <NDataTable
      v-bind="$attrs"
      :columns="columns"
      :data="data"
      :loading="loading"
      :bordered="bordered"
      :single-line="singleLine"
      :size="size"
      :row-key="rowKey"
      :row-props="computedRowProps"
      :scroll-x="scrollX"
      :max-height="maxHeight"
      :pagination="pagination"
      class="solo-table"
    >
      <template #empty>
        <slot name="empty">
          <div class="solo-table-empty">
            <SoloEmpty
              :variant="emptyVariant"
              :title="emptyTitle"
              :description="emptyDescription"
              :icon="emptyIcon"
            >
              <template v-if="$slots['empty-action']" #action>
                <slot name="empty-action" />
              </template>
            </SoloEmpty>
          </div>
        </slot>
      </template>

      <template v-if="$slots.loading" #loading>
        <slot name="loading" />
      </template>
    </NDataTable>
  </div>
</template>

<style scoped>
.solo-table-container {
  width: 100%;
  border-radius: var(--solo-radius, 8px);
  overflow: hidden;
}

.solo-table :deep(.n-data-table-th) {
  background-color: var(--solo-bg-soft);
  color: var(--solo-text-secondary);
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
  letter-spacing: 0.3px;
  border-bottom: 1px solid var(--solo-border-light);
  padding: 10px 14px;
}

.solo-table :deep(.n-data-table-td) {
  font-size: 13.5px;
  color: var(--solo-text-primary);
  border-bottom: 1px solid var(--solo-border-light);
  padding: 10px 14px;
}

.solo-table :deep(.n-data-table-tr:hover td) {
  background-color: var(--solo-bg-blue-soft);
}

.solo-table :deep(.is-clickable) {
  cursor: pointer;
}

.solo-table-empty {
  padding: 32px 16px;
}

.solo-table :deep(.amount-cell) {
  font-family: 'Outfit', 'Inter', monospace;
  font-variant-numeric: tabular-nums;
  text-align: right;
}

.solo-table :deep(.mono) {
  font-family: 'Inter', monospace;
  font-variant-numeric: tabular-nums;
}
</style>
