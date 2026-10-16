/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import axios from 'axios'

// 金额字段以字符串形式传输（Decimal 序列化），响应拦截器按此正则把命中字段的数值字符串转为 Number。
// 不变量：凡是前端要做数值运算（求和/比较）的响应字段，命名必须命中此正则；绕过 apiClient 的裸 fetch 不做转换。
const MONEY_KEY = /(?:amount|income|expense|profit|cost|tax|vat|assets|liabilities|equity|deductible|inflow|outflow|net|debit|credit|balance|value|sales|rate|percent)$/i

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

apiClient.interceptors.response.use(
  (response) => normalizeMoneyForDisplay(response.data),
  async (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('auth_token')
      localStorage.removeItem('auth_state')
      window.location.href = '/login'
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

export default apiClient
