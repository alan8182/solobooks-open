<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useUsageStore } from '@/stores/usage'

const DISMISS_KEY = 'solo:promo-banner-dismissed'

const route = useRoute()
const router = useRouter()
const usageStore = useUsageStore()

function readDismissed(): boolean {
  try {
    return typeof sessionStorage !== 'undefined' && sessionStorage.getItem(DISMISS_KEY) === '1'
  } catch {
    return false
  }
}

const dismissed = ref(readDismissed())

const quota = computed(() => usageStore.summary?.quota ?? null)

const visible = computed(() => {
  if (dismissed.value) return false
  if (route.path === '/mine/plan') return false
  return quota.value?.promoLowQuota === true
})

const remainingText = computed(() => {
  const remaining = quota.value?.remainingTokens ?? 0
  if (remaining <= 0) return '本期免费体验额度已用完'
  const label = remaining >= 10_000
    ? `${(remaining / 10_000).toFixed(1)} 万`
    : remaining.toLocaleString('zh-CN')
  return `本期免费体验额度即将用完（仅剩 ${label} Token）`
})

function dismiss() {
  dismissed.value = true
  try {
    sessionStorage.setItem(DISMISS_KEY, '1')
  } catch { /* ignore */ }
}
</script>

<template>
  <div v-if="visible" class="quota-low-banner" role="status">
    <div class="banner-copy">
      <i class="ri-flashlight-line" aria-hidden="true" />
      <span>
        {{ remainingText }}，新人可享 <strong>¥1 解锁 80 万 Token（30 天有效）</strong>，或直接升级云端标准版。
      </span>
    </div>
    <div class="banner-actions">
      <button
        type="button"
        class="banner-button primary"
        @click="router.push('/mine/plan?promo=first_month')"
      >
        ¥1 解锁 80 万
      </button>
      <button
        type="button"
        class="banner-button"
        @click="router.push('/mine/plan?plan=standard')"
      >
        升级标准版 ¥598/年
      </button>
      <button type="button" class="banner-close" aria-label="关闭提醒" @click="dismiss">
        <i class="ri-close-line" aria-hidden="true" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.quota-low-banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  max-width: var(--page-width);
  margin: 0 auto 16px;
  padding: 10px 14px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius);
  background: var(--solo-warning-bg);
}

.banner-copy {
  display: flex;
  align-items: center;
  gap: 8px;
  min-width: 0;
  color: var(--solo-text-primary);
  font-size: 12.5px;
  line-height: 1.5;
}

.banner-copy i {
  color: var(--solo-warning);
  font-size: 16px;
  flex-shrink: 0;
}

.banner-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.banner-button {
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius);
  background: var(--solo-bg-card);
  color: var(--solo-text-secondary);
  padding: 5px 10px;
  font: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}

.banner-button:hover,
.banner-button:focus-visible {
  border-color: var(--solo-primary);
  color: var(--solo-primary);
}

.banner-button.primary {
  border-color: var(--solo-primary);
  background: var(--solo-primary);
  color: var(--solo-bg-card);
}

.banner-button.primary:hover,
.banner-button.primary:focus-visible {
  background: var(--solo-primary-light);
  border-color: var(--solo-primary-light);
  color: var(--solo-bg-card);
}

.banner-close {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  border: 0;
  border-radius: var(--solo-radius-sm);
  background: transparent;
  color: var(--solo-text-muted);
  cursor: pointer;
}

.banner-close:hover {
  background: var(--solo-bg-card);
  color: var(--solo-text-primary);
}

@media (max-width: 768px) {
  .quota-low-banner {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
  }
  .banner-actions {
    width: 100%;
    flex-wrap: wrap;
  }
}
</style>
