// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks license v2.0.

import type { Guide } from './types'

/**
 * 税费种认定表在哪找。
 *
 * 原文 97 个字、5 张截图，信息全在图里。产品内「从电子税务局同步」弹窗原本只写了
 * 路径文字（且少了「纳税人信息查询」这一跳），现在用这份指引补齐，并把弹窗文案对齐。
 */
export const taxRegistrationGuide: Guide = {
  id: 'tax-registration',
  title: '税费种认定表在哪找',
  summary: '电子税务局里五步走到「税费种认定信息」，整表复制粘贴就能同步。',
  updatedAt: '2026-11',
  sourceUrl: 'https://docs.qq.com/doc/DTEh3VXREWm5yZXRX',
  notice: '只要同步一次。暂时找不到也不影响记账，系统会先按小微常规周期推算，方便时再回来补。',
  steps: [
    {
      title: '首页进「税务数字账户」',
      text: '登录电子税务局，在首页「热门服务」里点【税务数字账户】。',
      images: [
        {
          src: '/guides/tax-registration/01.webp',
          alt: '电子税务局首页热门服务，红框标出税务数字账户入口',
          width: 1200,
          height: 607,
        },
      ],
    },
    {
      title: '进入「账户查询」',
      text: '在税务数字账户页的四个入口里点【账户查询】。',
      images: [
        {
          src: '/guides/tax-registration/02.webp',
          alt: '税务数字账户页，红框标出账户查询入口',
          width: 1200,
          height: 650,
        },
      ],
    },
    {
      title: '再进「纳税人信息查询」',
      text: '账户查询页里有一组查询入口，点【纳税人信息查询】。',
      images: [
        {
          src: '/guides/tax-registration/03.webp',
          alt: '账户查询页，红框标出纳税人信息查询入口',
          width: 1200,
          height: 611,
        },
      ],
    },
    {
      title: '切到「税费种认定信息」并全选复制',
      text: '页面顶部有一排标签，切到【税费种认定信息】，点【查询】让表格出来，然后框选整张表格复制（Ctrl+C）。',
      tip: '连表头一起复制没关系，粘贴时表头和空行会自动过滤。',
      images: [
        {
          src: '/guides/tax-registration/04.webp',
          alt: '纳税人信息查询页，红框标出税费种认定信息标签与被框选的认定表格',
          width: 1200,
          height: 967,
        },
      ],
    },
    {
      title: '回到梭罗记账粘贴导入',
      text: '在「从电子税务局同步认定信息」弹窗里选「复制粘贴表格」，把内容粘进文本框，核对数据预览后点【确认导入认定数据】。',
      tip: '不想复制也可以上传这一页的截图，系统会 OCR 识别；识别后记得核对小数位（3% 与 0.03 是两回事）。',
      images: [
        {
          src: '/guides/tax-registration/05.webp',
          alt: '梭罗记账导入弹窗，粘贴框内是税局表格内容，下方是解析后的数据预览',
          width: 1200,
          height: 696,
        },
      ],
    },
  ],
}
