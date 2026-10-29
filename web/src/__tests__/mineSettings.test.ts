/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useWorkspaceViewStore } from '@/stores/workspaceView'

function maskPhone(phone?: string) {
  if (!phone) return ''
  return phone.replace(/(\d{3})\d{4}(\d{4})/, '$1****$2')
}

function validatePasswordInput(
  hasPassword: boolean,
  oldPwd: string,
  newPwd: string,
  confirmPwd: string,
): { valid: boolean; error?: string } {
  if (hasPassword && !oldPwd) {
    return { valid: false, error: '请输入当前密码' }
  }
  if (newPwd.length < 8) {
    return { valid: false, error: '密码长度至少 8 位' }
  }
  if (newPwd !== confirmPwd) {
    return { valid: false, error: '两次输入的新密码不一致' }
  }
  return { valid: true }
}

describe('mine settings security and helpers', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  describe('validatePasswordInput', () => {
    it('requires old password when hasPassword is true', () => {
      const res = validatePasswordInput(true, '', 'newpass123', 'newpass123')
      expect(res.valid).toBe(false)
      expect(res.error).toBe('请输入当前密码')
    })

    it('allows empty old password when hasPassword is false (first time set)', () => {
      const res = validatePasswordInput(false, '', 'newpass123', 'newpass123')
      expect(res.valid).toBe(true)
    })

    it('rejects passwords shorter than 8 chars', () => {
      const res = validatePasswordInput(false, '', '1234567', '1234567')
      expect(res.valid).toBe(false)
      expect(res.error).toBe('密码长度至少 8 位')
    })

    it('rejects mismatched new and confirm passwords', () => {
      const res = validatePasswordInput(true, 'oldpass123', 'newpass123', 'mismatch456')
      expect(res.valid).toBe(false)
      expect(res.error).toBe('两次输入的新密码不一致')
    })

    it('accepts valid password update payload', () => {
      const res = validatePasswordInput(true, 'oldpass123', 'newpass123', 'newpass123')
      expect(res.valid).toBe(true)
      expect(res.error).toBeUndefined()
    })
  })

  describe('maskPhone', () => {
    it('handles empty input gracefully', () => {
      expect(maskPhone('')).toBe('')
      expect(maskPhone(undefined)).toBe('')
    })

    it('correctly masks 11-digit phone number', () => {
      expect(maskPhone('13812345678')).toBe('138****5678')
    })
  })

  describe('workspace view preference integration', () => {
    it('switches mode between boss and accounting cleanly', () => {
      const store = useWorkspaceViewStore()
      expect(store.mode).toBe('boss')

      store.setMode('accounting')
      expect(store.mode).toBe('accounting')
      expect(store.isAccounting).toBe(true)
      expect(store.isBoss).toBe(false)

      store.setMode('boss')
      expect(store.mode).toBe('boss')
      expect(store.isBoss).toBe(true)
    })
  })
})
