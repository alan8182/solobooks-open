<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<!-- 会计视图标准表头：居中报表名 + 编制单位 / 报表日期 / 单位 三段元信息 -->
<script setup lang="ts">
import { computed } from 'vue'
import { useAuthStore } from '@/stores/auth'

const props = defineProps<{
  title: string
  /** 报表日期或期间，如「2026年10月31日」「2026年度」 */
  periodText: string
}>()

const authStore = useAuthStore()
const companyName = computed(() =>
  authStore.user?.companies.find(c => c.company_id === authStore.currentCompanyId)?.name || ''
)
</script>

<template>
  <div class="pro-statement-header" data-testid="pro-statement-header">
    <h3 class="pro-title">{{ title }}</h3>
    <div class="pro-meta">
      <span>编制单位：{{ companyName || '—' }}</span>
      <span>{{ periodText }}</span>
      <span>单位：元</span>
    </div>
  </div>
</template>

<style scoped>
.pro-statement-header {
  margin-bottom: 14px;
}

.pro-title {
  text-align: center;
  font-size: 17px;
  font-weight: 750;
  letter-spacing: 2px;
  margin: 0 0 10px;
  color: var(--solo-text-primary, #182230);
}

.pro-meta {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  font-size: 12.5px;
  color: var(--solo-text-secondary, #586474);
}
</style>
