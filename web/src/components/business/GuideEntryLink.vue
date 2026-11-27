<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks license v2.0. -->
<script setup lang="ts">
import { type GuideId } from '@/content/guides'
import { useGuideEntry } from '@/composables/useGuideEntry'

/**
 * 指引对应的官方办理入口（如电子税务局）。
 *
 * 网址写在指引数据里（`Guide.entry`），页面只给 guide id —— 这样「去哪办」和
 * 「怎么办」永远同源，改地址不会漏改某个页面。
 */
const props = defineProps<{ guide: GuideId }>()

// 入口按当前主体注册地解析：填过注册地就直接进本省电子税务局
const { entry } = useGuideEntry(props.guide)
</script>

<template>
  <a
    v-if="entry"
    class="guide-entry-link"
    :href="entry.url"
    target="_blank"
    rel="noopener"
  >
    <span>{{ entry.label }}</span>
    <i class="ri-external-link-line" aria-hidden="true" />
  </a>
</template>

<style scoped>
.guide-entry-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-left: 8px;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--solo-primary, #1747b0);
  text-decoration: none;
  white-space: nowrap;
}

.guide-entry-link:hover {
  color: var(--solo-primary-dark, #0d3282);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.guide-entry-link i {
  font-size: 12px;
}
</style>
