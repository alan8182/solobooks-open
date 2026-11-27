/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

export type CompanyOnboardingType = 'new' | 'migration'

/** 路由 `?create=` 只认这两个建账场景，脏链接一律忽略，避免弹窗被开成错误场景。 */
export function parseCompanyCreateQuery(value: unknown): CompanyOnboardingType | null {
  return value === 'new' || value === 'migration' ? value : null
}

/**
 * 建账完成后的落点：代账转入去期初建账工作台，新成立公司优先前往税费种认定。
 *
 * 新成立公司无历史期初，第一步应同步税费种认定以接管征期与申报事项；
 * 已记账公司则需先导入科目余额表确保账目连续。
 */
export function companyCreateLandingPath(
  type: CompanyOnboardingType,
  _startDate?: string | null,
): string {
  if (type === 'migration') return '/mine/opening'
  return '/tax-registration'
}

/**
 * 创建成功弹窗里的账套状态说明：弹窗绝不能对「当前停在哪个账套」保持沉默。
 *
 * 2026-11-21 事故：主体管理直接新建正式账套后不自动切换，弹窗副标题渲染为空，
 * 用户带着旧账套的心智继续操作——结账向导停在旧账、税费种认定粘贴进了旧账。
 */
export function createSuccessSwitchNotice(
  switched: boolean,
  prevCompanyName: string | null,
): string {
  if (switched) return '当前账套已自动切换至该主体。'
  return `自动切换失败，当前仍停留在「${prevCompanyName || '原账套'}」，请先在主体列表手动切换再继续操作。`
}

export interface StartDateDerivationInput {
  onboardingType: CompanyOnboardingType
  incorporationDate: string | null
  startDate: string | null
  /** 当前启用日期是否仍是自动带入值；用户手改后置为 false，不再被成立日期覆盖。 */
  startDateAuto: boolean
}

/**
 * 新成立公司的启用日期默认等于公司成立日期。
 *
 * 两个日期在账务上并不等价：成立日期是营业执照起点（决定年中建账的损益累计
 * 起始月），启用日期是账套记账下限（决定期间下限与结账起点）。新成立公司通常
 * 同一天开始记账，所以默认带入；但成立早于实际开始记账的公司仍要能改，手改后
 * 就不再跟随成立日期变化。
 *
 * 返回 null 表示当前无需改动。
 */
export function deriveStartDateFromIncorporation(
  input: StartDateDerivationInput,
): { startDate: string; startDateAuto: true } | null {
  if (input.onboardingType !== 'new') return null
  const incorporation = String(input.incorporationDate || '').trim()
  if (!incorporation) return null
  const current = String(input.startDate || '').trim()
  // 用户已经手改过启用日期：保留用户的选择
  if (!input.startDateAuto && current) return null
  // 自动值已与成立日期一致：无需重复写入（避免无谓触发重渲染）
  if (input.startDateAuto && current === incorporation) return null
  return { startDate: incorporation, startDateAuto: true }
}
