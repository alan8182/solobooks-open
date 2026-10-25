/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 行业 → 是否属于国家限制/禁止行业的默认判断。
 *
 * 依据：所属行业取自《国民经济行业分类》（GB/T 4754）大类，而「限制/禁止行业」
 * 依据《产业结构调整指导目录》限制类/淘汰类，两者并非一一对应。因此这里只做
 * **默认带出**：命中以下大类时默认按「是」处理（产品目标客户也不在这些行业），
 * 用户可在表单里手动改回「否」。未命中则默认「否」。
 *
 * 注意：这是产品默认值，不是法律认定；正式申报以税局口径为准。
 */

/** 限制/淘汰类业务较为集中的行业大类（GB/T 4754）。 */
export const RESTRICTED_INDUSTRIES: ReadonlySet<string> = new Set([
  // 采矿业：资源开采普遍受规模/工艺限制
  '煤炭开采和洗选业',
  '石油和天然气开采业',
  '黑色金属矿采选业',
  '有色金属矿采选业',
  '非金属矿采选业',
  '开采专业及辅助性活动',
  '其他采矿业',
  // 烟草：国家专营
  '烟草制品业',
  // 燃料加工 / 化工 / 化纤：限制类、淘汰类工艺集中
  '石油、煤炭及其他燃料加工业',
  '化学原料和化学制品制造业',
  '化学纤维制造业',
  // 冶炼压延：限制类产能集中
  '黑色金属冶炼和压延加工业',
  '有色金属冶炼和压延加工业',
])

export type RestrictedIndustryAnswer = 'yes' | 'no'

/** 按行业给出「是否限制/禁止行业」的默认答案；未知行业默认非限制。 */
export function restrictedIndustryFor(industry?: string | null): RestrictedIndustryAnswer {
  const name = (industry || '').trim()
  if (!name) return 'no'
  return RESTRICTED_INDUSTRIES.has(name) ? 'yes' : 'no'
}

/** 行业是否命中限制类默认清单。 */
export function isRestrictedIndustry(industry?: string | null): boolean {
  return restrictedIndustryFor(industry) === 'yes'
}

export interface IndustryGroupLike {
  label: string
  children: string[]
  hidden?: boolean
}

/**
 * 过滤行业分组：默认只保留未隐藏的组；「显示全部」时返回全部。
 *
 * 兜底：当前已选行业若落在被隐藏的组里，该组仍会被带出来——否则编辑旧主体时
 * 下拉会因找不到选项而显示空白，看起来像数据丢失。
 */
export function filterIndustryGroups<T extends IndustryGroupLike>(
  groups: T[],
  options: { showAll: boolean; selected?: string | null },
): T[] {
  const selected = (options.selected || '').trim()
  if (options.showAll) return groups
  return groups.filter(
    (group) => !group.hidden || (selected !== '' && group.children.includes(selected)),
  )
}
