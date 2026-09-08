<script setup lang="ts">
import { useRouter } from 'vue-router'

const router = useRouter()

const quickActions = [
  { label: '记录一笔', desc: '快速记录收入与支出', path: '/record/text', icon: 'ri-add-line', color: 'var(--solo-primary)', bg: 'var(--solo-bg-blue-soft)' },
  { label: '拍单据', desc: '识别发票与收据', path: '/record/receipt', icon: 'ri-camera-line', color: 'var(--solo-primary)', bg: 'var(--solo-bg-blue-soft)' },
  { label: '批量导入', desc: '导入多笔经营记录', path: '/record/batch', icon: 'ri-file-upload-line', color: 'var(--solo-warning)', bg: 'var(--solo-warning-bg)' },
  { label: '问问小秘', desc: '分析经营和财税问题', path: '/copilot', icon: 'ri-sparkling-line', color: 'var(--solo-primary)', bg: 'var(--solo-bg-blue-soft)' },
]
</script>

<template>
  <div class="quick-actions-section">
    <h3 class="section-title">快捷操作</h3>
    <div class="quick-actions-grid">
      <div
        v-for="action in quickActions"
        :key="action.path"
        class="action-card"
        @click="router.push(action.path)"
      >
        <div class="action-icon-box" :style="{ background: action.bg, color: action.color }">
          <i :class="action.icon" />
        </div>
        <div class="action-copy">
          <span class="action-label">{{ action.label }}</span>
          <span class="action-desc">{{ action.desc }}</span>
        </div>
        <i class="ri-arrow-right-s-line action-arrow" />
      </div>
    </div>
  </div>
</template>

<style scoped>
.quick-actions-section {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.section-title {
  font-size: 14px;
  font-weight: 650;
  color: var(--solo-text-primary);
  margin: 0;
  letter-spacing: 0.5px;
}
.quick-actions-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 16px;
}
.action-card {
  background: var(--solo-bg-card);
  border: 1px solid var(--solo-border-glass);
  border-radius: var(--solo-radius-lg);
  padding: 15px;
  display: flex;
  flex-direction: row;
  align-items: center;
  gap: 12px;
  cursor: pointer;
  box-shadow: none;
  transition: all 0.2s cubic-bezier(0.4, 0, 0.2, 1);
}
.action-card:hover {
  border-color: rgba(22, 93, 255, 0.3);
  box-shadow: var(--solo-shadow-sm);
  transform: translateY(-1px);
}
.action-icon-box {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
  transition: transform 0.2s;
}
.action-copy {
  min-width: 0;
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 2px;
}
.action-label {
  font-size: 13px;
  font-weight: 650;
  color: var(--solo-text-primary);
}
.action-desc {
  overflow: hidden;
  color: var(--solo-text-muted);
  font-size: 11px;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.action-arrow {
  color: var(--solo-text-muted);
  font-size: 16px;
}
@media (max-width: 768px) {
  .quick-actions-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
@media (max-width: 1180px) {
  .quick-actions-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}
@media (max-width: 480px) {
  .quick-actions-grid { grid-template-columns: 1fr; }
}
</style>
