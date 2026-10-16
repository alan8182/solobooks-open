/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * Agent API：SSE 流式对话 + 待确认动作审批。
 * 流式通道使用原生 fetch（绕过 axios），auth header 手动附加。
 */
import apiClient from './client'

export interface AgentStreamEvent {
  type: 'meta' | 'token' | 'reasoning' | 'tool_call' | 'tool_result' | 'action' | 'final' | 'done' | 'error'
  content?: string
  message?: string
  step?: number
  name?: string
  ok?: boolean
  summary?: string
  arguments?: Record<string, unknown>
  action?: AgentActionItem
  tools?: string[]
  maxSteps?: number
}

export interface AgentActionItem {
  id: string
  actionType: string
  title: string
  payload: Record<string, unknown>
  evidence: Record<string, unknown>
  result: Record<string, unknown> | null
  status: 'pending' | 'approved' | 'rejected' | 'expired'
  createdAt: string | null
  resolvedAt: string | null
}

export function chatAgentStream(
  messages: { role: 'user' | 'assistant'; content: string }[],
  onEvent: (ev: AgentStreamEvent) => void,
  signal?: AbortSignal,
  conversationId?: string,
  enableThinking?: boolean,
): Promise<void> {
  const base = import.meta.env.VITE_API_BASE || ''
  const token = localStorage.getItem('auth_token') || ''
  return (async () => {
    const payload: {
      messages: typeof messages
      conversationId?: string
      enableThinking?: boolean
    } = { messages }
    if (conversationId) payload.conversationId = conversationId
    if (enableThinking != null) payload.enableThinking = enableThinking
    const resp = await fetch(`${base}/api/agent/chat`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify(payload),
      signal,
    })
    if (!resp.ok || !resp.body) {
      throw new Error(`Agent 请求失败（HTTP ${resp.status}）`)
    }
    const reader = resp.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      let sep: number
      while ((sep = buffer.indexOf('\n\n')) >= 0) {
        const rawEvent = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)
        const dataLine = rawEvent
          .split('\n')
          .find((line) => line.startsWith('data: '))
        if (!dataLine) continue
        try {
          onEvent(JSON.parse(dataLine.slice(6)) as AgentStreamEvent)
        } catch {
          // 忽略无法解析的残片
        }
      }
    }
  })()
}

// 注意：apiClient 的响应拦截器直接返回 response.data（见 client.ts），
// 因此这里拿到的已经是后端 JSON body，而不是 AxiosResponse。

export async function listAgentActions(status = 'pending'): Promise<AgentActionItem[]> {
  const body = (await apiClient.get('/api/agent/actions', { params: { status } })) as unknown as {
    actions: AgentActionItem[]
  }
  return body.actions
}

export interface AgentActionDecision {
  id: string
  status: string
  result?: Record<string, unknown>
}

export interface AgentApproveOptions {
  /** 批量清单中被用户取消勾选的行索引 */
  excludeIndexes?: number[]
  /** 用户修正的字段，按行索引（单笔动作索引用 "0"） */
  edits?: Record<string, { category?: string; title?: string; payer?: string }>
}

export async function approveAgentAction(
  id: string,
  opts: AgentApproveOptions = {},
): Promise<AgentActionDecision> {
  return (await apiClient.post(`/api/agent/actions/${id}/approve`, {
    excludeIndexes: opts.excludeIndexes?.length ? opts.excludeIndexes : undefined,
    edits: Object.keys(opts.edits || {}).length ? opts.edits : undefined,
  })) as unknown as AgentActionDecision
}

export async function rejectAgentAction(id: string, reason?: string): Promise<AgentActionDecision> {
  return (await apiClient.post(`/api/agent/actions/${id}/reject`, {
    reason: reason || '',
  })) as unknown as AgentActionDecision
}
