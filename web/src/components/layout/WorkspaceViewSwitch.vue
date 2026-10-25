<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
import { useMessage } from 'naive-ui'
import { useWorkspaceViewStore } from '@/stores/workspaceView'
import type { WorkspaceView } from '@/utils/workspace'

const viewStore = useWorkspaceViewStore()
const message = useMessage()

function choose(mode: WorkspaceView) {
  if (viewStore.mode === mode) return
  viewStore.setMode(mode)
  message.success(mode === 'boss' ? '已切换到老板视图' : '已切换到会计视图')
}
</script>

<template>
  <div
    class="workspace-switch"
    role="group"
    aria-label="切换工作视图"
    title="只改变展示方式，不改变权限"
  >
    <span class="switch-caption">视图</span>
    <button
      type="button"
      class="switch-option"
      :class="{ active: viewStore.isBoss }"
      :aria-pressed="viewStore.isBoss"
      @click="choose('boss')"
    >
      老板
    </button>
    <button
      type="button"
      class="switch-option"
      :class="{ active: viewStore.isAccounting }"
      :aria-pressed="viewStore.isAccounting"
      @click="choose('accounting')"
    >
      会计
    </button>
  </div>
</template>

<style scoped>
/* 与全站分段控件同一长相：浅底凹槽 3px / 圆角 8px / 白色滑块 + 品牌蓝文字。
   不要改成胶囊或加描边 —— 内容区里的 NRadioGroup 已经是这个形状。 */
.workspace-switch {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  border-radius: 8px;
  background: var(--solo-bg-soft);
  flex-shrink: 0;
}

.switch-caption {
  padding: 0 6px 0 8px;
  color: var(--solo-text-muted);
  font-size: 11px;
  font-weight: 600;
}

.switch-option {
  min-height: 26px;
  padding: 3px 10px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--solo-text-secondary);
  font: inherit;
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
  transition: background-color 160ms ease-out, color 160ms ease-out;
}

.switch-option:hover {
  color: var(--solo-primary);
}

.switch-option:focus-visible {
  outline: 2px solid var(--solo-primary);
  outline-offset: 1px;
}

.switch-option.active {
  background: var(--solo-bg-card);
  color: var(--solo-primary);
  box-shadow: var(--solo-shadow-sm);
}

@media (max-width: 720px) {
  .switch-caption {
    display: none;
  }

  .switch-option {
    padding-inline: 9px;
  }
}
</style>
