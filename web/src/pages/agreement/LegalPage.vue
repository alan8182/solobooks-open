<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script lang="ts">
export interface LegalSection {
  heading: string
  paragraphs: string[]
}
</script>

<script setup lang="ts">
defineProps<{
  docTitle: string
  updatedAt: string
  sections: LegalSection[]
}>()
</script>

<template>
  <div class="legal-page">
    <div class="legal-card">
      <h1 class="legal-title">{{ docTitle }}</h1>
      <p class="legal-updated">更新日期：{{ updatedAt }}</p>
      <template v-for="section in sections" :key="section.heading">
        <h2 class="legal-heading">{{ section.heading }}</h2>
        <p v-for="(para, idx) in section.paragraphs" :key="idx" class="legal-para">{{ para }}</p>
      </template>
      <div class="legal-footer">
        <router-link to="/login">返回登录</router-link>
      </div>
    </div>
  </div>
</template>

<style scoped>
.legal-page {
  min-height: 100vh;
  background: var(--solo-bg-page, #f6f7f9);
  padding: 40px 16px;
  display: flex;
  justify-content: center;
}
.legal-card {
  width: 100%;
  max-width: 760px;
  background: var(--solo-bg-card, #fff);
  border-radius: var(--solo-radius-lg, 14px);
  box-shadow: var(--solo-shadow, 0 8px 24px rgba(24, 34, 48, 0.06));
  padding: 40px 44px;
}
.legal-title {
  font-size: 24px;
  font-weight: 700;
  color: var(--solo-text-primary, #182230);
  margin: 0;
}
.legal-updated {
  font-size: 13px;
  color: var(--solo-text-muted, #8a95a3);
  margin: 8px 0 4px;
}
.legal-heading {
  font-size: 16px;
  font-weight: 600;
  color: var(--solo-text-primary, #182230);
  margin: 28px 0 8px;
}
.legal-para {
  font-size: 14px;
  line-height: 1.9;
  color: var(--solo-text-secondary, #586474);
  margin: 0 0 8px;
  white-space: pre-wrap;
}
.legal-footer {
  margin-top: 36px;
  padding-top: 16px;
  border-top: 1px solid var(--solo-border-light, #edf0f4);
  font-size: 13px;
}
.legal-footer a {
  color: var(--solo-primary, #165dff);
  text-decoration: none;
}
@media (max-width: 640px) {
  .legal-card {
    padding: 28px 20px;
  }
}
</style>
