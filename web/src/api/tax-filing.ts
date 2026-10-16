/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'

export function markDeadlineFiled(
  deadlineId: string,
  evidence?: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return apiClient.post(`/api/tax-filing/items/${deadlineId}/file`, { evidence }, {
    headers: { 'Content-Type': 'application/json' },
  })
}
