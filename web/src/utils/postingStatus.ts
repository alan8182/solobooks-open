/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

export interface PostingStatusPresentation {
  label: string
  tagType: 'success' | 'info' | 'warning' | 'default'
  /**
   * 列表里的着色口径。过账成功是"正常态"，正常态不该上色——
   * 一屏 10 行都标绿，等于用颜色重复说 10 遍"没事"，真正待复核的那行就淹了。
   */
  tone: 'success' | 'warning' | 'danger' | 'info' | 'neutral'
  /** 是否需要用户处理（只有它才值得用彩色） */
  needsAttention: boolean
}

/**
 * 凭证过账状态的展示映射。草稿是默认态（不渲染标签，保持列表干净），
 * 未知的未来状态一律返回 null 以免误导。
 */
export function postingStatusPresentation(voucherStatus?: string | null): PostingStatusPresentation | null {
  switch (voucherStatus) {
    case 'posted':
      return { label: '已过账', tagType: 'success', tone: 'neutral', needsAttention: false }
    case 'pending_review':
      return { label: '待复核', tagType: 'warning', tone: 'warning', needsAttention: true }
    case 'reversed':
      return { label: '已冲销', tagType: 'default', tone: 'neutral', needsAttention: false }
    default:
      return null
  }
}

/**
 * 筛选用：voucherStatus 缺省视为草稿（未明确过账即未过账）。
 */
export function normalizedPostingStatus(voucherStatus?: string | null): string {
  return voucherStatus || 'draft'
}
