<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { NCard, NTag } from 'naive-ui'
import type { AdminOverviewData } from '@/api/admin'

const props = defineProps<{ overview: AdminOverviewData | null }>()

const stats = computed(() => [
  {
    label: '用户总数',
    value: props.overview?.counts.users ?? 0,
    icon: 'ri-user-3-line',
    tone: 'blue',
  },
  {
    label: '公司总数',
    value: props.overview?.counts.companies ?? 0,
    icon: 'ri-building-2-line',
    tone: 'green',
  },
  {
    label: '今日 Agent 动作',
    value: props.overview?.agent.actionsToday ?? 0,
    icon: 'ri-robot-2-line',
    tone: 'purple',
  },
  {
    label: '今日活跃会话',
    value: props.overview?.agent.conversationsToday ?? 0,
    icon: 'ri-chat-smile-2-line',
    tone: 'orange',
  },
])

const dbLabel = computed(() =>
  props.overview?.database.engine === 'sqlite' ? 'SQLite（测试模式）' : 'PostgreSQL',
)

function formatNumber(value: number | null | undefined): string {
  return typeof value === 'number' ? value.toLocaleString('zh-CN') : '—'
}
</script>

<template>
  <div class="admin-overview">
    <div class="stat-grid">
      <NCard v-for="stat in stats" :key="stat.label" class="stat-card" :bordered="false">
        <div class="stat-icon" :class="`tone-${stat.tone}`">
          <i :class="stat.icon" />
        </div>
        <div class="stat-meta">
          <span class="stat-value">{{ formatNumber(stat.value) }}</span>
          <span class="stat-label">{{ stat.label }}</span>
        </div>
      </NCard>
    </div>

    <div class="panel-grid">
      <NCard title="系统状态" :bordered="false">
        <template #header-extra>
          <i class="ri-heart-pulse-line status-title-icon" />
        </template>
        <ul class="status-list">
          <li>
            <span class="status-label"><i class="ri-database-2-line" /> 数据库</span>
            <NTag :type="overview?.database.ok ? 'success' : 'error'" size="small" round>
              {{ overview?.database.ok ? '正常' : '异常' }}
            </NTag>
            <span class="status-extra">{{ dbLabel }}</span>
          </li>
          <li>
            <span class="status-label"><i class="ri-cpu-line" /> LLM 熔断器</span>
            <NTag :type="overview?.llm.circuitOpen ? 'warning' : 'success'" size="small" round>
              {{ overview?.llm.circuitOpen ? '已熔断' : '正常' }}
            </NTag>
            <span class="status-extra">
              {{ overview?.llm.circuitOpen ? '配额/鉴权异常后已停用 LLM 调用' : 'LLM 调用正常放行' }}
            </span>
          </li>
          <li>
            <span class="status-label"><i class="ri-apps-2-line" /> 服务版本</span>
            <NTag size="small" round>v{{ overview?.version ?? '—' }}</NTag>
          </li>
        </ul>
      </NCard>

      <NCard title="Token 用量" :bordered="false">
        <template #header-extra>
          <NTag size="small" round :type="overview?.usage.trackingEnabled ? 'success' : 'default'">
            {{ overview?.usage.trackingEnabled ? '采集中' : 'M1 上线后开始积累' }}
          </NTag>
        </template>
        <ul class="status-list">
          <li>
            <span class="status-label"><i class="ri-sun-line" /> 今日 tokens</span>
            <span class="status-extra">{{ formatNumber(overview?.usage.tokensToday) }}</span>
          </li>
          <li>
            <span class="status-label"><i class="ri-calendar-line" /> 本月 tokens</span>
            <span class="status-extra">{{ formatNumber(overview?.usage.tokensThisMonth) }}</span>
          </li>
        </ul>
        <p class="usage-note">
          <i class="ri-information-line" />
          Token 用量按 UTC 日界统计，数据自 M1 埋点上线起积累。
        </p>
      </NCard>
    </div>

    <NCard title="异常调用" :bordered="false" class="error-card">
      <template #header-extra>
        <NTag size="small" round :type="(overview?.llm.errorCount7d ?? 0) > 0 ? 'warning' : 'success'">
          近 7 天 {{ overview?.llm.errorCount7d ?? 0 }} 次异常
        </NTag>
      </template>
      <div v-if="(overview?.llm.recentErrors?.length ?? 0) === 0" class="error-empty">
        <i class="ri-shield-check-line" />
        <span>近 7 天无异常调用</span>
      </div>
      <template v-else>
        <div v-if="overview?.llm.topErrors?.length" class="error-top">
          <NTag
            v-for="e in overview.llm.topErrors"
            :key="e.type"
            size="small"
            round
            :bordered="false"
            type="warning"
          >
            {{ e.type }} × {{ e.count }}
          </NTag>
        </div>
        <ul class="error-list">
          <li v-for="(e, idx) in overview?.llm.recentErrors" :key="idx">
            <span class="error-time">{{ (e.createdAt || '').replace('T', ' ').slice(0, 19) }}</span>
            <span class="error-scene">{{ e.scene }}</span>
            <span class="error-model">{{ e.model }}</span>
            <span class="error-type">{{ e.errorType }}</span>
            <span class="error-user">{{ e.userId || '—' }}</span>
          </li>
        </ul>
      </template>
    </NCard>
  </div>
