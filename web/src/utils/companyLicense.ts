/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 营业执照识别结果 → 新建主体表单。
 *
 * 两条硬规则：
 * 1. 只填空字段。用户已经填了的内容（尤其是手改过的公司名、税号）绝不被覆盖，
 *    后台照抄的原文与用户输入冲突时，以用户为准。
 * 2. 认不准就不填。执照上的主体类型不在支持范围、住所认不出注册地时，
 *    留空并给出可读提示，让用户亲自选，而不是猜一个像的塞进去。
 *    这对应 PRODUCT.md「确认权属于用户」：AI 给候选，关键字段必须可核对。
 */
import type { LicenseOcrResult } from '@/api/types'
import { etaxRegionFromAddress, etaxRegionFromTaxId, etaxRegionLabel } from '@/utils/etaxPortal'

/** 新建主体支持的主体类型（个体户/个独当前内测中，不参与自动预填）。 */
export const SUPPORTED_COMPANY_TYPES = ['有限责任公司'] as const
export type SupportedCompanyType = (typeof SUPPORTED_COMPANY_TYPES)[number]

export interface EntityTypeMapping {
  /** 能映射到受支持类型时给出；否则空串 */
  type: SupportedCompanyType | ''
  /** 执照原文 */
  raw: string
  /** 需要用户处理的说明；映射成功且无歧义时为空 */
  note: string
}

/**
 * 执照「类型」原文 → 支持的主体类型。
 *
 * 按关键词从窄到宽匹配：`有限责任公司` 必须先于宽泛的 `公司`，
 * 「股份有限公司」不属于有限责任公司，不能因为带「公司」就归过去；
 * 个体工商户与个人独资企业当前在内测中，不自动填入，改为明确提示加微信申请内测。
 */
export function mapLicenseEntityType(raw?: string | null): EntityTypeMapping {
  const text = String(raw || '').trim()
  if (!text) return { type: '', raw: '', note: '' }
  if (/个体|个人独资/.test(text)) {
    return {
      type: '',
      raw: text,
      note: `执照上的主体类型是「${text}」，个体工商户与个人独资企业当前在内测中，暂仅支持有限责任公司；如需优先体验可加微信申请内测`,
    }
  }
  if (/有限责任公司/.test(text)) return { type: '有限责任公司', raw: text, note: '' }
  return {
    type: '',
    raw: text,
    note: `执照上的主体类型是「${text}」，当前只支持${SUPPORTED_COMPANY_TYPES.join(' / ')}，请手动选择`,
  }
}

/** 新建主体表单里与执照相关的字段；其余字段（行业、启用日期等）不参与预填。 */
export interface LicenseTargetForm {
  name: string
  taxId: string
  type: string
  incorporationDate: string | null
  region: string
}

export interface LicensePrefill<T> {
  /** 合并后的表单（未识别的字段保持原值） */
  form: T
  /** 本次自动填入的字段中文名 */
  filled: string[]
  /** 用户已填、因此没被覆盖的字段中文名 */
  kept: string[]
  /** 需要用户核对/补填的提示 */
  notices: string[]
}

/**
 * 把识别结果合并进表单，只填空字段。
 *
 * @param form 当前表单快照（不会被修改）
 * @param result 后端 normalize 过的识别结果
 */
export function applyLicensePrefill<T extends LicenseTargetForm>(
  form: T,
  result: LicenseOcrResult,
): LicensePrefill<T> {
  const next: T = { ...form }
  const filled: string[] = []
  const kept: string[] = []
  const notices: string[] = [...(result.warnings || [])]

  function assign<K extends keyof T>(key: K, value: string, label: string): boolean {
    if (!value) return false
    const current = String(form[key] ?? '').trim()
    if (current) {
      // 与识别结果相同就不必提示「已填/已保留」：用户看到的表单本来就是这个值
      if (current !== value) kept.push(label)
      return false
    }
    next[key] = value as T[K]
    filled.push(label)
    return true
  }

  assign('name', result.name, '公司名称')
  assign('taxId', result.taxId, '纳税人识别号')

  const entity = mapLicenseEntityType(result.entityType)
  if (entity.note) notices.push(entity.note)
  if (assign('type', entity.type, '主体类型') && entity.raw !== entity.type) {
    // 执照原文与下拉选项不是逐字一致（「有限责任公司(自然人独资)」），
    // 按类型归并是系统判断，要让用户看见依据
    notices.push(`已按执照「${entity.raw}」填入主体类型「${entity.type}」，请核对`)
  }

  assign('incorporationDate', result.incorporationDate, '公司成立日期')

  const regionKey = etaxRegionFromAddress(result.address)
  const taxRegionKey = regionKey ? '' : etaxRegionFromTaxId(result.taxId)
  const finalRegionKey = regionKey || taxRegionKey
  if (finalRegionKey) {
    if (assign('region', finalRegionKey, '注册地')) {
      const [province, city] = finalRegionKey.split('|')
      const label = etaxRegionLabel(province, city)
      // 注册地是从「住所 / 税号」推出来的，不是执照上的独立字段，必须让用户确认
      if (label) {
        notices.push(
          taxRegionKey
            ? `没从执照住所认出注册地，已按统一社会信用代码推断为「${label}」，请核对`
            : `已按执照住所推断注册地为「${label}」，请核对`,
        )
      }
    }
  } else if (result.address || result.taxId) {
    // 住所和税号都认不出：注册地是必填，必须明确告诉用户手动选
    notices.push(`没从执照住所和统一社会信用代码认出注册地，请手动选择`)
  }

  if (result.confidence > 0 && result.confidence < 0.6) {
    notices.push('识别把握较低，请对照执照逐项核对')
  }

  return { form: next, filled, kept, notices }
}
