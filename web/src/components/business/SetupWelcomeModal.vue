<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks license v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { NModal } from 'naive-ui'
import { SoloButton } from '@/components/ui'
import { useAuthStore } from '@/stores/auth'
import { useGuideStateStore } from '@/stores/guideState'

/**
 * L0 起跑线：首次登录时的欢迎与分流引导。
 *
 * 采用第一屏双通道分流：
 * - 演示沙盒体验：推荐直接逛逛演示账套（0负担体验），或立即创建属于自己的真实企业账套。
 * - 真实账套：提供从零建账、代账转入等入口。
 */
const props = defineProps<{ show: boolean }>()
const emit = defineEmits<{ 'update:show': [boolean] }>()

const router = useRouter()
const authStore = useAuthStore()
const guideStore = useGuideStateStore()

const currentCompany = computed(
  () =>
    authStore.companies.find((item) => item.company_id === authStore.currentCompanyId) ?? null,
)
const isDemo = computed(() => currentCompany.value?.isDemo === true)

function close() {
  guideStore.markL0Seen()
  const userId = authStore.user?.user_id || authStore.userId || ''
  if (userId) {
    try {
      localStorage.setItem(`solo:l0-seen:${userId}`, '1')
    } catch { /* ignore */ }
  }
  emit('update:show', false)
}

function go(path: string) {
  close()
  router.push(path)
}
</script>

<template>
  <NModal
    :show="props.show"
    preset="card"
    :title="isDemo ? '欢迎来到 SoloBooks' : '上手起跑线'"
    class="setup-welcome"
    :style="{ width: '540px', maxWidth: '92vw' }"
    :mask-closable="true"
    @update:show="emit('update:show', $event)"
  >
    <!-- 演示账套：第一屏直接双通道分流，明确沙盒属性与建账入口 -->
    <div v-if="isDemo" class="onboarding-sandbox-flow">
      <div class="welcome-block demo-sandbox-block">
        <div class="sandbox-tag">
          <i class="ri-flask-line" aria-hidden="true" />
          <span>沙盒体验环境 · 样本数据</span>
        </div>
        <p class="welcome-company">{{ currentCompany?.name || '【演示】杭州南山科技有限公司' }}</p>
        <p class="welcome-desc">
          不同于传统代账“只抄发票不对流水”的黑盒账，SoloBooks 陪你在日常确认中建立老板财务直觉，从第一天起养出票流一致、公私分明、随时经得起授信与融资尽调的体面账。系统已备好 3 个月完整样本数据：
        </p>
      </div>

      <div class="start-options">
        <button type="button" class="start-option start-option-featured" @click="close">
          <i class="ri-compass-3-line" aria-hidden="true" />
          <span class="start-copy">
            <strong>带我逛逛演示账套 · 零负担体验（推荐）</strong>
            <span>体验“老板白话视图 + 会计账表同源”、银行流水 100% 勾稽与月度合规封账，不用担心弄乱数据</span>
          </span>
        </button>

        <button type="button" class="start-option" @click="go('/mine/companies?create=new')">
          <i class="ri-building-line" aria-hidden="true" />
          <span class="start-copy">
            <strong>我是老板 · 立即建立真实企业账套</strong>
            <span>好公司不是融到资才开始规范财务。输入主体信息，从第一天起按你期许的公司样子建账</span>
          </span>
        </button>
      </div>
    </div>

    <!-- 非演示账套：常规公司建账引导 -->
    <div v-else class="onboarding-regular-flow">
      <div class="welcome-block">
        <p class="welcome-company">{{ currentCompany?.name || '当前账套' }}</p>
        <p class="welcome-line">
          启用日期
          <strong>{{ currentCompany?.startDate || '未填写' }}</strong>
        </p>
      </div>
      <p class="welcome-note">
        好公司不是融到资才开始规范财务。启用日期之前的钱和欠款记在「期初」，之后每一笔做到票、流、账 100% 勾稽与公私隔离——不仅让你看懂真实利润与现金流，更让账本随时经得起银行授信与投资人尽调。
      </p>

      <div class="start-options" style="margin-top: 14px;">
        <button type="button" class="start-option" @click="go('/mine/companies?create=new')">
          <i class="ri-seedling-line" aria-hidden="true" />
          <span class="start-copy">
            <strong>我是新成立的公司 · 从零建账</strong>
            <span>填一次主体信息，0 期初直接开记，从第一天起养出合规资产账</span>
          </span>
        </button>
        <button type="button" class="start-option" @click="go('/mine/companies?create=migration')">
          <i class="ri-exchange-box-line" aria-hidden="true" />
          <span class="start-copy">
            <strong>从其他代账/软件转入 · 导入期初</strong>
            <span>告别外部代账黑盒，导入科目余额表 Excel，把财务掌控权拿回自己手里</span>
          </span>
        </button>
        <button type="button" class="start-option" @click="go('/guide')">
          <i class="ri-eye-line" aria-hidden="true" />
          <span class="start-copy">
            <strong>先看看示例账套</strong>
            <span>拿一本三个月的标准合规演示账，花 3 分钟建立老板看账直觉</span>
          </span>
        </button>
      </div>
    </div>

    <template #footer>
      <div class="welcome-footer">
        <SoloButton variant="ghost" size="small" @click="close">稍后再说，先看看</SoloButton>
        <SoloButton v-if="isDemo" variant="primary" size="small" @click="close">进入系统</SoloButton>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.welcome-block {
  padding: 12px 14px;
  border-radius: var(--solo-radius-control);
  background: var(--solo-bg-soft);
}

.welcome-company {
  margin: 0;
  color: var(--solo-text-primary);
  font-size: 15px;
  font-weight: 650;
}

.welcome-line {
  margin: 6px 0 0;
  color: var(--solo-text-secondary);
  font-size: 12.5px;
}

.welcome-line strong {
  color: var(--solo-text-primary);
  font-weight: 650;
}

.welcome-note {
  margin: 12px 0 0;
  color: var(--solo-text-secondary);
  font-size: 12.5px;
  line-height: 1.6;
}

.onboarding-sandbox-flow {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.start-options {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.start-option {
  display: flex;
  align-items: center;
  gap: 14px;
  width: 100%;
  padding: 14px 16px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-control);
  background: var(--solo-bg-card);
  color: var(--solo-text-primary);
  font: inherit;
  text-align: left;
  cursor: pointer;
  transition: border-color 160ms ease-out, background-color 160ms ease-out;
}

.start-option:hover {
  border-color: var(--solo-primary);
  background: var(--solo-bg-blue-soft);
}

.start-option:focus-visible {
  outline: 2px solid var(--solo-primary);
  outline-offset: 1px;
}

.start-option > i {
  color: var(--solo-primary);
  font-size: 22px;
  flex-shrink: 0;
}

.start-copy {
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.start-copy strong {
  font-size: 13.5px;
  font-weight: 650;
}

.start-copy span {
  color: var(--solo-text-secondary);
  font-size: 12px;
  line-height: 1.4;
}

.welcome-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}

.demo-sandbox-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: var(--solo-bg-blue-soft);
  border: 1px solid var(--solo-border);
}

.sandbox-tag {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  align-self: flex-start;
  padding: 3px 8px;
  border-radius: var(--solo-radius-control);
  background: var(--solo-bg-card);
  color: var(--solo-primary);
  font-size: 11px;
  font-weight: 600;
}

.welcome-desc {
  margin: 0;
  color: var(--solo-text-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.start-option-featured {
  border-color: var(--solo-primary);
  background: var(--solo-bg-blue-soft);
}
</style>
