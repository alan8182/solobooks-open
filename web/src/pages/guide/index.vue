<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks license v2.0. -->
<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { NPopconfirm, useMessage } from 'naive-ui'
import { SoloButton, SoloStatusBadge } from '@/components/ui'
import SetupChecklist from '@/components/business/SetupChecklist.vue'
import { createDemoCompany, deleteDemoCompany } from '@/api/setup'
import { useSetupReadiness } from '@/composables/useSetupReadiness'
import { useAuthStore } from '@/stores/auth'
import { useGuideStateStore } from '@/stores/guideState'
import { GLOSSARY } from '@/utils/glossary'

/**
 * 上手引导总页。
 *
 * 首页的清单是「路过时看到」，这里是「主动来查」：完整清单 + 常见说法 +
 * 重放入口 + 演示账套。首页清单就绪后折叠，用户想再看只能从这里进。
 */
const router = useRouter()
const message = useMessage()
const authStore = useAuthStore()
const guideStore = useGuideStateStore()
const { readiness, loading } = useSetupReadiness()

const creating = ref(false)
const deleting = ref(false)

const demoCompany = computed(() => authStore.companies.find((item) => item.isDemo) ?? null)
const isDemoCurrent = computed(
  () => Boolean(demoCompany.value) && demoCompany.value?.company_id === authStore.currentCompanyId,
)
const progressText = computed(() =>
  readiness.value ? `${readiness.value.completed}/${readiness.value.total}` : '—',
)


async function handleCreateDemo() {
  if (creating.value) return
  creating.value = true
  try {
    const result = await createDemoCompany()
    guideStore.patch({ demoCompanyId: result.companyId })
    message.success(result.reused ? '已复用现有演示账套' : `演示账套已生成（${result.entries} 笔）`)
    await enterCompany(result.companyId)
  } catch (err) {
    const detail = (err as { response?: { data?: { detail?: { message?: string } } } })?.response
      ?.data?.detail
    message.error(detail?.message || '生成演示账套失败，请稍后重试')
  } finally {
    creating.value = false
  }
}

async function handleDeleteDemo() {
  const target = demoCompany.value
  if (!target || deleting.value) return
  deleting.value = true
  try {
    const result = await deleteDemoCompany(target.company_id)
    guideStore.patch({ demoCompanyId: null })
    if (result.nextCompanyId) {
      await authStore.switchCompanyContext(result.nextCompanyId)
      message.success('演示账套已删除，已切换账套')
    } else {
      message.success('演示账套已删除')
    }
    await authStore.fetchProfile()
  } catch (err) {
    const detail = (err as { response?: { data?: { detail?: { message?: string } } } })?.response
      ?.data?.detail
    message.error(detail?.message || '删除演示账套失败')
  } finally {
    deleting.value = false
  }
}

async function enterCompany(companyId: string) {
  if (companyId === authStore.currentCompanyId) {
    await router.push('/')
    return
  }
  await authStore.switchCompanyContext(companyId)
  await router.push('/')
}

function replayWelcome() {
  // AppLayout 里消费这个标志打开 L0；放在 sessionStorage 是刻意的，
  // 它只影响本次会话，不会让用户下次登录又看一遍
  sessionStorage.setItem('solo:l0-replay', '1')
  router.push('/')
}

function expandChecklist() {
  guideStore.patch({ collapsed: false, dismissedAt: null, archivedAt: null, readyAt: null })
  message.success('清单已重新展开')
}

function clearWaived() {
  guideStore.patch({ waived: [] })
  message.success('已清除跳过标记')
}
</script>

