/**
 * Copyright (c) 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { existsSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { GUIDE_LIST, getGuide, isPageGuide, type GuideId } from '@/content/guides'

// vitest 的 import.meta.url 不是 file: 协议（jsdom 环境下会抛 scheme 错误），
// 用 cwd 定位 public —— 测试统一从 web/ 目录启动。
const PUBLIC_DIR = join(process.cwd(), 'public')

/**
 * 指引是「用户照着做」的内容，错一处就会把人带到错误的菜单里。
 * 这里守的是结构完整性与资源可达性，文案对错仍需人工核对腾讯文档。
 */
describe('操作指引内容', () => {
  it('每份指引都有标题、摘要、更新时间和来源链接', () => {
    GUIDE_LIST.forEach((guide) => {
      expect(guide.title.length, `${guide.id} 缺标题`).toBeGreaterThan(0)
      expect(guide.summary.length, `${guide.id} 缺摘要`).toBeGreaterThan(0)
      expect(guide.updatedAt, `${guide.id} 缺更新时间`).toMatch(/^\d{4}-\d{2}$/)
      expect(guide.sourceUrl, `${guide.id} 缺腾讯文档来源`).toMatch(/^https:\/\/docs\.qq\.com\//)
    })
  })

  it('注册表按 id 取得到，未知 id 返回 null', () => {
    const ids: GuideId[] = ['wechat-bill', 'bank-statement', 'tax-registration', 'iit']
    ids.forEach((id) => {
      expect(getGuide(id)?.id, `${id} 未登记`).toBe(id)
    })
    expect(getGuide('not-exist')).toBeNull()
  })

  it('每一步都有标题，且至少有正文或截图之一', () => {
    GUIDE_LIST.forEach((guide) => {
      expect(guide.steps.length, `${guide.id} 没有步骤`).toBeGreaterThan(0)
      guide.steps.forEach((step, index) => {
        expect(step.title.length, `${guide.id} 第 ${index + 1} 步缺标题`).toBeGreaterThan(0)
        const hasContent = Boolean(step.text) || Boolean(step.images?.length)
        expect(hasContent, `${guide.id} 第 ${index + 1} 步既没正文也没图`).toBe(true)
      })
    })
  })

  it('截图文件都真实存在，且落在自己的目录下', () => {
    GUIDE_LIST.forEach((guide) => {
      guide.steps.forEach((step) => {
        ;(step.images ?? []).forEach((image) => {
          expect(
            image.src.startsWith(`/guides/${guide.id}/`),
            `${image.src} 应放在 /guides/${guide.id}/ 下`,
          ).toBe(true)
          const filePath = join(PUBLIC_DIR, image.src)
          expect(existsSync(filePath), `${image.src} 文件不存在`).toBe(true)
          // 声明宽高是为了占位不跳动；缺失会让抽屉在图片加载时抖一下
          expect(image.width).toBeGreaterThan(0)
          expect(image.height).toBeGreaterThan(0)
          expect(image.alt.length, `${image.src} 缺替代文字`).toBeGreaterThan(0)
        })
      })
    })
  })

  it('只有长文指引走独立页，其余走就地抽屉', () => {
    expect(isPageGuide('iit')).toBe(true)
    expect(isPageGuide('wechat-bill')).toBe(false)
    expect(isPageGuide('bank-statement')).toBe(false)
    expect(isPageGuide('tax-registration')).toBe(false)
  })
})
