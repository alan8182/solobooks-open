/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 期初工作台「税务期初与申报衔接备忘」的 localStorage 草稿兜底。
 * 服务端（tax_memory 随期初配置持久化）是权威数据，这里只兜「尚未保存过服务端」的草稿。
 *
 * 键名必须带账套维度（对齐 workspaceView / guideState / copilotChat 的
 * `solo:<feature>:${companyId || 'default'}` 惯例），否则同账号切换账套会把
 * A 账套的可弥补亏损 / 已预缴税额带进 B 账套的表单。
 *
 * 注意：旧版全局键 `solo_tax_opening_memory_${year}` **不做迁移**——
 * 税额串账套的危害远大于重填一次；且用户一旦保存过期初凭证，服务端即权威。
 * 旧键留作孤儿、永远不再读取。
 */

export interface TaxOpeningMemory {
  priorYearLoss: number
  prepaidCitTax: number
  migrationNote: string
}

const STORAGE_PREFIX = 'solo:tax-opening-memory:'

export function taxOpeningMemoryStorageKey(companyId: string | null, year: number): string {
  return `${STORAGE_PREFIX}${companyId || 'default'}:${year}`
}

export function loadTaxOpeningMemory(companyId: string | null, year: number): TaxOpeningMemory | null {
  try {
    const raw = localStorage.getItem(taxOpeningMemoryStorageKey(companyId, year))
    if (!raw) return null
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return null
    const obj = parsed as Partial<TaxOpeningMemory>
    return {
      priorYearLoss: Number(obj.priorYearLoss) || 0,
      prepaidCitTax: Number(obj.prepaidCitTax) || 0,
      migrationNote: String(obj.migrationNote || ''),
    }
  } catch {
    return null
  }
}

export function saveTaxOpeningMemory(
  companyId: string | null,
  year: number,
  memory: TaxOpeningMemory,
): void {
  try {
    localStorage.setItem(taxOpeningMemoryStorageKey(companyId, year), JSON.stringify(memory))
  } catch {
    // 隐私模式或配额失败不阻断期初工作台
  }
}