<template>
  <div class="guide-page">
    <header class="page-header">
      <div class="header-copy">
        <h2>上手引导</h2>
        <p class="subtitle">
          不是为了教你用软件，是为了把账做到能出报表、能报税。每一项都按真实数据判定，不用你打勾。
        </p>
      </div>
      <SoloStatusBadge v-if="isDemoCurrent" tone="info">演示账套 · 样本数据</SoloStatusBadge>
      <SoloStatusBadge v-else-if="readiness" :tone="readiness.ready ? 'success' : 'neutral'">
        {{ readiness.ready ? '账套已就绪' : '进行中' }} · {{ progressText }}
      </SoloStatusBadge>
    </header>

    <p v-if="isDemoCurrent" class="demo-progress-note">
      以下完成度来自演示账套的样本数据，不计入你的上手进度。
      <a @click="router.push('/mine/companies')">创建我自己的账套</a>
    </p>

    <SetupChecklist :readiness="readiness" :loading="loading" mode="page" />

    <section class="guide-card">
      <header class="card-header">
        <h3>常见说法</h3>
        <span class="card-note">这些词在系统里都长一个样</span>
      </header>
      <div class="glossary-grid">
        <div v-for="item in GLOSSARY" :key="item.term" class="glossary-item">
          <strong>{{ item.term }}</strong>
          <span>{{ item.plain }}</span>
          <em v-if="item.detail">{{ item.detail }}</em>
          <button
            v-if="item.see"
            type="button"
            class="glossary-link"
            @click="router.push(item.see)"
          >
            去看看
            <i class="ri-arrow-right-line" aria-hidden="true" />
          </button>
        </div>
      </div>
    </section>

    <section class="guide-card">
      <header class="card-header">
        <h3>演示账套</h3>
        <span v-if="demoCompany" class="card-note">注册时自动生成 · 可随时删除</span>
      </header>
      <p class="card-body">
        每个账户注册时会自动生成一本带三个月流水的演示账，直接看报表、结账和申报长什么样。演示账套与你的真实账套完全隔离，删除后可在这里重新生成。
      </p>
      <div class="card-actions">
        <SoloButton
          v-if="!demoCompany"
          variant="primary"
          :loading="creating"
          icon="ri-sparkling-line"
          @click="handleCreateDemo"
        >
          {{ creating ? '正在生成（约 10 秒）' : '重新生成演示账套' }}
        </SoloButton>
        <template v-else>
          <SoloButton
            variant="primary"
            icon="ri-eye-line"
            :disabled="isDemoCurrent"
            @click="demoCompany && enterCompany(demoCompany.company_id)"
          >
            {{ isDemoCurrent ? '当前已在演示账套' : '进入演示账套' }}
          </SoloButton>
          <NPopconfirm @positive-click="handleDeleteDemo">
            <template #trigger>
              <SoloButton variant="ghost" :loading="deleting">删除演示账套</SoloButton>
            </template>
            删除后这本演示账的全部数据都会清空，不影响你的真实账套。确认删除？
          </NPopconfirm>
        </template>
      </div>
    </section>

    <section class="guide-card">
      <header class="card-header">
        <h3>重放与重置</h3>
      </header>
      <p class="card-body">清单只在你没配完时出现，配完会自动收起。想再看一遍可以从这里重放。</p>
      <div class="card-actions">
        <SoloButton variant="secondary" icon="ri-restart-line" @click="replayWelcome">
          重看新手引导
        </SoloButton>
        <SoloButton variant="secondary" @click="expandChecklist">重新展开清单</SoloButton>
        <SoloButton variant="ghost" @click="clearWaived">清除跳过标记</SoloButton>
      </div>
    </section>
  </div>
</template>

<style scoped>
.guide-page {
  display: flex;
  flex-direction: column;
  gap: 18px;
  max-width: 1080px;
  margin: 0 auto;
}

.page-header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px;
  margin-bottom: 2px;
}

.header-copy h2 {
  margin: 0;
  color: var(--solo-text-primary);
  font-size: 24px;
  font-weight: 650;
  letter-spacing: -0.5px;
}

.subtitle {
  margin: 8px 0 0;
  max-width: 620px;
  color: var(--solo-text-secondary);
  font-size: 13px;
  line-height: 1.6;
}

.demo-progress-note {
  margin: 0;
  padding: 10px 14px;
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius);
  background: var(--solo-bg-blue-soft);
  color: var(--solo-text-secondary);
  font-size: 12.5px;
  line-height: 1.6;
}

.demo-progress-note a {
  color: var(--solo-primary);
  font-weight: 600;
  cursor: pointer;
}

.guide-card {
  border: 1px solid var(--solo-border);
  border-radius: var(--solo-radius-lg);
  background: var(--solo-bg-card);
  padding: 16px 18px 18px;
}

.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.card-header h3 {
  margin: 0;
  color: var(--solo-text-primary);
  font-size: 14.5px;
  font-weight: 650;
}

.card-note {
  color: var(--solo-text-muted);
  font-size: 11.5px;
}

.card-body {
  margin: 8px 0 0;
  color: var(--solo-text-secondary);
  font-size: 12.5px;
  line-height: 1.6;
}

.card-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-top: 14px;
}

.glossary-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px 18px;
  margin: 12px 0 0;
}

.glossary-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 10px 12px 12px;
  border: 1px solid var(--solo-border-light);
  border-radius: 8px;
  background: var(--solo-bg-soft);
}

.glossary-item strong {
  color: var(--solo-text-primary);
  font-size: 13px;
  font-weight: 650;
}

.glossary-item span {
  color: var(--solo-text-secondary);
  font-size: 12px;
  line-height: 1.6;
}

.glossary-item em {
  color: var(--solo-text-muted);
  font-size: 11.5px;
  font-style: normal;
  line-height: 1.6;
}

.glossary-link {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 2px 0;
  border: 0;
  background: transparent;
  color: var(--solo-primary);
  font: inherit;
  font-size: 11.5px;
  font-weight: 650;
  cursor: pointer;
}

.glossary-link:hover {
  text-decoration: underline;
}

@media (max-width: 768px) {
  .glossary-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 768px) {
  .page-header {
    flex-direction: column;
    align-items: flex-start;
  }
}
</style>
