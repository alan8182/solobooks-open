<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'

const route = useRoute()
const router = useRouter()

onMounted(() => {
  if (route.params.id) {
    router.replace({ path: '/private-expenses', query: { edit: String(route.params.id) } })
  } else {
    router.replace({ path: '/private-expenses', query: { create: '1' } })
  }
})
</script>

<template>
  <div class="pe-redirect-page" />
</template>
