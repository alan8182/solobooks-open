// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
// Licensed under the SoloBooks License v2.0.

import type { Guide } from './types'

/**
 * 支付宝商家对账单导出。
 *
 * 原文档是一张标注图 + 一行流程，信息以截图为准；文案按截图序号写步骤，
 * 与微信账单指引同一结构（抽屉形态）。
 */
export const alipayBillGuide: Guide = {
  id: 'alipay-bill',
  title: '支付宝商家对账单怎么导出',
  summary: '从支付宝商家平台按月导出交易账单，解压后上传「业务明细.csv」，自动拆分商家实收与手续费。',
  updatedAt: '2026-11',
  sourceUrl: 'https://docs.qq.com/doc/DTGNUaVVTZWxUQnd0',
  entry: {
    label: '去支付宝商家后台',
    url: 'https://b.alipay.com/home.htm',
    hint: '电脑浏览器登录后进「对账中心 → 账单管理 → 账单下载」',
  },
  notice: '用支付宝商家收款才需要导入；不用支付宝收款，这一步直接跳过。',
  steps: [
    {
      title: '登录商家平台，进入「对账中心」',
      text: '打开支付宝商家平台（b.alipay.com），顶部导航点【对账中心】（截图①）。',
      tip: '从支付宝 App 里进不去这个页面，要用电脑浏览器登录 b.alipay.com。',
    },
    {
      title: '「账单管理」下点【账单下载】，页签选「交易账单」',
      text: '左侧菜单「账单管理」分组下点【账单下载】（截图②），顶部页签选【交易账单】（截图③）。',
      images: [
        {
          src: '/guides/alipay-bill/01.webp',
          alt: '支付宝商家平台对账中心页，标注了顶部【对账中心】、左侧【账单下载】、页签【交易账单】、月账单选项与月份格子',
          width: 1200,
          height: 777,
        },
      ],
    },
    {
      title: '账单选择选「月账单」，点对应月份下载',
      text: '账单选择选【月账单】（截图④），在下方年历里点要记账的那个月（例如 10 月）开始下载，得到一个 zip 压缩包。',
      tip: '月账单当月 1 日 9:00 生成，次日才有；日账单次日 9:00 生成。只导一个月的账单即可，跨月账单会在结账时被拦。',
    },
    {
      title: '解压后上传「业务明细.csv」',
      text: '解压 zip，把「业务明细.csv」（不要传「业务明细(汇总).csv」）拖进结账向导「导入平台账单」的上传区。同一份账单重复上传会自动跳过，不会重复记账。',
      tip: '解压出的文件以支付宝账号开头（如 2088…02250156_20261118_业务明细.csv），认准带「业务明细」、不带「（汇总）」的那份。',
      images: [
        {
          src: '/guides/alipay-bill/02.webp',
          alt: '解压后的文件列表，红框标注「业务明细.csv」，旁边是被排除的「业务明细(汇总).csv」',
          width: 1200,
          height: 209,
        },
      ],
    },
  ],
}
