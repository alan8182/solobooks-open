/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { computed, ref, watch } from 'vue'
import { defineStore } from 'pinia'
import { useAuthStore } from '@/stores/auth'
import { isWorkspaceView, type WorkspaceView } from '@/utils/workspace'

const STORAGE_PREFIX = 'solo:workspace-view:'

export function workspaceStorageKey(companyId: string | null): string {
  return `${STORAGE_PREFIX}${companyId || 'default'}`
}

export function readWorkspaceView(companyId: string | null): WorkspaceView {
  try {
    const stored = localStorage.getItem(workspaceStorageKey(companyId))
    return isWorkspaceView(stored) ? stored : 'boss'
  } catch {
    return 'boss'
  }
}

export function writeWorkspaceView(companyId: string | null, mode: WorkspaceView): void {
  try {
    localStorage.setItem(workspaceStorageKey(companyId), mode)
  } catch {
    // Private mode or storage quota failure should not block the workspace.
  }
}

export const useWorkspaceViewStore = defineStore('workspaceView', () => {
  const authStore = useAuthStore()
  const mode = ref<WorkspaceView>(readWorkspaceView(authStore.currentCompanyId))

  const isBoss = computed(() => mode.value === 'boss')
  const isAccounting = computed(() => mode.value === 'accounting')

  function load() {
    mode.value = readWorkspaceView(authStore.currentCompanyId)
  }

  function setMode(next: WorkspaceView) {
    if (!isWorkspaceView(next)) return
    mode.value = next
    writeWorkspaceView(authStore.currentCompanyId, next)
  }

  function toggleMode() {
    setMode(mode.value === 'boss' ? 'accounting' : 'boss')
  }

  watch(
    () => authStore.currentCompanyId,
    () => load(),
  )

  return {
    mode,
    isBoss,
    isAccounting,
    load,
    setMode,
    toggleMode,
  }
})
