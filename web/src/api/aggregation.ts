/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// 通用归集 API 层。三条不可让步的纪律（违反即白屏/错数）：
//
// 1. 调用铁律：client.ts 的响应拦截器已 return response.data，所以
//    `const data = await apiClient.get(...)`。禁止 `const { data } = await ...`
//    ——那会解构出 undefined，且 TS 不报错（any 泄漏），是最隐蔽的白屏来源。
//
// 2. 形状归一化：后端 4 种返回形状不一致（裸数组 / {items} / 单对象 / PUT report-line-rules 返回数组）。
//    本文件把形状差异**全部吃掉**，对外一律暴露 `T[]` 或单个 T。`{items}` 在全项目
//    只在本文件内部出现一次；组件层写 `res.items` 会直接编译报错——这是刻意的。
//
// 3. 金额归一化只做一处：见 compareReportLineRules。组件层禁止 Number(x.amount) 兜底。

import apiClient from './client'
import type {
  AggregationAllocation,
  AggregationAllocationInput,
  AggregationBasis,
  AggregationDimension,
  AggregationDimensionInput,
  AggregationDimensionUpdate,
  AggregationLineState,
  AggregationMember,
  AggregationMemberInput,
  AggregationMemberUpdate,
  AggregationMeta,
  AggregationPendingItem,
  AggregationPendingStateParam,
  AggregationReportItem,
  AggregationRule,
  AggregationRuleInput,
  LabelOption,
  ReportKind,
  ReportLineDiff,
  ReportLinePatternInput,
  ReportLineRule,
  ReportLineRuleInput,
  ReportLineSign,
} from './types'

// ===== §3.2 形状归一化类型别名 =====
// 别名按「形状」命名而非按端点命名：形状写错时名字就会读不通，编译器也会拦。

/** ① 裸数组（后端直接返回 list） */
export type AggregationDimensionList = AggregationDimension[]
export type AggregationMemberList = AggregationMember[]
export type ReportLineRuleList = ReportLineRule[]

/** ② { items: [...] } —— 仅在下方 fetchRules / replaceRules 内部出现，不对外暴露 */
type AggregationRulesEnvelope = { items?: AggregationRule[] }

/** ③ 带分页与合计的待归集清单（唯一保留 items 的对外类型，因为它还有 total/totals） */
export interface AggregationPendingResponse {
  items: AggregationPendingItem[]
  total: number
  limit: number
  offset: number
  basis: AggregationBasis
  state: AggregationPendingStateParam
  /** ⚠️ 是全量合计（后端在分页前累加），不是当页合计 */
  totals: { lineAmount: number; allocatedAmount: number; remainingAmount: number }
}

/** 归集报表。注意未归集口径叫 unassignedAmount，与清单的 remainingAmount 不同名 */
export interface AggregationReportResponse {
  dimensionId: string
  dimensionCode: string
  dimensionName: string
  basis: AggregationBasis
  start: string
  end: string
  scopePrefixes: string[]
  items: AggregationReportItem[]
  totals: {
    lineAmount: number
    allocatedAmount: number
    unassignedAmount: number
    balanced: boolean
    allocatedLineCount: number
    partialLineCount: number
    unallocatedLineCount: number
    lineCount: number
  }
}

/** PUT/DELETE allocations 的回执（两者形状相同） */
export interface AllocationWriteResult {
  journalLineId: string
  dimensionId: string
  lineAmount: number
  allocatedAmount: number
  remainingAmount: number
  state: AggregationLineState
  allocations: AggregationAllocation[]
}

/** POST /apply-rules 的回执：没有 matched 字段（别按直觉写） */
export interface ApplyRulesResult {
  allocated: number
  skipped: number
  unmatched: number
  rules: number
}

export interface SwitchReadinessResult {
  report: ReportKind
  ready: boolean
  requiredCleanPeriods: number
  currentCleanStreak: number
  checkedRuns: number
  /** 审计 after 原样 dict（report/period/clean/skipped/compared/diffCount），不是任意对象 */
  latest: {
    report: string
    period: string
    clean: boolean
    skipped: boolean
    compared: number
    diffCount: number
  } | null
}

