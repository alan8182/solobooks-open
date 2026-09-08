import apiClient from './client'

export function parseInvoiceRows(rows: Record<string, any>[]): Promise<{
  valid: Record<string, any>[]
  errors: { row: number; reason: string }[]
  total: number
  valid_count: number
}> {
  return apiClient.post('/api/invoice/parse', { rows })
}

export function importInvoices(rows: Record<string, any>[], classify = true): Promise<{
  imported: number
  total: number
  errors: { row: number; reason: string }[]
  entries: { id: string; title: string; success: boolean }[]
}> {
  return apiClient.post('/api/invoice/import', { rows, classify })
}

export function parseInvoiceColumns(columns: string[]): Promise<{
  mappings: { column: string; mapping: string | null }[]
}> {
  return apiClient.post('/api/invoice/parse-columns', { columns })
}
