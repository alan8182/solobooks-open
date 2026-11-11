// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks license v2.0.

/**
 * 站内操作指引的结构化内容。
 *
 * 为什么不用 iframe 嵌腾讯文档：腾讯文档页会带进整套编辑器外壳（菜单/工具栏/
 * 登录按钮/状态栏），且移动端 webview 不可用、无法埋点、加载不可控。所以文档只
 * 作为「内容源头 + 最新版兜底」，正文与截图抽成这里的结构化数据，由产品内组件
 * 按自己的设计语言渲染。
 *
 * 维护约定：
 * - 改腾讯文档后，重抽截图并更新这里（流程见 docs/operation-guides.md）；
 * - `updatedAt` 必须跟着改，界面会显示「截图更新于 YYYY-MM」，避免用户照旧图操作；
 * - 截图一律放 `public/guides/<guideId>/`，WebP，宽度 1200。
 */
export type GuideId = 'wechat-bill' | 'bank-statement' | 'tax-registration' | 'iit'

export interface GuideImage {
  /** public 下的绝对路径，例如 /guides/wechat-bill/01.webp */
  src: string
  alt: string
  /** 原始宽高，用于占位避免加载时跳动 */
  width: number
  height: number
}

export interface GuideStep {
  title: string
  /** 正文。说清「点哪里、填什么」，不写会计术语 */
  text?: string
  images?: GuideImage[]
  /** 这一步最容易踩的坑，单独一行灰底提示 */
  tip?: string
}

export interface Guide {
  id: GuideId
  title: string
  /** 一句话说明这份指引解决什么问题，出现在抽屉/页面副标题 */
  summary: string
  /** 截图核对月份，如 '2026-11' */
  updatedAt: string
  /** 腾讯文档最新版。内容仍在文档里维护，这里只是兜底入口 */
  sourceUrl: string
  /** 顶部提醒，放合规要点或「什么时候可以跳过」 */
  notice?: string
  steps: GuideStep[]
}
