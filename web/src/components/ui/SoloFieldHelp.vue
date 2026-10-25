<!-- Copyright (c) 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks license v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { NTooltip } from 'naive-ui'
import { lookupTerm } from '@/utils/glossary'

/**
 * label 旁的 ？帮助：把专业词的一句话解释放在用户手边。
 *
 * 收口全站既有的三种 ？ 实现（NPopover click / NTooltip hover / render 函数）：
 * 统一为 NTooltip(hover) + ri-information-line——tax-filing 已有 12 处 hover 惯例，
 * 不引入第四种风格。解释文案优先查术语表（`term`），没有词条的用 `text` 直传。
 */
const props = defineProps<{
  /** 术语表词条名；找不到词条时回落到 `text`，再回落到 term 本身 */
  term?: string
  /** 没有对应词条的一次性解释 */
  text?: string
}>()

const tip = computed(() => {
  // text 是显式解释，优先；term 只查术语表——查不到就空着不渲染，
  // 绝不把词条名本身当解释显示出去（用户会看到「不存在的词」四个字）
  if (props.text) return props.text
  if (props.term) return lookupTerm(props.term)?.plain ?? ''
  return ''
})
</script>

<template>
  <NTooltip v-if="tip" trigger="hover" :style="{ maxWidth: '320px' }">
    <template #trigger>
      <i class="ri-information-line field-help-icon" aria-hidden="true" />
    </template>
    <span class="field-help-tip">{{ tip }}</span>
  </NTooltip>
</template>

<style scoped>
.field-help-icon {
  color: var(--solo-text-muted);
  font-size: 14px;
  cursor: help;
  vertical-align: -2px;
}

.field-help-icon:hover {
  color: var(--solo-text-secondary);
}

.field-help-tip {
  font-size: 12px;
  line-height: 1.6;
}
</style>
