import apiClient from './client'
import type { RdExpenseTypeOption, RdProject } from './types'

export interface RdProjectInput {
  code: string
  name: string
  manager?: string
  startDate: string
  endDate?: string
  status: 'draft' | 'active' | 'completed'
  initiationDocumentStatus: 'missing' | 'partial' | 'complete'
  notes?: string
}

export interface RdLedgerItem {
  date: string
  voucherNo?: string
  entryId: string
  summary: string
  projectCode: string
  projectName: string
  expenseTypeLabel: string
  accountCode: string
  amount: number
  allocationRate: number
  allocationMethod: string
  allocationBasis?: string
}

export interface RdUnallocatedItem {
  entryId: string
  date: string
  voucherNo?: string
  summary: string
  accountCode: string
  accountName: string
  amount: number
  reason: string
  status: 'draft' | 'pending_review' | 'posted'
}

export interface RdCorrectionAllocation {
  projectId: string
  expenseType: RdExpenseTypeOption['value']
  amount: number
  allocationMethod: 'direct' | 'timesheet' | 'usage' | 'area' | 'quantity' | 'other'
  allocationBasis?: string
  notes?: string
}

export interface RdReconciliationSummary {
  policyVersion: string
  policyLabel: string
  policyDisclaimer: string
  region?: string
  programBatch?: string
  ledgerTotal: number
  generalLedgerTotal: number
  difference: number
  isReconciled: boolean
  unallocatedCount: number
  policyStatus: 'suggested' | 'waiting_review' | 'not_included'
  projectSummary: Array<{ projectId: string; projectCode: string; projectName: string; amount: number }>
  expenseTypeSummary: Array<{ value: string; label: string; accountCode: string; amount: number }>
  evidenceIssues: Array<{
    entryId: string
    voucherNo?: string
    projectId: string
    projectCode: string
    type: string
    message: string
    amount: number
  }>
  evidenceDocuments: RdEvidenceDocument[]
  policyReviewItems: Array<{
    entryId: string
    voucherNo?: string
    projectCode: string
    expenseTypeLabel: string
    amount: number
    status: 'suggested' | 'waiting_review' | 'not_included'
    reason: string
  }>
}

export interface RdEvidenceDocument {
  id: string
  projectId: string
  entryId?: string
  documentType: string
  referenceNo?: string
  fileUrl?: string
  status: 'available' | 'pending_review' | 'missing'
  notes?: string
  createdAt: string
}

export interface RdEvidenceInput {
  projectId: string
  entryId?: string
  documentType: 'initiation' | 'invoice' | 'receipt' | 'contract' | 'timesheet' | 'material_issue' | 'usage_log' | 'acceptance' | 'other'
  referenceNo?: string
  fileUrl?: string
  status: 'available' | 'pending_review' | 'missing'
  notes?: string
}

export function getRdProjects(includeArchived = false): Promise<RdProject[]> {
  return apiClient.get('/api/rd/projects', { params: { include_archived: includeArchived } })
}

export function createRdProject(data: RdProjectInput): Promise<RdProject> {
  return apiClient.post('/api/rd/projects', data)
}

export function updateRdProject(id: string, data: Partial<Omit<RdProjectInput, 'status'>> & { status?: RdProject['status'] }): Promise<RdProject> {
  return apiClient.patch(`/api/rd/projects/${id}`, data)
}

export function getRdExpenseTypes(): Promise<{ items: RdExpenseTypeOption[] }> {
  return apiClient.get('/api/rd/expense-types')
}

export function getRdLedger(params: { startDate: string; endDate: string; projectId?: string }): Promise<{ items: RdLedgerItem[]; total: number }> {
  return apiClient.get('/api/rd/ledger', { params })
}

export function getRdUnallocated(): Promise<{ items: RdUnallocatedItem[]; count: number }> {
  return apiClient.get('/api/rd/unallocated')
}

export function correctRdUnallocated(
  entryId: string,
  data: { correctionDate: string; reason: string; allocations: RdCorrectionAllocation[] },
): Promise<{ originalEntryId: string; reversalEntryId?: string; correctedEntry: Record<string, unknown> }> {
  return apiClient.post(`/api/rd/unallocated/${entryId}/correct`, data, {
    headers: { 'Idempotency-Key': crypto.randomUUID() },
  })
}

export function getRdReconciliationSummary(params: {
  startDate: string
  endDate: string
  policyVersion?: string
  region?: string
  programBatch?: string
}): Promise<RdReconciliationSummary> {
  return apiClient.get('/api/rd/reconciliation-summary', { params })
}

export function getRdEvidenceTypes(): Promise<{ items: Array<{ value: string; label: string }> }> {
  return apiClient.get('/api/rd/evidence-types')
}

export function getRdEvidence(params: { projectId?: string; entryId?: string } = {}): Promise<{ items: RdEvidenceDocument[] }> {
  return apiClient.get('/api/rd/evidence', { params })
}

export function createRdEvidence(data: RdEvidenceInput): Promise<RdEvidenceDocument> {
  return apiClient.post('/api/rd/evidence', data)
}
