/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks license v2.0.
 */

import { onMounted, ref, watch } from 'vue'
import { getSetupReadiness } from '@/api/setup'
import type { SetupReadiness } from '@/api/types'
import { useAuthStore } from '@/stores/auth'
import { useGuideStateStore } from '@/stores/guideState'
import { useWorkspaceViewStore } from '@/stores/workspaceView'

/**
 * 账套就绪度的唯一取数入口。
 *
 * 失败时**不弹提示**：引导挂掉不该打断用户记账，调用方拿到 `failed` 后
 * 整块不渲染即可（框架原则五：引导可以失灵，但不能挡住主流程）。
 */
export function useSetupReadiness(options: { autoLoad?: boolean } = {}) {
  const authStore = useAuthStore()
  const viewStore = useWorkspaceViewStore()
  const guideStore = useGuideStateStore()

  const readiness = ref<SetupReadiness | null>(null)
  const loading = ref(true)
  const failed = ref(false)

  async function load() {
    if (!authStore.currentCompanyId) {
      readiness.value = null
      loading.value = false
      return
    }
    loading.value = true
    failed.value = false
    try {
      const data = await getSetupReadiness({
        view: viewStore.mode,
        waived: guideStore.state.waived,
      })
      readiness.value = data
      if (data.ready) guideStore.markReady()
    } catch {
      readiness.value = null
      failed.value = true
    } finally {
      loading.value = false
    }
  }

  // 切账套或切视图都要重算：同一份状态，两个视图只换文案
  watch(
    [() => authStore.currentCompanyId, () => viewStore.mode],
    () => {
      void load()
    },
  )

  if (options.autoLoad !== false) onMounted(load)

  return { readiness, loading, failed, reload: load }
}
