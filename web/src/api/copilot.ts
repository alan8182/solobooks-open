import apiClient from './client'
import type { CopilotChatRequest, CopilotChatResponse } from './types'

export function copilotChat(data: CopilotChatRequest): Promise<CopilotChatResponse> {
  return apiClient.post('/api/copilot/chat', data)
}
