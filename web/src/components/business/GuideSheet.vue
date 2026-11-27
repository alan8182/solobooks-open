<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks license v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { SoloDrawer } from '@/components/ui'
import { getGuide, type GuideId } from '@/content/guides'
import { useGuideEntry } from '@/composables/useGuideEntry'
import GuideSteps from './GuideSteps.vue'

/**
 * 就地指引抽屉：用户正在导入/申报的页面里点「怎么做」，不离开当前上下文。
 *
 * 只承载短指引（微信账单 / 银行流水 / 税费种认定）。个税那种长文走独立页，
 * 在抽屉里滚 9 张截图会把背后的操作页顶丢。
 */
const props = withDefaults(
  defineProps<{
    show: boolean
    guideId: GuideId
  }>(),
  {},
)

const emit = defineEmits<{
  'update:show': [value: boolean]
}>()

const guide = computed(() => getGuide(props.guideId))
const { entry } = useGuideEntry(props.guideId)
</script>

<template>
  <SoloDrawer
    :show="show"
    :width="720"
    @update:show="emit('update:show', $event)"
  >
    <template #header>
      <div v-if="guide" class="guide-sheet-head">
        <h3 class="guide-sheet-title">{{ guide.title }}</h3>
        <span class="guide-sheet-updated">截图更新于 {{ guide.updatedAt }}</span>
      </div>
    </template>

    <template v-if="guide">
      <p class="guide-sheet-summary">{{ guide.summary }}</p>

      <div v-if="guide.notice" class="guide-sheet-notice">
        <i class="ri-information-line" aria-hidden="true" />
        <span>{{ guide.notice }}</span>
      </div>

      <!-- 官方办理入口：能直接跳过去的，就不让用户读完步骤再自己找网址 -->
      <a
        v-if="entry"
        class="guide-sheet-entry"
        :href="entry.url"
        target="_blank"
        rel="noopener"
      >
        <i class="ri-external-link-line" aria-hidden="true" />
        <span class="guide-sheet-entry-label">{{ entry.label }}</span>
        <span v-if="entry.hint" class="guide-sheet-entry-hint">{{ entry.hint }}</span>
      </a>

      <GuideSteps :steps="guide.steps" dense />
    </template>

    <template v-if="guide" #footer>
      <a
        class="guide-sheet-source"
        :href="guide.sourceUrl"
        target="_blank"
        rel="noopener"
      >
        查看最新版（腾讯文档）
        <i class="ri-external-link-line" aria-hidden="true" />
      </a>
    </template>
  </SoloDrawer>
</template>

<style scoped>
.guide-sheet-head {
  display: flex;
  align-items: baseline;
  gap: 10px;
  flex-wrap: wrap;
}

.guide-sheet-title {
  margin: 0;
  font-size: 16px;
  font-weight: 650;
  color: var(--solo-text-primary, #182230);
  letter-spacing: -0.2px;
}

.guide-sheet-updated {
  font-size: 11.5px;
  color: var(--solo-text-muted, #636e7c);
}

.guide-sheet-summary {
  margin: 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--solo-text-secondary, #586474);
}

.guide-sheet-notice {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 10px 12px;
  border-radius: 8px;
  background: var(--solo-bg-blue-soft, #edf3ff);
  font-size: 12.5px;
  line-height: 1.65;
  color: var(--solo-text-secondary, #586474);
}

.guide-sheet-notice i {
  flex: 0 0 auto;
  margin-top: 1px;
  color: var(--solo-primary, #1747b0);
  font-size: 14px;
}

.guide-sheet-entry {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
  margin: 12px 0 4px;
  padding: 7px 12px;
  border-radius: 8px;
  background: var(--solo-bg-blue-soft, #edf3ff);
  color: var(--solo-primary, #1747b0);
  font-size: 13px;
  text-decoration: none;
}

.guide-sheet-entry:hover {
  text-decoration: underline;
  text-underline-offset: 2px;
}

.guide-sheet-entry-label {
  font-weight: 600;
}

.guide-sheet-entry-hint {
  color: var(--solo-text-secondary, #586474);
  font-size: 12px;
  font-weight: 400;
}

.guide-sheet-source {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
  text-decoration: none;
}

.guide-sheet-source:hover {
  color: var(--solo-primary, #1747b0);
}
</style>