</template>

<style scoped>
.stat-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}

.stat-card {
  display: flex;
  align-items: center;
}

.stat-card :deep(.n-card__content) {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 18px 20px;
}

.stat-icon {
  width: 44px;
  height: 44px;
  border-radius: var(--solo-radius);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  flex-shrink: 0;
}

.tone-blue { background: var(--solo-info-bg); color: var(--solo-info); }
.tone-green { background: var(--solo-income-bg); color: var(--solo-income); }
.tone-purple { background: #f1ecfd; color: #6b4fd8; }
.tone-orange { background: var(--solo-warning-bg); color: var(--solo-warning); }

.stat-meta {
  display: flex;
  flex-direction: column;
  min-width: 0;
}

.stat-value {
  font-size: 24px;
  font-weight: 700;
  color: var(--solo-text-primary);
  line-height: 1.2;
}

.stat-label {
  font-size: 13px;
  color: var(--solo-text-secondary);
}

.panel-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
  margin-top: 16px;
}

.status-title-icon {
  font-size: 18px;
  color: var(--solo-text-muted);
}

.status-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.status-list li {
  display: flex;
  align-items: center;
  gap: 10px;
}

.status-label {
  display: flex;
  align-items: center;
  gap: 6px;
  width: 130px;
  flex-shrink: 0;
  color: var(--solo-text-primary);
  font-size: 14px;
}

.status-label i {
  color: var(--solo-text-muted);
}

.status-extra {
  font-size: 13px;
  color: var(--solo-text-muted);
}

.usage-note {
  margin: 16px 0 0;
  padding: 10px 12px;
  background: var(--solo-bg-soft);
  border-radius: var(--solo-radius-sm);
  font-size: 12.5px;
  color: var(--solo-text-secondary);
  display: flex;
  align-items: center;
  gap: 6px;
}

.error-card {
  margin-top: 16px;
}

.error-empty {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 20px 0;
  color: var(--solo-income);
  font-size: 13.5px;
}

.error-empty i {
  font-size: 18px;
}

.error-top {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.error-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  font-size: 12.5px;
}

.error-list li {
  display: grid;
  grid-template-columns: 165px 110px 140px 1fr 1fr;
  gap: 8px;
  padding: 6px 10px;
  background: var(--solo-bg-soft);
  border-radius: var(--solo-radius-sm);
}

.error-time { color: var(--solo-text-muted); }
.error-scene { color: var(--solo-text-secondary); }
.error-model { color: var(--solo-text-secondary); font-family: monospace; }
.error-type { color: var(--solo-expense); font-weight: 600; }
.error-user { color: var(--solo-text-muted); }

@media (max-width: 1100px) {
  .stat-grid { grid-template-columns: repeat(2, 1fr); }
  .panel-grid { grid-template-columns: 1fr; }
}
</style>
