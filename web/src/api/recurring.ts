import apiClient from './client'

export interface RecurringTemplate {
  id: string
  company_id: string
  name: string
  template_data: Record<string, any>
  frequency: string
  next_due_date: string
  is_active: number
  created_at: string
  updated_at: string
}

export interface TemplateCreateRequest {
  name: string
  template_data: Record<string, any>
  frequency: string
  next_due_date: string
}

export function getTemplates(): Promise<RecurringTemplate[]> {
  return apiClient.get('/api/recurring')
}

export function getTemplate(id: string): Promise<RecurringTemplate> {
  return apiClient.get(`/api/recurring/${id}`)
}

export function createTemplate(data: TemplateCreateRequest): Promise<RecurringTemplate> {
  return apiClient.post('/api/recurring', data)
}

export function updateTemplate(id: string, data: Partial<TemplateCreateRequest & { is_active: number }>): Promise<RecurringTemplate> {
  return apiClient.put(`/api/recurring/${id}`, data)
}

export function deleteTemplate(id: string): Promise<{ ok: boolean }> {
  return apiClient.delete(`/api/recurring/${id}`)
}

export function applyTemplate(id: string): Promise<{ entry: Record<string, any> }> {
  return apiClient.post(`/api/recurring/${id}/apply`)
}
