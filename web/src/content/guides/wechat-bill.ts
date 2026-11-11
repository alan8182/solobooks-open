// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks license v2.0.

import type { Guide } from './types'

/**
 * 微信支付商户账单导出。
 *
 * 原文只有三句话、一张标注图，信息密度低 —— 所以这一份不单独做页，用抽屉就够。
 */
export const wechatBillGuide: Guide = {
  id: 'wechat-bill',
  title: '微信支付商户账单怎么导出',
  summary: '从微信支付商户平台按月导出交易账单，上传后自动拆分收款与手续费。',
  updatedAt: '2026-11',
  sourceUrl: 'https://docs.qq.com/doc/DTE91YVZzd2hjYkJo',
  notice: '用微信支付收款才需要导入；不用微信收款，这一步直接跳过。',
  steps: [
    {
      title: '登录商户平台，进入「交易账单」',
      text: '打开微信支付商户平台，顶部点【交易中心】，左侧「交易管理」下点【交易账单】。',
    },
    {
      title: '选账单类型和时间范围',
      text: '对账单类型选「所有交易账单」，时间范围选要记账的那个月（例如 10-01 至 10-31）。',
      tip: '只导一个月的账单即可；跨月的账单会在结账时被拦下来。',
    },
    {
      title: '打包下载后上传',
      text: '点【打包下载】，把下载到的 CSV 文件拖进「导入微信/平台账单」的上传区。',
      tip: '同一份账单重复上传会自动跳过，不会重复记账。',
      images: [
        {
          src: '/guides/wechat-bill/01.webp',
          alt: '微信支付商户平台交易账单页，标注了交易中心、交易账单、时间范围与打包下载按钮',
          width: 1200,
          height: 734,
        },
      ],
    },
  ],
}
