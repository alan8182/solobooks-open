/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 平台净额结算凭证的展示口径。
 *
 * 结算凭证由银行对账按所选账目实际命中的待结算科目生成，来源与
 * `reconciliationType` 一一对应（微信 1012001 / 支付宝 1012002 / 历史 1012）。
 * 页面与 composable 共用这一份标签，禁止各自按「微信」写死——支付宝结算
 * 显示成微信属于对用户撒谎。
 */
export interface SettlementTypeLabel {
  /** 凭证标签短文案，如「支付宝结算凭证」 */
  tag: string
  /** 完整口径，如「支付宝净额结算」 */
  full: string
}

export const SETTLEMENT_TYPE_LABELS: Record<string, SettlementTypeLabel> = {
  wechat_settlement: { tag: '微信结算凭证', full: '微信净额结算' },
  alipay_settlement: { tag: '支付宝结算凭证', full: '支付宝净额结算' },
  platform_settlement: { tag: '平台结算凭证', full: '平台净额结算' },
}

export function isSettlementType(type?: string | null): boolean {
  return Boolean(type && SETTLEMENT_TYPE_LABELS[type])
}

export function settlementTypeLabel(
  type: string | null | undefined,
  part: 'tag' | 'full',
): string {
  const label = type ? SETTLEMENT_TYPE_LABELS[type] : undefined
  return label ? label[part] : (type || '')
}

/** 取消对账关联时结算凭证会被后端一并删除，提示里必须点名真实平台。 */
export function settlementCancelMessage(type?: string | null): string {
  return `对账关联已取消；原账目仍保留在账上，${settlementTypeLabel(type, 'tag') || '结算凭证'}已一并删除`
}
