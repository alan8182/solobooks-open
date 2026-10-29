/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import type { ArchiveItem } from '@/api/types'

export interface EvidenceTip {
  tag: string
  tip: string
  level: 'info' | 'warning' | 'success'
}

/**
 * 依据账目分类、摘要、金额和单据形式推导证据链建议与税务避坑指南
 */
export function getEvidenceTip(item: Partial<ArchiveItem>): EvidenceTip | null {
  const text = `${item.category || ''} ${item.summary || ''}`.toLowerCase()
  const amt = Math.abs(Number(item.amount || 0))
  const isIncome = item.direction === 'income' || text.includes('收入') || text.includes('销售')

  // 1. 无单据优先提示（缺票与小额自查）
  if (item.docType === 'no_document') {
    if (amt <= 500) {
      return {
        tag: '小额凭据',
        tip: '单笔 500 元以下零星支出，税法允许使用收款凭条及支付流水入账并在所得税前扣除。',
        level: 'info',
      }
    }
    return {
      tag: '大额缺票',
      tip: '大额无票列支：若在汇算清缴前未取得合规发票，必须全额做企业所得税纳税调增（按25%补税）。',
      level: 'warning',
    }
  }

  // 2. 老板垫资与往来款
  if (text.includes('垫付') || text.includes('老板') || text.includes('借款') || text.includes('股东') || text.includes('往来')) {
    return {
      tag: '老板垫资',
      tip: '老板垫付归档要件：发票（公司抬头） + 个人支付流水 + 简要业务说明（说明用于何项业务，防范公私不分被追缴20%个税）；公司还款时附公转私还款回单。',
      level: 'warning',
    }
  }

  // 3. 员工费用报销
  if (text.includes('差旅') || text.includes('报销') || text.includes('招待') || text.includes('交通') || text.includes('福利') || text.includes('餐费')) {
    return {
      tag: '费用报销',
      tip: '报销归档要件：合规发票 + 报销审批单/费用清单 + 支付给员工的转账流水（证明真实出资与业务真实关联）。',
      level: 'info',
    }
  }

  // 4. 采购与无形技术服务外包（仅针对支出/成本，排除常规对外销售收入）
  if (!isIncome && amt >= 5000 && (text.includes('外包') || text.includes('咨询') || text.includes('技术服务') || text.includes('劳务') || text.includes('推广'))) {
    return {
      tag: '技术外包',
      tip: '无形服务税务重点核查真实性：务必备齐对公银行水单、服务合同协议与交付成果文档（防范虚开发票稽查风险）。',
      level: 'warning',
    }
  }

  return null
}
