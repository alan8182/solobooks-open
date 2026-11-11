/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 期初导入「旧科目 → 系统科目」映射的按账套持久化。
 * 键名遵循仓库惯例（workspaceView / guideState / copilotChat）：
 * `solo:<feature>:${companyId || 'default'}`。
 * 代账场景同一客户软件重复导出时，映射自动套用免重配；
 * 键按来源科目编码，同一软件导出的编码稳定。
 */

export type OpeningMapping = Record<string, string>

export function openingMappingStorageKey(companyId: string | null): string {
  return `solo:opening-mapping:${companyId || 'default'}`
}

export function loadOpeningMapping(companyId: string | null): OpeningMapping {
  try {
    const raw = localStorage.getItem(openingMappingStorageKey(companyId))
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return {}
    const out: OpeningMapping = {}
    for (const [k, v] of Object.entries(parsed as Record<string, unknown>)) {
      if (k && typeof v === 'string' && v) out[k] = v
    }
    return out
  } catch {
    return {}
  }
}

/**
 * 从预填映射里挑出「上次已确认过」的那部分。
 * 只有这些能在导入时直接套用：按名称猜出来的建议值仍需用户点确认
 * （PRODUCT.md 确认权），已确认过的再问一遍就是纯骚扰。
 */
export function pickConfirmedMappings(
  prefilled: OpeningMapping,
  confirmed: OpeningMapping,
): OpeningMapping {
  const out: OpeningMapping = {}
  for (const [key, target] of Object.entries(prefilled)) {
    if (confirmed[key] === target) out[key] = target
  }
  return out
}

export function saveOpeningMapping(companyId: string | null, mapping: OpeningMapping): void {
  try {
    localStorage.setItem(openingMappingStorageKey(companyId), JSON.stringify(mapping))
  } catch {
    // 隐私模式或配额失败不阻断导入流程
  }
}
