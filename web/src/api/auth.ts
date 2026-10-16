/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'
import type {
  AuthResponse,
  PhoneLoginRequest,
  PhoneSendCodeRequest,
  PhoneSendCodeResponse,
  PasswordLoginRequest,
  ResetPasswordRequest,
  SetPasswordRequest,
  UserProfile,
  UpdateProfileRequest,
  CreateCompanyRequest,
  CompanyInfo,
  SwitchCompanyRequest,
} from './types'

export function sendPhoneCode(data: PhoneSendCodeRequest): Promise<PhoneSendCodeResponse> {
  return apiClient.post('/api/auth/phone/send-code', data)
}

export function loginByPhoneCode(data: PhoneLoginRequest): Promise<AuthResponse> {
  return apiClient.post('/api/auth/phone/login', data, { timeout: 90000 })
}

export function loginByPassword(data: PasswordLoginRequest): Promise<AuthResponse> {
  return apiClient.post('/api/auth/password/login', data)
}

export function setPassword(
  data: SetPasswordRequest,
): Promise<{ success: boolean; has_password: boolean }> {
  return apiClient.post('/api/auth/password/set', data)
}

export function resetPassword(data: ResetPasswordRequest): Promise<{ success: boolean }> {
  return apiClient.post('/api/auth/password/reset', data)
}

export function bindPhone(data: PhoneLoginRequest): Promise<{ message: string }> {
  return apiClient.post('/api/auth/phone/bind', data)
}

export function getAuthMe(): Promise<UserProfile> {
  return apiClient.get('/api/auth/me')
}

export function updateProfile(data: UpdateProfileRequest): Promise<{ message: string }> {
  return apiClient.post('/api/auth/profile', data)
}

export function switchCompany(data: SwitchCompanyRequest): Promise<AuthResponse> {
  return apiClient.post('/api/auth/switch-company', data)
}

export function createCompany(data: CreateCompanyRequest): Promise<CompanyInfo> {
  return apiClient.post('/api/auth/companies', data)
}

export function updateCompany(companyId: string, data: Partial<CreateCompanyRequest>): Promise<CompanyInfo> {
  return apiClient.put(`/api/auth/companies/${companyId}`, data)
}

export function requestStepUp(data: {
  action: 'reopen_period' | 'confirm_legacy_migration' | 'adjust_opening_balance'
  phone: string
  code: string
  bizId?: string
}): Promise<{ stepUpToken: string; expiresIn: number }> {
  return apiClient.post('/api/auth/step-up', data)
}
