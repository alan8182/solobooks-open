import axios from 'axios'

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
  headers: { 'Content-Type': 'application/json' },
})

apiClient.interceptors.request.use((config) => {
  const token = localStorage.getItem('auth_token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  const method = (config.method || 'get').toLowerCase()
  if (['post', 'put', 'patch', 'delete'].includes(method) && !config.headers.has('Idempotency-Key')) {
    config.headers.set('Idempotency-Key', crypto.randomUUID())
  }
  return config
})

apiClient.interceptors.response.use(
  (response) => normalizeMoneyForDisplay(response.data),
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('auth_token')
      localStorage.removeItem('auth_state')
      window.location.href = '/login'
    }
    return Promise.reject(error)
  },
)

export default apiClient
