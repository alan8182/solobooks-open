<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks license v2.0. -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { RouterLink } from 'vue-router'
import { isPageGuide, type GuideId } from '@/content/guides'
import GuideSheet from './GuideSheet.vue'

/**
 * 流程里的「怎么做？」入口。
 *
 * 形态由指引内容量决定：短指引就地开抽屉（不离开当前页），长指引跳独立页。
 * 调用方只给 guide id，不用关心用哪种 —— 免得同一个指引在两个页面出现两种打开方式。
 */
const props = withDefaults(
  defineProps<{
    guide: GuideId
    label?: string
    /** 次要说明，跟在链接后面，例如「含 3 张截图」 */
    hint?: string
  }>(),
  { label: '怎么做？', hint: '' },
)

const open = ref(false)
const asPage = computed(() => isPageGuide(props.guide))
</script>

<template>
  <RouterLink
    v-if="asPage"
    class="guide-trigger"
    :to="`/guides/${guide}`"
  >
    <i class="ri-question-line" aria-hidden="true" />
    <span>{{ label }}</span>
    <span v-if="hint" class="guide-trigger-hint">{{ hint }}</span>
  </RouterLink>

  <template v-else>
    <button
      type="button"
      class="guide-trigger"
      aria-haspopup="dialog"
      @click="open = true"
    >
      <i class="ri-question-line" aria-hidden="true" />
      <span>{{ label }}</span>
      <span v-if="hint" class="guide-trigger-hint">{{ hint }}</span>
    </button>

    <GuideSheet v-model:show="open" :guide-id="guide" />
  </template>
</template>

<style scoped>
.guide-trigger {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0;
  border: 0;
  background: none;
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--solo-primary, #1747b0);
  text-decoration: none;
  cursor: pointer;
}

.guide-trigger:hover {
  color: var(--solo-primary-dark, #0d3282);
  text-decoration: underline;
  text-underline-offset: 2px;
}

.guide-trigger i {
  font-size: 14px;
}

.guide-trigger-hint {
  color: var(--solo-text-muted, #636e7c);
  font-size: 12px;
  text-decoration: none;
}
</style>
