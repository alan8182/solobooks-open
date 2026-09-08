import apiClient from './client'

export function parseWechatBill(file: File): Promise<{
  valid: Record<string, any>[]
  errors: { row: number; reason: string }[]
  summary: Record<string, any>
  total: number
}> {
  const form = new FormData()
  form.append('file', file)
  return apiClient.post('/api/wechat-bill/parse', form, {
    headers: { 'Content-Type': 'multipart/form-data' },
  })
}

export function importWechatBill(rows: Record<string, any>[]): Promise<{
  imported: number
  failed: number
  ignored_duplicates: number
  results: { status: string; title: string; amount?: number; error?: string }[]
}> {
  return apiClient.post('/api/wechat-bill/import', { rows })
}
