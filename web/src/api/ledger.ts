/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'
import type { VoucherGenerateResponse } from './types'

export function generateVoucher(entryIds: string[]): Promise<VoucherGenerateResponse> {
  return apiClient.post('/api/ledger/voucher', { entryIds })
}
