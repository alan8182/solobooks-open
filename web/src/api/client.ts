/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import axios, { type AxiosRequestConfig } from 'axios'
import { createDiscreteApi } from 'naive-ui'

// 金额字段以字符串形式传输（Decimal 序列化），响应拦截器按此正则把命中字段的数值字符串转为 Number。
// 不变量：凡是前端要做数值运算（求和/比较）的响应字段，命名必须命中此正则；绕过 apiClient 的裸 fetch 不做转换。
//
// 工资表字段（salary_records / employees）是 snake_case，早先只有 personal_tax 命中 tax$，
// 其余 base_salary / bonus / allowance / deduction / personal_social_security /
// company_social_security / net_salary 全部漏网，保持字符串传入 NInputNumber。
// naive-ui 的 format() 见 typeof value !== 'number' 直接返回空串，草稿工资表 5 列金额
// 因此永远显示 placeholder「请输入」——用户以为没填，手工覆盖掉系统算好的社保。
// 故补 _salary / _base / _security / bonus / allowance / deduction 六个后缀；
// 二级校验（纯数字）保证 social_security 这类枚举值不会被误转。
const MONEY_KEY = /(?:amount|income|expense|profit|cost|tax|vat|assets|liabilities|equity|deductible|inflow|outflow|net|debit|credit|balance|value|sales|rate|percent|amortization|_salary|_base|_security|_total|bonus|allowance|deduction)$/i

function normalizeMoneyForDisplay(value: unknown, key = ''): any {
  // Binary downloads (XLS/PDF/images) must bypass JSON display normalization.
  if (
    (typeof Blob !== 'undefined' && value instanceof Blob) ||
    (typeof ArrayBuffer !== 'undefined' && value instanceof ArrayBuffer)
  ) {
    return value
  }
  if (Array.isArray(value)) return value.map((item) => normalizeMoneyForDisplay(item))
  if (value && typeof value === 'object') {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>).map(([childKey, child]) => [
        childKey,
        normalizeMoneyForDisplay(child, childKey),
      ]),
    )
  }
  if (typeof value === 'string' && MONEY_KEY.test(key) && /^-?\d+(?:\.\d+)?$/.test(value)) {
    return Number(value)
  }
  return value
}

const apiClient = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || '',
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
    'X-Client-Core': 'solobooks-web-jzxc',
  },
})

// crypto.randomUUID 仅在安全上下文（HTTPS / localhost）可用；
// HTTP 部署（如 http://ip:8080）下为 undefined，需回落到 getRandomValues 手工构造 UUID。
export function makeIdempotencyKey(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  const bytes = crypto.getRandomValues(new Uint8Array(16))
  bytes[6] = (bytes[6] & 0x0f) | 0x40
  bytes[8] = (bytes[8] & 0x3f) | 0x80
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`
}

apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('auth_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  const method = (config.method || 'get').toLowerCase()
  if (['post', 'put', 'patch', 'delete'].includes(method) && !config.headers.has('Idempotency-Key')) {
    config.headers.set('Idempotency-Key', makeIdempotencyKey())
  }
  return config
})

/**
 * 401 是否真的代表「登录失效」。
 *
 * 后端把 MCP 子应用 mount 在根路径当兜底，未命中的 /api/* 曾经会返回
 * `{"jsonrpc":...}` 形式的 401。那种 401 表达的是「这个接口不存在 / 需要 PAT」，
 * 不是登录过期——照旧清 token 会把用户直接踢回登录页（新增路由但后端未重启时必现）。
 */
export function shouldClearSession(status: number | undefined, body: unknown): boolean {
  if (status !== 401) return false
  if (body && typeof body === 'object' && 'jsonrpc' in (body as Record<string, unknown>)) {
    return false
  }
  return true
}

/**
 * 402 = AI 用量超出当前套餐额度（core/plans.py 额度未设限时后端不会返回 402）。
 * 这里用 naive-ui 离散 API 全局弹一次提示——调用方各自的错误处理仍会照常执行，
 * 对没有接 reportErrorToast 的调用点（如工具型页面）这是唯一可见出口。
 */
let quotaMessageApi: ReturnType<typeof createDiscreteApi>['message'] | null = null
let quotaToastAt = 0

function notifyQuotaExceeded(): void {
  const now = Date.now()
  if (now - quotaToastAt < 3000) return // 批量调用（如导入逐行分类）只弹一次
  quotaToastAt = now
  if (!quotaMessageApi) {
    quotaMessageApi = createDiscreteApi(['message']).message
  }
  quotaMessageApi.error('AI 额度已用完，请升级套餐或等待下期恢复', { duration: 5000 })
}

apiClient.interceptors.response.use(
  (response) => normalizeMoneyForDisplay(response.data),
  async (error) => {
    if (shouldClearSession(error.response?.status, error.response?.data)) {
      localStorage.removeItem('auth_token')
      localStorage.removeItem('auth_state')
      window.location.href = '/login'
    }
    if (error.response?.status === 402) {
      notifyQuotaExceeded()
    }
    // 文件下载类请求（responseType: 'blob'）失败时响应体是 Blob，
    // 还原成 JSON 让调用方的 err.response.data.detail 能取到具体原因。
    const data = error.response?.data
    if (typeof Blob !== 'undefined' && data instanceof Blob) {
      try {
        error.response.data = JSON.parse(await data.text())
      } catch {
        // 二进制或非 JSON 错误体，保持原样
      }
    }
    return Promise.reject(error)
  },
)

// 拦截器已把响应解包为 data 本体（见 normalizeMoneyForDisplay）。
// axios 自带泛型 get<T> 的返回类型是 Promise<AxiosResponse<T>>，与运行时相反：
// 调用方按 AxiosResponse 解构 data 会拿到 undefined 且编译不报错（最隐蔽的 any 泄漏）。
// 这里用 axios 官方的第二泛型参数把返回类型钉成 data 本体；默认 any 保持
// api 层「裸调用 + 显式 : Promise<T> 标注」的既有写法可编译，泛型调用获得真类型。
const raw = apiClient
export default {
  get: <T = any, R = T>(url: string, config?: AxiosRequestConfig) => raw.get<T, R>(url, config),
  post: <T = any, R = T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    raw.post<T, R>(url, data, config),
  put: <T = any, R = T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    raw.put<T, R>(url, data, config),
  patch: <T = any, R = T>(url: string, data?: unknown, config?: AxiosRequestConfig) =>
    raw.patch<T, R>(url, data, config),
  delete: <T = any, R = T>(url: string, config?: AxiosRequestConfig) => raw.delete<T, R>(url, config),
}
