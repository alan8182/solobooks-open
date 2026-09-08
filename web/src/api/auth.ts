import apiClient from './client'
import type {
  AuthResponse,
  PhoneLoginRequest,
  PhoneSendCodeRequest,
  PhoneSendCodeResponse,
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
