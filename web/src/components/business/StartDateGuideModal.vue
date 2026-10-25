<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { NModal, NSpace, NButton, NResult } from 'naive-ui'
import { startDateGuideVisible, startDateGuideInfo } from '@/composables/startDateGuide'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const authStore = useAuthStore()

const companyId = computed(() => authStore.currentCompanyId || '')

function close() {
  startDateGuideVisible.value = false
}

function goOpening() {
  close()
  router.push('/mine/opening')
}

function goCompanies() {
  close()
  router.push(companyId.value ? `/mine/companies?edit=${companyId.value}` : '/mine/companies')
}
</script>

<template>
  <NModal
    v-model:show="startDateGuideVisible"
    title="无法入账"
    preset="card"
    style="width: 460px; max-width: 92vw"
  >
    <NResult
      status="warning"
      :title="`记账日期 ${startDateGuideInfo?.occurDate || ''} 早于账套启用日期 ${startDateGuideInfo?.startDate || ''}`"
      description="启用日期之前的业务不逐笔记账，应折算为期初余额带入；启用日期填晚了也会导致这个提示。"
    />
    <NSpace vertical :size="8" class="guide-actions">
      <NButton type="primary" block @click="goOpening">这笔是启用前的历史业务，去「期初设置」录入期初余额</NButton>
      <NButton v-if="companyId" block @click="goCompanies">启用日期填晚了，去「主体管理」修改</NButton>
      <NButton quaternary block @click="close">返回修改记账日期</NButton>
    </NSpace>
  </NModal>
</template>

<style scoped>
.guide-actions {
  margin-top: 8px;
}
</style>