export interface ReportLineCompareResult {
  report: ReportKind
  skipped: boolean
  clean: boolean
  diffs: ReportLineDiff[]
  compared: number
  /** ⚠️ skipped=true 的早退分支（diff_report_lines）不返回此字段，故可选 */
  ruleCount?: number
  period: string
  periodType: 'month' | 'quarter'
  comparedAt: string
  switchReadiness: SwitchReadinessResult
}

// ===== §3.4 参数类型 =====

export type { LabelOption, AggregationMeta }

/** ⚠️ scopePrefixes 在 GET /pending 是**逗号分隔字符串**，在 POST /apply-rules 是**字符串数组**。
 *  两者不同形，不要抽公共参数构造函数。 */
export interface AggregationPendingParams {
  /** YYYY-MM */
  start?: string
  /** YYYY-MM */
  end?: string
  /**
   * 视为必传（产品侧铁律）：dimensionId 为空时后端 aggregation_service.py:829-831
   * 会跳过 allocated 行，用户看到的清单语义不直观，且前端不提供「不选维度」这个选项。
   */
  dimensionId: string
  /** 逗号分隔字符串，如 '5,6'。留空则后端用 DEFAULT_SCOPE_PREFIXES('5','6') */
  scopePrefixes?: string
  basis?: AggregationBasis
  state?: AggregationPendingStateParam
  /** ≤ 500（后端 Query(le=500)） */
  limit?: number
  offset?: number
}

export interface ReplaceLineAllocationsBody {
  dimensionId: string
  allocations: AggregationAllocationInput[]
  complete: boolean
}

export interface ApplyRulesBody {
  dimensionId: string
  /** YYYY-MM，缺省走后端 period_start_for() */
  start: string
  end: string
  basis?: AggregationBasis
  /** ⚠️ 数组，与 GET /pending 的逗号字符串不同形 */
  scopePrefixes?: string[]
}

export interface AggregationReportParams {
  dimensionId: string
  /** 必填，缺省直接 422 */
  start: string
  end: string
  basis?: AggregationBasis
  /** 逗号分隔字符串 */
  scopePrefixes?: string
}

export interface CompareReportLineRulesBody {
  report: ReportKind
  /** YYYY-MM（月报）或 YYYY-Qn（季报） */
  period: string
  periodType?: 'month' | 'quarter'
}

export type { ReportLineSign, ReportLinePatternInput }

// ===== 内部工具 =====

/**
 * 把后端 money_text() 字符串转 number。
 * 只服务于 compare.diffs 的三个字段——它们不命中 client.ts 的 MONEY_KEY
 * （正则要求 amount/rate/... 结尾），会以 "120.00" 字符串落地。
 * 归一化**只在这里做一次**，组件层拿到的必定是 number。
 */
function toNum(value: unknown): number {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : 0
}

/** compare 的原始响应：三个金额字段此时仍是 string */
type RawCompareResult = Omit<
  ReportLineCompareResult,
  'diffs' | 'ruleCount'
> & {
  diffs?: Array<Omit<ReportLineDiff, 'baseline' | 'configured' | 'delta'> & {
    baseline: unknown
    configured: unknown
    delta: unknown
  }>
  ruleCount?: number
}

// ===== meta =====

/** GET /api/aggregation/meta —— 静态枚举，整个 store 生命周期只拉一次 */
export function getAggregationMeta(): Promise<AggregationMeta> {
  return apiClient.get('/api/aggregation/meta')
}

// ===== 维度 =====

/**
 * GET /api/aggregation/dimensions —— 后端返回**裸数组**。
 * ⚠️ query 名是 snake_case 的 `include_archived`（routers/aggregation.py:77 未设 alias），
 * 不是 includeArchived。
 */
export function listDimensions(includeArchived = false): Promise<AggregationDimension[]> {
  return apiClient.get('/api/aggregation/dimensions', {
    params: { include_archived: includeArchived },
  })
}

