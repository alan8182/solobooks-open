export interface PostingStatusPresentation {
  label: string
  tagType: 'success' | 'info' | 'warning' | 'default'
}

/**
 * 凭证过账状态的展示映射。草稿是默认态（不渲染标签，保持列表干净），
 * 未知的未来状态一律返回 null 以免误导。
 */
export function postingStatusPresentation(voucherStatus?: string | null): PostingStatusPresentation | null {
  switch (voucherStatus) {
    case 'posted':
      return { label: '已过账', tagType: 'success' }
    case 'pending_review':
      return { label: '待复核', tagType: 'warning' }
    case 'reversed':
      return { label: '已冲销', tagType: 'default' }
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
