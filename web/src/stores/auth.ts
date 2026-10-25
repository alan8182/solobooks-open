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
    const token = ref<string | null>(null)
    const userId = ref<string | null>(null)
    const user = ref<UserProfile | null>(null)

    if (token.value && !localStorage.getItem('auth_token')) {
      localStorage.setItem('auth_token', token.value)
    }

    const isAuthenticated = computed(() => !!token.value)
    const currentCompanyId = computed(() => user.value?.current_company_id ?? null)
    const companies = computed(() => user.value?.companies ?? [])

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
    },
  },
)
