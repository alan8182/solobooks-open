import apiClient from './client'
import type { VoucherGenerateResponse } from './types'

export function generateVoucher(entryIds: string[]): Promise<VoucherGenerateResponse> {
  return apiClient.post('/api/ledger/voucher', { entryIds })
}