/** POST /api/aggregation/dimensions —— 单对象 */
export function createDimension(body: AggregationDimensionInput): Promise<AggregationDimension> {
  return apiClient.post('/api/aggregation/dimensions', body)
}

/** PATCH /api/aggregation/dimensions/{id} —— 单对象；归档走 status:'archived' */
export function updateDimension(
  id: string,
  body: AggregationDimensionUpdate,
): Promise<AggregationDimension> {
  return apiClient.patch(`/api/aggregation/dimensions/${id}`, body)
}

// ===== 归集对象（成员）=====

/** GET .../members —— 裸数组；query 名同样是 include_archived */
export function listMembers(
  dimensionId: string,
  includeArchived = false,
): Promise<AggregationMember[]> {
  return apiClient.get(`/api/aggregation/dimensions/${dimensionId}/members`, {
    params: { include_archived: includeArchived },
  })
}

/** POST .../members —— 单对象 */
export function createMember(
  dimensionId: string,
  body: AggregationMemberInput,
): Promise<AggregationMember> {
  return apiClient.post(`/api/aggregation/dimensions/${dimensionId}/members`, body)
}

/** PATCH /api/aggregation/members/{id} —— 注意路径没有 /dimensions 前缀 */
export function updateMember(
  memberId: string,
  body: AggregationMemberUpdate,
): Promise<AggregationMember> {
  return apiClient.patch(`/api/aggregation/members/${memberId}`, body)
}

// ===== 规则 =====

/**
 * GET .../rules —— 后端返回 `{items:[...]}`，这里解包成**数组**对外暴露。
 * 全项目 `{items}` 只在本函数与 replaceAggregationRules 内出现。
 */
export async function listAggregationRules(dimensionId: string): Promise<AggregationRule[]> {
  const res = (await apiClient.get(
    `/api/aggregation/dimensions/${dimensionId}/rules`,
  )) as AggregationRulesEnvelope
  return res?.items ?? []
}

/**
 * PUT .../rules —— 整批替换（不是逐条 upsert）。
 * 后端同样返回 `{items:[...]}`，照样解包成数组。
 * ⚠️ 整批语义：漏传的规则会被删除，不是「不动」。
 */
export async function replaceAggregationRules(
  dimensionId: string,
  rules: AggregationRuleInput[],
): Promise<AggregationRule[]> {
  const res = (await apiClient.put(`/api/aggregation/dimensions/${dimensionId}/rules`, {
    rules,
  })) as AggregationRulesEnvelope
  return res?.items ?? []
}

// ===== 待归集清单 =====

/**
 * GET /api/aggregation/pending —— 唯一保留 `{items,...}` 外形的端点，
 * 因为它同时携带 total / limit / offset / totals，分页与合计条都要用。
 */
export function listPendingLines(
  params: AggregationPendingParams,
): Promise<AggregationPendingResponse> {
  return apiClient.get('/api/aggregation/pending', { params })
}

// ===== 分配写入 =====

/**
 * PUT /api/aggregation/lines/{journalLineId}/allocations
 * ⚠️ **整行替换**：不传全部分配会把已有归集抹掉且不可撤销（只有审计日志）。
 * 打开编辑抽屉时必须先用 item.allocations 回填。
 * ⚠️ 入参字段名是 allocationMethod / allocationBasis，不是 method / basis。
 */
export function replaceLineAllocations(
  journalLineId: string,
  body: ReplaceLineAllocationsBody,
): Promise<AllocationWriteResult> {
  return apiClient.put(`/api/aggregation/lines/${journalLineId}/allocations`, body)
}

/**
 * DELETE /api/aggregation/lines/{journalLineId}/allocations
 * ⚠️ dimensionId 是**必填 query**，漏了会 422。
 * ⚠️ 该行在此维度下无归集记录时后端抛 404 NOT_FOUND，
 *    所以「清空」按钮只在 item.allocations.length > 0 时显示。
 */
