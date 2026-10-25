<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { ref } from 'vue'
import { NButton, NModal } from 'naive-ui'

const show = ref(false)
</script>

<template>
  <button
    type="button"
    class="beta-contact-fab no-print"
    aria-label="查看内测群联系方式"
    @click="show = true"
  >
    <span class="beta-dot" />
    <span class="beta-copy">
      <strong>内测中</strong>
      <small>加群反馈</small>
    </span>
  </button>

  <NModal
    v-model:show="show"
    preset="card"
    title="梭罗记账 · 内测群"
    class="beta-contact-modal"
    :style="{ width: '420px', maxWidth: '92vw', borderRadius: '20px' }"
  >
    <div class="beta-contact-body">
      <p class="beta-lead">
        当前是内测版本，遇到问题不用自己扛。
      </p>
      <p class="beta-desc">
        扫码加我微信，<strong>备注「记账」</strong>，我会拉你进内测群。
        群里有会计师和工程师，记账、报税、折旧、摊销问题都可以直接问。
      </p>
      <div class="beta-qr-wrap">
        <img
          src="/wechat-beta-qr.png"
          alt="微信内测群二维码"
          class="beta-qr"
        />
      </div>
      <p class="beta-tip">添加时请备注「记账」，方便我快速通过。</p>
    </div>
    <template #footer>
      <div class="beta-footer">
        <NButton type="primary" @click="show = false">我知道了</NButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.beta-contact-fab {
  position: fixed;
  right: 20px;
  bottom: 22px;
  z-index: 1200;
  display: flex;
  align-items: center;
  gap: 9px;
  padding: 9px 13px 9px 10px;
  border: 1px solid rgba(23, 71, 176, 0.18);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.94);
  box-shadow: 0 10px 28px rgba(24, 34, 48, 0.14);
  cursor: pointer;
  color: var(--solo-text-primary);
  backdrop-filter: blur(10px);
  transition: transform 0.18s ease, box-shadow 0.18s ease;
}

.beta-contact-fab:hover {
  transform: translateY(-2px);
  box-shadow: 0 14px 32px rgba(24, 34, 48, 0.18);
}

.beta-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--solo-income);
  box-shadow: 0 0 0 4px rgba(15, 115, 84, 0.12);
  flex: none;
}

.beta-copy {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  line-height: 1.15;
}

.beta-copy strong {
  font-size: 12.5px;
  font-weight: 700;
  letter-spacing: 0.02em;
}

.beta-copy small {
  margin-top: 2px;
  font-size: 10.5px;
  color: var(--solo-text-muted);
}

.beta-contact-body {
  color: var(--solo-text-secondary);
}

.beta-lead {
  margin: 0 0 8px;
  font-size: 14px;
  font-weight: 600;
  color: var(--solo-text-primary);
}

.beta-desc {
  margin: 0 0 16px;
  font-size: 13px;
  line-height: 1.7;
}

.beta-desc strong {
  color: var(--solo-primary);
}

.beta-qr-wrap {
  display: flex;
  justify-content: center;
  padding: 10px;
  border: 1px solid var(--solo-border-light);
  border-radius: 16px;
  background: var(--solo-bg-card);
}

.beta-qr {
  width: min(250px, 68vw);
  aspect-ratio: 882 / 914;
  object-fit: contain;
  display: block;
}

.beta-tip {
  margin: 12px 0 0;
  text-align: center;
  font-size: 12px;
  color: var(--solo-text-muted);
}

.beta-footer {
  display: flex;
  justify-content: flex-end;
}

@media (max-width: 640px) {
  .beta-contact-fab {
    right: 12px;
    bottom: 14px;
    padding: 8px 11px 8px 9px;
  }

  .beta-copy small {
    display: none;
  }
}

@media (prefers-reduced-motion: reduce) {
  .beta-contact-fab {
    transition: none;
  }
}
</style>
