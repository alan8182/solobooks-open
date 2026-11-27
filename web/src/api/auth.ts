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
  LicenseOcrResult,
  SwitchCompanyRequest,
  WechatWebAuthorizeUrlParams,
  WechatWebAuthorizeUrlResponse,
  WechatWebBindPhoneRequest,
  WechatWebConfigResponse,
  WechatWebExchangeResult,
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

// ── 微信开放平台网站应用扫码登录（PC）──
// pending ticket / state 全部在 HttpOnly cookie 里，URL 与 JS 都拿不到。
// withCredentials 让跨域 staging 也能带上 cookie（生产同源无影响）。

/** 微信网站应用登录开关；enabled=false 时登录页隐藏入口。 */
export function getWechatWebConfig(): Promise<WechatWebConfigResponse> {
  return apiClient.get('/api/auth/wechat/web/config', { withCredentials: true })
}

/** 生成授权 URL；后端同时 Set-Cookie `wx_web_state`（HttpOnly，防 CSRF）。 */
export async function getWechatWebAuthorizeUrl(
  params: WechatWebAuthorizeUrlParams = {},
): Promise<WechatWebAuthorizeUrlResponse> {
  // 后端字段名是 authorize_url（历史契约写作 url），在 API 层统一，页面只认 url
  const data = await apiClient.get<{ url?: string; authorize_url?: string }>(
    '/api/auth/wechat/web/authorize-url',
    { params, withCredentials: true },
  )
  return { url: data?.url || data?.authorize_url || '' }
}

/** 用 pending cookie 换取登录态；ticket 不进 URL、无请求体。 */
export async function exchangeWechatWebTicket(): Promise<WechatWebExchangeResult> {
  // 后端返回 phone_required（snake_case），归一化成页面契约 phoneRequired
  const data = await apiClient.post<
    Partial<AuthResponse> & { phoneRequired?: boolean; phone_required?: boolean }
  >('/api/auth/wechat/web/exchange', undefined, {
    withCredentials: true,
    timeout: 90000,
  })
  if (data?.phoneRequired || data?.phone_required) return { phoneRequired: true }
  return data as AuthResponse
}

/** 网站微信首次登录绑定手机号；号码已有老账号时由后端合并，前端不做判断。 */
export function bindWechatWebPhone(data: WechatWebBindPhoneRequest): Promise<AuthResponse> {
  return apiClient.post('/api/auth/wechat/web/bind-phone', data, {
    withCredentials: true,
    timeout: 90000,
  })
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

export type UpdateCompanyRequest = Partial<CreateCompanyRequest> & {
  onboardingChangeReason?: string
}

export function updateCompany(companyId: string, data: UpdateCompanyRequest): Promise<CompanyInfo> {
  return apiClient.put(`/api/auth/companies/${companyId}`, data)
}

/**
 * 上传营业执照照片，识别新建主体要填的字段。
 *
 * 只返回候选值：创建主体仍走 `createCompany`，用户在表单里核对后提交。
 * 识别慢（视觉模型），超时放宽到 120s，与税费种认定识别一致。
 */
export function recognizeCompanyLicense(file: File): Promise<LicenseOcrResult> {
  const body = new FormData()
  body.append('file', file)
  return apiClient.post('/api/auth/companies/license-ocr', body, {
    headers: { 'Content-Type': 'multipart/form-data' },
    timeout: 120000,
  })
}

/** 永久删除主体及其全部业务数据（不可恢复，需名称确认 + 短信二次认证）。 */
export function deleteCompany(
  companyId: string,
  confirmName: string,
  stepUpToken: string,
): Promise<{ deleted: boolean; companyId: string; companyName: string }> {
  return apiClient.delete(`/api/auth/companies/${companyId}`, {
    data: { confirmName, stepUpToken },
  })
}

export function requestStepUp(data: {
  action:
    | 'reopen_period'
    | 'confirm_legacy_migration'
    | 'adjust_opening_balance'
    | 'delete_company'
  phone: string
  code: string
  bizId?: string
  /** 仅 delete_company 使用：被删除目标主体（必须在当前账号名下）。 */
  companyId?: string
}): Promise<{ stepUpToken: string; expiresIn: number }> {
  return apiClient.post('/api/auth/step-up', data)
}
