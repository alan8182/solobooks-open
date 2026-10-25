<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { useRouter } from 'vue-router'
import { NButton } from 'naive-ui'
import { SoloEmpty } from '@/components/ui'

const router = useRouter()
</script>

<template>
  <div class="not-found-page">
    <SoloEmpty
      title="这个页面不存在"
      description="链接可能已经过期，或者地址输入有误。"
      icon="ri-question-line"
    >
      <template #action>
        <div class="not-found-actions">
          <NButton type="primary" @click="router.push('/')">返回工作台</NButton>
          <NButton quaternary @click="router.back()">回到上一页</NButton>
        </div>
      </template>
    </SoloEmpty>
  </div>
</template>

<style scoped>
.not-found-page {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 60vh;
}

.not-found-actions {
  display: flex;
  gap: 8px;
  align-items: center;
}
</style>
