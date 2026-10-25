/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import apiClient from './client'
import type { CopilotChatRequest, CopilotChatResponse } from './types'

export function copilotChat(data: CopilotChatRequest): Promise<CopilotChatResponse> {
  return apiClient.post('/api/copilot/chat', data)
}
