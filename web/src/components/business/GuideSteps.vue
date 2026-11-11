<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks license v2.0. -->
<script setup lang="ts">
import { NImage } from 'naive-ui'
import type { GuideStep } from '@/content/guides'

/**
 * 指引步骤渲染：编号 + 标题 + 说明 + 截图 + 坑位提示。
 *
 * 抽屉（GuideSheet）和独立页（/guides/:id）共用这一份，避免两处排版走样。
 * 截图用 NImage 带预览 —— 税局/网银界面字小，不能放大等于没给。
 */
withDefaults(
  defineProps<{
    steps: GuideStep[]
    /** 紧凑档：抽屉里用，缩一点间距 */
    dense?: boolean
  }>(),
  { dense: false },
)
</script>

<template>
  <ol class="guide-steps" :class="{ 'is-dense': dense }">
    <li v-for="(step, index) in steps" :key="`${index}-${step.title}`" class="guide-step">
      <div class="step-index" aria-hidden="true">{{ index + 1 }}</div>
      <div class="step-body">
        <h4 class="step-title">{{ step.title }}</h4>
        <p v-if="step.text" class="step-text">{{ step.text }}</p>

        <figure v-for="image in step.images || []" :key="image.src" class="step-figure">
          <NImage
            :src="image.src"
            :alt="image.alt"
            width="100%"
            object-fit="contain"
            lazy
            :img-props="{
              width: image.width,
              height: image.height,
              style: 'display:block; width:100%; height:auto; border-radius:8px;',
            }"
          />
          <figcaption>{{ image.alt }}</figcaption>
        </figure>

        <p v-if="step.tip" class="step-tip">
          <i class="ri-lightbulb-flash-line" aria-hidden="true" />
          <span>{{ step.tip }}</span>
        </p>
      </div>
    </li>
  </ol>
</template>

<style scoped>
.guide-steps {
  display: flex;
  flex-direction: column;
  gap: 20px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.guide-steps.is-dense {
  gap: 16px;
}

.guide-step {
  display: flex;
  gap: 12px;
  align-items: flex-start;
}

.step-index {
  flex: 0 0 22px;
  width: 22px;
  height: 22px;
  margin-top: 1px;
  border-radius: 999px;
  background: var(--solo-bg-blue-soft, #edf3ff);
  color: var(--solo-primary, #1747b0);
  font-size: 12px;
  font-weight: 650;
  line-height: 22px;
  text-align: center;
}

.step-body {
  min-width: 0;
  flex: 1;
}

.step-title {
  margin: 0;
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
  line-height: 1.5;
}

.step-text {
  margin: 6px 0 0;
  font-size: 13px;
  line-height: 1.7;
  color: var(--solo-text-secondary, #586474);
}

.step-figure {
  margin: 10px 0 0;
}

.step-figure :deep(img) {
  display: block;
  border: 1px solid var(--solo-border-light, #edf0f4);
  background: var(--solo-bg-card, #fff);
}

.step-figure figcaption {
  margin-top: 6px;
  font-size: 11.5px;
  line-height: 1.5;
  color: var(--solo-text-muted, #636e7c);
}

.step-tip {
  display: flex;
  gap: 6px;
  align-items: flex-start;
  margin: 10px 0 0;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--solo-bg-soft, #f1f4f8);
  font-size: 12.5px;
  line-height: 1.6;
  color: var(--solo-text-secondary, #586474);
}

.step-tip i {
  flex: 0 0 auto;
  margin-top: 1px;
  color: var(--solo-warning, #a05c10);
  font-size: 14px;
}
</style>
