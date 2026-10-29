<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { usePageRefresh } from '@/composables/usePageRefresh'
// 通用归集工作台（设计文档 T02）。
//
// hasDimensions === false 时**只渲染 EmptyState**，不渲染概览与统计——
// 空态下展示「维度数 0」的表格是噪音，用户需要的只是下一步该做什么。
import { computed, h, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import {
  NAlert,
  NButton,
  NCard,
  NDataTable,
  NStatistic,
  NTag,
  useMessage,
} from 'naive-ui'
import type { DataTableColumns } from 'naive-ui'
import DimensionPicker from './_components/DimensionPicker.vue'
import EmptyState from './_components/EmptyState.vue'
import { listAggregationRules, listMembers } from '@/api/aggregation'
import { useAggregationStore } from '@/stores/aggregation'
import { errorText } from '@/utils/aggregation'
import type { AggregationDimension, AggregationMember, AggregationRule } from '@/api/types'

const router = useRouter()
const message = useMessage()
const store = useAggregationStore()

const statsLoading = ref(false)

/** 每个维度的成员与规则条数。key = dimensionId */
const memberCountMap = ref<Record<string, number>>({})
const ruleCountMap = ref<Record<string, number>>({})

/** 维度概览表数据（成员数/规则数从上面的 map 取，不额外请求） */
const dimensionRows = computed<AggregationDimension[]>(() => store.dimensions)

const columns = computed<DataTableColumns<AggregationDimension>>(() => [
  { title: '编码', key: 'code', width: 140 },
  { title: '名称', key: 'name', ellipsis: { tooltip: true } },
  {
    title: '类型',
    key: 'kind',
    width: 110,
    // kind 的中文标签来自 GET /meta，不在前端硬编码
    render: (row) => store.labelOf('kind', row.kind),
  },
  {
    title: '归集对象',
    key: 'memberCount',
    width: 100,
    render: (row) => String(memberCountMap.value[row.id] ?? 0),
  },
  {
    title: '规则数',
    key: 'ruleCount',
    width: 90,
    render: (row) => String(ruleCountMap.value[row.id] ?? 0),
  },
  {
    title: '状态',
    key: 'status',
    width: 96,
    render: (row) =>
      h(
        NTag,
        { size: 'small', bordered: false, type: row.status === 'active' ? 'success' : 'info' },
        { default: () => (row.status === 'active' ? '启用' : '已归档') },
      ),
  },
])

/** 4 张快捷统计卡 */
const activeDimensionCount = computed(() => store.dimensions.length)
const archivedDimensionCount = computed(
  () => store.dimensions.filter((d) => d.status === 'archived').length,
)
const activeMemberCount = computed(() =>
  Object.entries(memberCountMap.value).reduce((sum, [, count]) => sum + count, 0),
)
const totalRuleCount = computed(() =>
  Object.values(ruleCountMap.value).reduce((sum, count) => sum + count, 0),
)

/** 子页入口卡。指向尚未实现的页面时，由占位页承接（T03–T05 落地） */
const entryCards = [
  { path: '/aggregation/pending', title: '待归集清单', desc: '按期间逐笔归集，支持批量与执行规则' },
  { path: '/aggregation/rules', title: '归集规则', desc: '按科目前缀、方向、关键词自动匹配' },
  { path: '/aggregation/dimensions', title: '维度与对象', desc: '维护维度与归集对象、起止日期' },
  { path: '/aggregation/report', title: '归集报表', desc: '按归集对象汇总已过账凭证行' },
]

/**
 * 拉取每个维度的成员数与规则数。
 * ⚠️ 必须 Promise.all 并发：维度多时串行 await 会把首屏拖成 N 个 RTT。
 * 单个维度失败不拖垮整页——降级为「未知」，统计卡按已知值求和。
 */
async function loadStats(): Promise<void> {
  const targets = store.dimensions.filter((d) => d.status === 'active')
  if (!targets.length) {
    memberCountMap.value = {}
    ruleCountMap.value = {}
    return
  }
  statsLoading.value = true
  try {
    const settled = await Promise.allSettled(
      targets.map(async (dimension) => {
        const [members, rules]: [AggregationMember[], AggregationRule[]] = await Promise.all([
          listMembers(dimension.id, false),
          listAggregationRules(dimension.id),
        ])
        return { id: dimension.id, members: members.length, rules: rules.length }
      }),
    )
    const nextMembers: Record<string, number> = {}
    const nextRules: Record<string, number> = {}
    settled.forEach((result, index) => {
      if (result.status === 'fulfilled') {
        nextMembers[result.value.id] = result.value.members
        nextRules[result.value.id] = result.value.rules
      } else {
        const id = targets[index].id
        nextMembers[id] = 0
        nextRules[id] = 0
      }
    })
    memberCountMap.value = nextMembers
    ruleCountMap.value = nextRules
    const failed = settled.filter((r) => r.status === 'rejected').length
    if (failed) message.warning(`${failed} 个维度的成员/规则读取失败，统计可能偏低`)
  } catch (err) {
    message.error(errorText(err))
  } finally {
    statsLoading.value = false
  }
}

onMounted(async () => {
  try {
    await store.bootstrap()
    await loadStats()
  } catch (err) {
    message.error(errorText(err))
  }
})

// 从别的标签切回来时静默重拉，避免对着旧数字干活
usePageRefresh(() => Promise.all([store.bootstrap(), loadStats()]))
</script>

<template>
  <div class="agg-page">
    <div class="page-header">
      <h2>通用归集</h2>
      <NButton size="small" type="primary" @click="router.push('/aggregation/dimensions')">
        维度与对象
      </NButton>
    </div>

    <EmptyState v-if="!store.hasDimensions" />

    <template v-else>
      <div class="picker-row">
        <DimensionPicker />
      </div>

      <NAlert type="info" :bordered="false" class="scope-note">
        待归集清单包含草稿、待审核与已过账凭证；归集报表只统计已过账凭证，两者金额不同是刻意的。
      </NAlert>

      <div class="stat-bar">
        <NCard :bordered="false" class="stat-card">
          <NStatistic :value="activeDimensionCount" label="启用维度" />
        </NCard>
        <NCard :bordered="false" class="stat-card">
          <NStatistic :value="activeMemberCount" label="启用对象数" />
        </NCard>
        <NCard :bordered="false" class="stat-card">
          <NStatistic :value="archivedDimensionCount" label="已归档维度" />
        </NCard>
        <NCard :bordered="false" class="stat-card">
          <NStatistic :value="totalRuleCount" label="规则总数" />
        </NCard>
      </div>

      <NCard title="维度概览" :bordered="false" class="block-card">
        <NDataTable
          :columns="columns"
          :data="dimensionRows"
          :row-key="(row: AggregationDimension) => row.id"
          :bordered="false"
          striped
          size="small"
          :loading="statsLoading"
        />
      </NCard>

      <div class="entry-grid">
        <NCard
          v-for="card in entryCards"
          :key="card.path"
          :bordered="false"
          class="entry-card"
          hoverable
          @click="router.push(card.path)"
        >
          <h4>{{ card.title }}</h4>
          <p>{{ card.desc }}</p>
        </NCard>
      </div>
    </template>
  </div>
</template>

<style scoped>
.agg-page {
  max-width: 1080px;
  margin: 0 auto;
}
.page-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.picker-row {
  margin-bottom: 12px;
}
.scope-note {
  margin-bottom: 16px;
}
.stat-bar {
  display: flex;
  gap: 16px;
  margin-bottom: 16px;
}
.stat-card {
  flex: 1;
  border-radius: var(--solo-radius-lg);
}
.block-card {
  border-radius: var(--solo-radius-lg);
  margin-bottom: 16px;
}
.entry-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 16px;
}
.entry-card {
  cursor: pointer;
  border-radius: var(--solo-radius-lg);
}
.entry-card h4 {
  margin: 0 0 6px;
  font-size: 15px;
  font-weight: 600;
}
.entry-card p {
  margin: 0;
  font-size: 12px;
  color: var(--solo-text-secondary);
}
@media (max-width: 820px) {
  .stat-bar {
    overflow-x: auto;
    padding-bottom: 4px;
  }
  .stat-card {
    min-width: 150px;
  }
}
</style>