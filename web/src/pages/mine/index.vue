<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { NCard, NButton, useMessage } from 'naive-ui'
import { useAuthStore } from '@/stores/auth'
import { useEntryStore } from '@/stores/entry'
import { formatMoney } from '@/utils/format'

const router = useRouter()
const message = useMessage()
const authStore = useAuthStore()
const entryStore = useEntryStore()

const user = ref(authStore.user)

async function handleLogout() {
  authStore.logout()
  message.success('已退出登录')
  router.push('/login')
}

onMounted(async () => {
  if (!authStore.user && authStore.token) {
    try {
      await authStore.fetchProfile()
      user.value = authStore.user
    } catch {
      // ignore
    }
  }
})
</script>

<template>
  <div class="mine-page">
    <div class="page-header">
      <h2>个人中心</h2>
    </div>

    <!-- User info -->
    <NCard :bordered="false" class="user-card mb-4">
      <div class="user-info">
        <div class="user-avatar">{{ user?.nickname?.[0] || '?' }}</div>
        <div class="user-detail">
          <div class="user-name">{{ user?.nickname || '用户' }}</div>
          <div class="user-phone">{{ user?.phone || '' }}</div>
        </div>
      </div>
    </NCard>

    <!-- Stats -->
    <NCard :bordered="false" class="stats-card mb-4">
      <div class="stats-grid">
        <div class="stat-item">
          <span class="stat-number">{{ entryStore.activeEntries.length }}</span>
          <span class="stat-label">记账笔数</span>
        </div>
        <div class="stat-item">
          <span class="stat-number income">{{ formatMoney(entryStore.monthlyIncome) }}</span>
          <span class="stat-label">本月收入</span>
        </div>
        <div class="stat-item">
          <span class="stat-number expense">{{ formatMoney(entryStore.monthlyExpense) }}</span>
          <span class="stat-label">本月支出</span>
        </div>
      </div>
    </NCard>

    <!-- Menu list -->
    <NCard :bordered="false" class="menu-card mb-4">
      <div class="menu-item" @click="router.push('/mine/companies')">
        <span class="menu-icon">🏢</span>
        <span class="menu-label">公司管理</span>
        <span class="menu-arrow">→</span>
      </div>
      <div class="menu-divider" />
      <div class="menu-item" @click="router.push('/mine/categories')">
        <span class="menu-icon">📂</span>
        <span class="menu-label">分类管理</span>
        <span class="menu-arrow">→</span>
      </div>
      <div class="menu-divider" />
      <div class="menu-item" @click="router.push('/mine/accounts')">
        <span class="menu-icon">📊</span>
        <span class="menu-label">科目表</span>
        <span class="menu-arrow">→</span>
      </div>
      <div class="menu-divider" />
      <div class="menu-item" @click="router.push('/mine/salary')">
        <span class="menu-icon">💵</span>
        <span class="menu-label">薪酬社保</span>
        <span class="menu-arrow">→</span>
      </div>
      <div class="menu-divider" />
      <div class="menu-item" @click="router.push('/mine/opening')">
        <span class="menu-icon">⚖️</span>
        <span class="menu-label">期初余额</span>
        <span class="menu-arrow">→</span>
      </div>
      <div class="menu-divider" />
      <div class="menu-item" @click="router.push('/recurring')">
        <span class="menu-icon">🔄</span>
        <span class="menu-label">周期性模板</span>
        <span class="menu-arrow">→</span>
      </div>
      <div class="menu-divider" />
      <div class="menu-item" @click="router.push('/bank')">
        <span class="menu-icon">🏦</span>
        <span class="menu-label">银行对账</span>
        <span class="menu-arrow">→</span>
      </div>
      <div class="menu-divider" />
      <div class="menu-item" @click="router.push('/mine/audit-log')">
        <span class="menu-icon">📋</span>
        <span class="menu-label">审计日志</span>
        <span class="menu-arrow">→</span>
      </div>
    </NCard>

    <NButton block @click="handleLogout">退出登录</NButton>
  </div>
</template>

<style scoped>
.mine-page {
  max-width: 640px;
  margin: 0 auto;
}
.page-header {
  margin-bottom: 20px;
}
.page-header h2 {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
}
.mb-4 { margin-bottom: 16px; }

/* User */
.user-card {
  border-radius: var(--solo-radius-lg);
}
.user-info {
  display: flex;
  align-items: center;
  gap: 16px;
}
.user-avatar {
  width: 52px;
  height: 52px;
  border-radius: 50%;
  background: var(--solo-primary);
  color: #fff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 22px;
  font-weight: 700;
}
.user-name {
  font-size: 18px;
  font-weight: 600;
  color: var(--solo-text-primary);
}
.user-phone {
  font-size: 13px;
  color: var(--solo-text-muted);
  margin-top: 2px;
}

/* Stats */
.stats-card {
  border-radius: var(--solo-radius-lg);
}
.stats-grid {
  display: flex;
  gap: 24px;
}
.stat-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.stat-number {
  font-size: 18px;
  font-weight: 700;
  color: var(--solo-text-primary);
}
.stat-number.income { color: var(--solo-income); }
.stat-number.expense { color: var(--solo-expense); }
.stat-label {
  font-size: 12px;
  color: var(--solo-text-muted);
}

/* Menu */
.menu-card {
  border-radius: var(--solo-radius-lg);
}
.menu-item {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 6px 0;
  cursor: pointer;
}
.menu-icon {
  font-size: 18px;
}
.menu-label {
  flex: 1;
  font-size: 14px;
  color: var(--solo-text-primary);
}
.menu-arrow {
  color: var(--solo-text-muted);
  font-size: 14px;
}
.menu-divider {
  height: 1px;
  background: var(--solo-border-light);
  margin: 6px 0;
}
</style>
