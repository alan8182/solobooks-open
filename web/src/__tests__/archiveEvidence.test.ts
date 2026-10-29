/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { getEvidenceTip } from '@/utils/archiveEvidence'

describe('archiveEvidence helper', () => {
  it('识别老板垫资与往来款，提示个人付款流水与业务说明防范20%个税', () => {
    const tip = getEvidenceTip({
      category: '其他应付款-老板垫付',
      summary: '老板垫付购买办公电脑',
      amount: '5999.00',
      docType: 'has_invoice',
    })
    expect(tip).not.toBeNull()
    expect(tip?.tag).toBe('老板垫资')
    expect(tip?.level).toBe('warning')
    expect(tip?.tip).toContain('防范公私不分被追缴20%个税')
    expect(tip?.tip).toContain('个人支付流水')
  })

  it('识别员工费用报销（差旅/餐饮/办公），提示报销单与员工打款水单', () => {
    const tip = getEvidenceTip({
      category: '管理费用-差旅费',
      summary: '张三出差深圳差旅费报销',
      amount: '1280.00',
      docType: 'has_invoice',
    })
    expect(tip).not.toBeNull()
    expect(tip?.tag).toBe('费用报销')
    expect(tip?.level).toBe('info')
    expect(tip?.tip).toContain('报销审批单')
    expect(tip?.tip).toContain('支付给员工的转账流水')
  })

  it('识别大额技术外包与无形咨询服务，提示合同水单与交付成果防范虚开', () => {
    const tip = getEvidenceTip({
      category: '主营业务成本-技术外包',
      summary: '小程序前端UI外包开发',
      amount: '15000.00',
      docType: 'has_invoice',
    })
    expect(tip).not.toBeNull()
    expect(tip?.tag).toBe('技术外包')
    expect(tip?.level).toBe('warning')
    expect(tip?.tip).toContain('交付成果文档')
    expect(tip?.tip).toContain('对公银行水单')
  })

  it('识别单笔500元以下小额无票开支，提示可用个人收款收据扣除', () => {
    const tip = getEvidenceTip({
      category: '管理费用-办公费',
      summary: '门市换锁修门',
      amount: '160.00',
      docType: 'no_document',
    })
    expect(tip).not.toBeNull()
    expect(tip?.tag).toBe('小额凭据')
    expect(tip?.level).toBe('info')
    expect(tip?.tip).toContain('单笔 500 元以下')
  })

  it('识别大额无单据支出，提示企业所得税汇算需纳税调增风险', () => {
    const tip = getEvidenceTip({
      category: '管理费用-其他',
      summary: '展会临时场地租赁',
      amount: '8000.00',
      docType: 'no_document',
    })
    expect(tip).not.toBeNull()
    expect(tip?.tag).toBe('大额缺票')
    expect(tip?.level).toBe('warning')
    expect(tip?.tip).toContain('纳税调增')
  })

  it('常规合规有票单据无额外敏感特征时返回 null', () => {
    const tip = getEvidenceTip({
      category: '主营业务收入',
      summary: '销售软件授权收入',
      amount: '20000.00',
      docType: 'has_invoice',
    })
    expect(tip).toBeNull()
  })
})