export function clearLineAllocations(
  journalLineId: string,
  dimensionId: string,
): Promise<AllocationWriteResult> {
  return apiClient.delete(`/api/aggregation/lines/${journalLineId}/allocations`, {
    params: { dimensionId },
  })
}

// ===== 执行规则 =====

/**
 * POST /api/aggregation/apply-rules
 * ⚠️ scopePrefixes 是**字符串数组**，与 GET /pending 的逗号字符串不同形。
 * 约定：已有人工分配的行整行跳过，绝不覆盖。
 */
export function applyRules(body: ApplyRulesBody): Promise<ApplyRulesResult> {
  return apiClient.post('/api/aggregation/apply-rules', body)
}

// ===== 归集报表 =====

/**
 * GET /api/aggregation/report —— start/end **必填**（后端 Query 无 default），漏传 422。
 * 口径只取 posted 凭证，与待归集清单（含草稿）数字不同是刻意的。
 */
export function getAggregationReport(
  params: AggregationReportParams,
): Promise<AggregationReportResponse> {
  return apiClient.get('/api/aggregation/report', { params })
}

// ===== 报表口径规则（P2）=====

/**
 * GET /api/aggregation/report-line-rules —— **裸数组**。
 * ⚠️ 后端此端点没有 status/includeArchived 过滤参数，前端只能拉全量后本地过滤。
 */
export function listReportLineRules(report?: ReportKind): Promise<ReportLineRule[]> {
  return apiClient.get('/api/aggregation/report-line-rules', {
    params: report ? { report } : undefined,
  })
}

/**
 * PUT /api/aggregation/report-line-rules —— **也是裸数组**（replace_rules 末 return after，
 * after 是 list）。既不是单对象也不是 {items}，所以这里没有解包动作。
 * ⚠️ 整批替换：后端先 delete 全部再重建，校验失败整批回滚。
 */
export function replaceReportLineRules(
  report: ReportKind,
  rules: ReportLineRuleInput[],
): Promise<ReportLineRule[]> {
  return apiClient.put('/api/aggregation/report-line-rules', { report, rules })
}

/**
 * DELETE /api/aggregation/report-line-rules/{id} —— 返回单对象。
 * ⚠️ 是**软删**（status='archived'），UI 文案用「归档」不用「删除」。
 */
export function archiveReportLineRule(ruleId: string): Promise<ReportLineRule> {
  return apiClient.delete(`/api/aggregation/report-line-rules/${ruleId}`)
}

/**
 * POST /api/aggregation/report-line-rules/compare —— 双轨对比。
 *
 * ⚠️⚠️ 全模块唯一的金额归一化点：diffs[].baseline/configured/delta 由后端 money_text()
 * 输出为字符串，且字段名不命中 client.ts 的 MONEY_KEY 正则（该正则要求
 * amount|income|...|rate|percent 等结尾），因此会以 "120.00" 原样落到前端。
 * 若不在此 Number()，UI 会拼出 ¥0120.00，用户据此判断口径错误 → 做出错误财务决策。
 *
 * 归一化只做这一次，组件层禁止再写 Number(diff.delta)。
 */
export async function compareReportLineRules(
  body: CompareReportLineRulesBody,
): Promise<ReportLineCompareResult> {
  const raw = (await apiClient.post(
    '/api/aggregation/report-line-rules/compare',
    body,
  )) as RawCompareResult
  return {
    ...raw,
    diffs: (raw.diffs ?? []).map((diff) => ({
      ...diff,
      baseline: toNum(diff.baseline),
      configured: toNum(diff.configured),
      delta: toNum(diff.delta),
    })),
  }
}

/**
 * GET /api/aggregation/report-line-rules/switch-readiness —— 只回答「够不够格切」，
 * 不执行切换（后端也没有切换端点）。所以前端**不提供**任何「切换口径」按钮。
 */
export function getSwitchReadiness(report: ReportKind): Promise<SwitchReadinessResult> {
  return apiClient.get('/api/aggregation/report-line-rules/switch-readiness', {
    params: { report },
  })
}
