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
 * 创建后是否切换当前账套：
 * - 演示账套不能继续停在演示里（样本数据会被当成真账）；
 * - 从建账引导进来的用户本意就是启用这个新主体。
 */
export function shouldSwitchAfterCreate(wasDemo: boolean, fromOnboarding: boolean): boolean {
  return wasDemo || fromOnboarding
}
