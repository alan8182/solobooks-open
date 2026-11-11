// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks license v2.0.

import type { Guide } from './types'

/**
 * 银行交易单下载。
 *
 * 原文的「专属邮箱」截图拍的是我们自己产品的旧界面，已经过时 —— 这里换成
 * 指向产品内真实入口（邮箱免传收单）的说明，不再搬那张图。
 */
export const bankStatementGuide: Guide = {
  id: 'bank-statement',
  title: '银行流水怎么下载',
  summary: '手机银行 App 找到交易明细，直接导出到梭罗记账的专属邮箱，免下载免上传。',
  updatedAt: '2026-11',
  sourceUrl: 'https://docs.qq.com/doc/DTExZTFVGdE9nS2hV',
  notice: '各家银行叫法不同：交易明细、收支明细、账户实时查询都指同一个页面，一般在首页或「我的账户」里。',
  steps: [
    {
      title: '找到能导出的流水页面',
      text: '在手机银行里找到流水明细页，页面上通常有下载或导出的图标。找不到就直接问银行客户经理要「可导出的交易明细」入口。',
    },
    {
      title: '导出到专属邮箱',
      text: '点下载/导出 → 日期范围选上个月 → 分享方式选「邮箱」→ 接收邮箱填梭罗记账给这个账户的专属邮箱 → 收支类型选「全部」→ 格式选 Excel → 确认发送。',
      tip: '专属邮箱在「月度结账 → 导入单据 → 邮箱免传收单」里，点一下就能复制；填过一次后银行 App 会记住。',
      images: [
        {
          src: '/guides/bank-statement/01.webp',
          alt: '招商银行企业 App 收支明细导出流程：进入收支明细、点下载、填写日期范围与接收邮箱',
          width: 1200,
          height: 756,
        },
      ],
    },
    {
      title: '回到本页等附件进来',
      text: '邮件到达后，附件会自动出现在收单列表里，确认即可导入。也可以直接用电脑网银导出 CSV / Excel，在本页上传。',
      tip: '电脑网银导出的文件同样支持，系统会自动识别日期、金额、余额列；重复流水会自动跳过。',
    },
  ],
}
