// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks license v2.0.

import type { Guide, GuideId } from './types'
import { bankStatementGuide } from './bank-statement'
import { iitGuide } from './iit'
import { taxRegistrationGuide } from './tax-registration'
import { wechatBillGuide } from './wechat-bill'

/**
 * 指引注册表：一处登记，页面按 id 取用。
 *
 * 加新指引只要做三件事：写数据文件、在这里登记、把截图放进 public/guides/<id>/。
 */
const GUIDES: Record<GuideId, Guide> = {
  'wechat-bill': wechatBillGuide,
  'bank-statement': bankStatementGuide,
  'tax-registration': taxRegistrationGuide,
  iit: iitGuide,
}

/** 需要独立页面（而不是抽屉）的指引：内容长、图多，用户是「专门来看」的 */
const PAGE_ONLY: ReadonlySet<GuideId> = new Set<GuideId>(['iit'])

export function getGuide(id: string): Guide | null {
  return (GUIDES as Record<string, Guide>)[id] ?? null
}

export function isPageGuide(id: GuideId): boolean {
  return PAGE_ONLY.has(id)
}

export const GUIDE_LIST: Guide[] = Object.values(GUIDES)

export type { Guide, GuideId, GuideImage, GuideStep } from './types'
