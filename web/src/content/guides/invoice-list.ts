// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks license v2.0.

import type { Guide } from './types'

/**
 * 电子税务局导出发票列表。
 *
 * 原文只有三句话 + 三张截图，关键信息全在图上的红色批注里：查询类型要按
 * 「开具发票 / 取得发票」分别导出、日期选上个月、最后点【导出全部】。这里
 * 把批注提成正文，避免用户只看正文漏掉「导两次」。
 */
export const invoiceListGuide: Guide = {
  id: 'invoice-list',
  title: '发票怎么从电子税务局导出',
  summary: '在电子税务局按「取得发票 / 开具发票」分别导出 Excel，上传到「导入发票」即可。',
  updatedAt: '2026-11',
  sourceUrl: 'https://docs.qq.com/doc/DTGtEWGVrd3doaUdD',
  // 全国统一规范电子税务局按省分设子域名，没有统一的登录地址；税务总局 12366
  // 平台的「网上办税」页是官方聚合入口，选省份后由它跳到 etax.<省>.chinatax.gov.cn
  // （2026-11 实测浙江/广东跳转正常）。
  entry: {
    label: '去电子税务局',
    url: 'https://12366.chinatax.gov.cn/bsfw/onlinetaxation/main',
    hint: '在「主体管理」填好注册地就能直达本省；没填时先在地图上选省份',
    // 主体档案里有注册地时直接打开本省电子税务局，不用再在地图上找一次
    companyRegion: true,
  },
  notice: '进项和销项要分开导：查询类型选「取得发票」导出别人开给你的票，选「开具发票」导出你开出去的票，各导一次。',
  steps: [
    {
      title: '首页进【发票业务】',
      text: '登录电子税务局，在首页「热门服务」里点【发票业务】。',
      images: [
        {
          src: '/guides/invoice-list/01.webp',
          alt: '电子税务局首页热门服务，红框标出发票业务入口',
          width: 1200,
          height: 648,
        },
      ],
    },
    {
      title: '点【发票查询统计】',
      text: '进入发票业务页后，在下方「用票业务办理」区域点【发票查询统计】。',
      images: [
        {
          src: '/guides/invoice-list/02.webp',
          alt: '发票业务页用票业务办理区域，红框标出发票查询统计入口',
          width: 1200,
          height: 659,
        },
      ],
    },
    {
      title: '选查询类型、日期，导出全部',
      text: '进入【全量发票查询】：查询类型选「取得发票」或「开具发票」，开票日期起止选要导入的月份，点【查询】，再点【导出全部】得到 Excel。换一个查询类型重复一次，两个文件分别上传到「导入发票」。',
      tip: '导出的是税局标准列（数电发票号码、销方名称、价税合计等），直接拖进导入发票窗口即可；同一张票重复上传会自动判重。',
      images: [
        {
          src: '/guides/invoice-list/03.webp',
          alt: '全量发票查询页：查询类型选择取得发票或开具发票、开票日期选上个月、红框标出导出全部按钮',
          width: 1200,
          height: 823,
        },
      ],
    },
  ],
}
