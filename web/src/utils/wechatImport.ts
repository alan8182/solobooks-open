// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0.

/** 微信账单单条导入结果（/api/wechat-bill/import 返回的 results 元素）。 */
export interface WechatImportResult {
  status: string
  title?: string
  amount?: number
  error?: string
}

export interface WechatImportFailureGroup {
  /** 归一化后的失败原因（日期替换为占位符，同因可合并）。 */
  message: string
  count: number
  /** 拦截原因是「记账日期早于账套启用日期」，可引导用户去主体管理调整启用日期。 */
  isStartDateIssue: boolean
}

const START_DATE_ERROR_MARKER = '早于账套启用日期'

/** 剥掉具体日期，让「同一拦截原因、不同交易日期」的失败能合并为一组。 */
function normalizeError(message: string): string {
  return message.replace(/\d{4}-\d{2}-\d{2}/g, 'YYYY-MM-DD')
}

/**
 * 把导入结果按失败原因聚合：同因合并计数，供结账向导展示失败明细面板。
 * 微信账单的失败通常是同一拦截原因批量命中（如启用日期校验），逐条罗列没有信息量。
 */
export function summarizeWechatImportFailures(results: WechatImportResult[]): WechatImportFailureGroup[] {
  const groups: WechatImportFailureGroup[] = []
  const byMessage = new Map<string, WechatImportFailureGroup>()
  for (const item of results || []) {
    if (item?.status !== 'failed' || !item.error) continue
    const key = normalizeError(item.error)
    let group = byMessage.get(key)
    if (!group) {
      group = {
        message: key,
        count: 0,
        isStartDateIssue: item.error.includes(START_DATE_ERROR_MARKER),
      }
      byMessage.set(key, group)
      groups.push(group)
    }
    group.count += 1
  }
  return groups
}
