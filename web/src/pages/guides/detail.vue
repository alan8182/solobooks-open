<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks license v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { SoloEmpty } from '@/components/ui'
import GuideSteps from '@/components/business/GuideSteps.vue'
import { getGuide } from '@/content/guides'

/**
 * 图文指引独立页 `/guides/:id`。
 *
 * 只给内容长的指引用（目前是个税代扣代缴）：用户是「专门来看」的，看完就去外部
 * 系统操作，不需要抽屉那种「背后还留着操作页」的形态。
 */
const route = useRoute()
const router = useRouter()

const guide = computed(() => getGuide(String(route.params.id ?? '')))

function goBack() {
  if (window.history.length > 1) router.back()
  else router.push('/guide')
}
</script>

<template>
  <div class="guide-page">
    <button type="button" class="guide-back" @click="goBack">
      <i class="ri-arrow-left-line" aria-hidden="true" />
      返回
    </button>

    <template v-if="guide">
      <header class="guide-header">
        <h1 class="guide-title">{{ guide.title }}</h1>
        <p class="guide-summary">{{ guide.summary }}</p>
        <div class="guide-meta">
          <span>截图更新于 {{ guide.updatedAt }}</span>
          <a :href="guide.sourceUrl" target="_blank" rel="noopener">
            查看最新版（腾讯文档）
            <i class="ri-external-link-line" aria-hidden="true" />
          </a>
        </div>
      </header>

      <div v-if="guide.notice" class="guide-notice">
        <i class="ri-information-line" aria-hidden="true" />
        <span>{{ guide.notice }}</span>
      </div>

      <section class="guide-body">
        <GuideSteps :steps="guide.steps" />
      </section>

      <footer class="guide-footer">
        截图来自税局/银行实际界面，版本更新后可能与你的页面略有差异；以腾讯文档最新版为准。
      </footer>
    </template>

    <SoloEmpty
      v-else
      title="没有这份指引"
      description="链接可能已经失效，回上手引导页看看还有哪些操作说明。"
      icon="ri-question-line"
    >
      <template #action>
        <button type="button" class="guide-back is-inline" @click="router.push('/guide')">
          去上手引导
        </button>
      </template>
    </SoloEmpty>
  </div>
</template>

<style scoped>
.guide-page {
  max-width: 760px;
  margin: 0 auto;
  padding: 24px 20px 56px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.guide-back {
  align-self: flex-start;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0;
  border: 0;
  background: none;
  font-size: 13px;
  color: var(--solo-text-secondary, #586474);
  cursor: pointer;
}

.guide-back:hover {
  color: var(--solo-primary, #1747b0);
}

.guide-back.is-inline {
  padding: 7px 14px;
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: var(--solo-radius-control, 8px);
  background: var(--solo-bg-card, #fff);
  font-size: 13px;
}

.guide-header {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.guide-title {
  margin: 0;
  font-size: 24px;
  font-weight: 700;
  letter-spacing: -0.4px;
  color: var(--solo-text-primary, #182230);
}

.guide-summary {
  margin: 0;
  font-size: 14px;
  line-height: 1.7;
  color: var(--solo-text-secondary, #586474);
}

.guide-meta {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
  font-size: 12px;
  color: var(--solo-text-muted, #636e7c);
}

.guide-meta a {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  color: var(--solo-text-muted, #636e7c);
  text-decoration: none;
}

.guide-meta a:hover {
  color: var(--solo-primary, #1747b0);
}

.guide-notice {
  display: flex;
  gap: 8px;
  align-items: flex-start;
  padding: 12px 14px;
  border-radius: var(--solo-radius-control, 8px);
  background: var(--solo-bg-blue-soft, #edf3ff);
  font-size: 13px;
  line-height: 1.7;
  color: var(--solo-text-secondary, #586474);
}

.guide-notice i {
  flex: 0 0 auto;
  margin-top: 2px;
  color: var(--solo-primary, #1747b0);
  font-size: 15px;
}

.guide-body {
  padding: 20px 22px;
  border: 1px solid var(--solo-border, #dde3ea);
  border-radius: var(--solo-radius-lg, 12px);
  background: var(--solo-bg-card, #fff);
}

.guide-footer {
  font-size: 12px;
  line-height: 1.7;
  color: var(--solo-text-muted, #636e7c);
}
</style>
