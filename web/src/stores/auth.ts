/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import type { UserProfile } from '@/api/types'
import { getAuthMe, switchCompany as apiSwitchCompany } from '@/api/auth'

export const useAuthStore = defineStore(
  'auth',
  () => {
    // 登录态的单一真相是 localStorage.auth_token：路由守卫、RootSwitch、apiClient
    // 都直接读它，只有 store 认 auth_state 快照。两边不一致时（auth_state 缺失/过期、
    // 存储被部分清理），守卫判定「已登录」而 store 判定「未登录」，
    // AppLayout 会 push('/login') 又被守卫弹回 '/'，页面永远停在「加载中...」。
    // 因此 store 初始化以 auth_token 为准，persist 恢复后再对齐一次（见下方 afterHydrate）。
    const token = ref<string | null>(localStorage.getItem('auth_token'))
    const userId = ref<string | null>(null)
    const user = ref<UserProfile | null>(null)

    if (token.value && !localStorage.getItem('auth_token')) {
      localStorage.setItem('auth_token', token.value)
    }

    const isAuthenticated = computed(() => !!token.value)
    const currentCompanyId = computed(() => user.value?.current_company_id ?? null)
    const companies = computed(() => user.value?.companies ?? [])
    const openingBalanceRequired = computed(() => Boolean(user.value?.opening_balance_required))
    const openingBalanceReason = computed(() => user.value?.opening_balance_reason ?? null)

    function setSession(data: { access_token: string; user_id: string; company_id: string }) {
      token.value = data.access_token
      userId.value = data.user_id
      localStorage.setItem('auth_token', data.access_token)
    }

    async function fetchProfile() {
      try {
        const profile = await getAuthMe()
        user.value = profile
        return profile
      } catch {
        logout()
        throw new Error('Failed to fetch profile')
      }
    }

    async function switchCompanyContext(companyId: string) {
      const resp = await apiSwitchCompany({ company_id: companyId })
      token.value = resp.access_token
      localStorage.setItem('auth_token', resp.access_token)
      await fetchProfile()
    }

    function logout() {
      token.value = null
      userId.value = null
      user.value = null
      localStorage.removeItem('auth_token')
    }

    return {
      token,
      userId,
      user,
      isAuthenticated,
      currentCompanyId,
      companies,
      openingBalanceRequired,
      openingBalanceReason,
      setSession,
      fetchProfile,
      switchCompanyContext,
      logout,
    }
  },
  {
    persist: {
      key: 'auth_state',
      storage: localStorage,
      pick: ['token', 'userId'],
      // hydrateStore 会用 auth_state 快照 $patch 覆盖 token（发生在 setup 之后），
      // 所以对齐必须放在 afterHydrate：缺 auth_token 时回写，快照过期时以 auth_token 为准。
      afterHydrate: ({ store }) => {
        const raw = localStorage.getItem('auth_token')
        if (raw) {
          if (store.token !== raw) store.token = raw
        } else if (store.token) {
          localStorage.setItem('auth_token', store.token)
        }
      },
    },
  },
)
