<!-- Copyright (c) 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks license v2.0. -->
<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { NModal } from 'naive-ui'
import { SoloButton } from '@/components/ui'
import { muteCurrentRescue, rescueCurrent, rescueNext, rescuePendingCount } from '@/composables/useRescue'
import type { RescueAction } from '@/utils/rescueRules'

/**
 * 错误救援弹窗：错误码 → 症状 + 为什么 + 出路。
 *
 * 与 `StartDateGuideModal` 并存不冲突：那个只处理 `ENTRY_BEFORE_START_DATE`
 * （记账日期早于启用日期），这个处理「进去了但被后端拒绝」的一类。
 */
const router = useRouter()

const current = computed(() => rescueCurrent())
const actions = computed<RescueAction[]>(() =>
  current.value ? current.value.rule.actions(current.value.ctx) : [],
)

function runAction(action: RescueAction) {
  if (action.path) {
    if (action.query) {
      router.push({ path: action.path, query: action.query })
    } else {
      router.push(action.path)
    }
  }
  action.run?.()
  rescueNext()
}
</script>

<template>
  <NModal
    :show="Boolean(current)"
    preset="card"
    :title="current?.rule.title || ''"
    :style="{ width: '520px', maxWidth: '92vw' }"
    @update:show="rescueNext()"
  >
    <template v-if="current">
      <p class="rescue-reason">{{ current.reason }}</p>
      <p v-if="rescuePendingCount > 1" class="rescue-queue">
        还有 {{ rescuePendingCount - 1 }} 条错误待处理
      </p>
      <p class="rescue-code">错误码 {{ current.rule.code }}</p>
    </template>

    <template #footer>
      <div class="rescue-footer">
        <button type="button" class="rescue-mute" @click="muteCurrentRescue()">
          不再提示这个错误
        </button>
        <div class="rescue-actions">
          <SoloButton
            v-for="action in actions"
            :key="action.label"
            :variant="action.kind === 'primary' ? 'primary' : action.kind === 'secondary' ? 'secondary' : 'ghost'"
            size="small"
            @click="runAction(action)"
          >
            {{ action.label }}
          </SoloButton>
        </div>
      </div>
    </template>
  </NModal>
</template>

<style scoped>
.rescue-reason {
  margin: 0;
  color: var(--solo-text-primary);
  font-size: 13.5px;
  line-height: 1.7;
}

.rescue-queue {
  margin: 10px 0 0;
  color: var(--solo-text-secondary);
  font-size: 12px;
}

.rescue-code {
  margin: 10px 0 0;
  color: var(--solo-text-muted);
  font-family: var(--solo-font-mono, monospace);
  font-size: 11px;
}

.rescue-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.rescue-mute {
  padding: 4px 0;
  border: 0;
  background: transparent;
  color: var(--solo-text-muted);
  font: inherit;
  font-size: 11.5px;
  cursor: pointer;
}

.rescue-mute:hover {
  color: var(--solo-text-secondary);
  text-decoration: underline;
}

.rescue-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
</style>
