// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
// Licensed under the SoloBooks License v2.0.

import { beforeEach, describe, expect, it, vi } from 'vitest'

const toPngMock = vi.fn()

vi.mock('html-to-image', () => ({
  toPng: (...args: unknown[]) => toPngMock(...args),
}))

import { saveWorkpaperImage } from '@/utils/workpaperImage'

describe('saveWorkpaperImage 底稿保存为图片', () => {
  beforeEach(() => {
    toPngMock.mockReset().mockResolvedValue('data:image/png;base64,AAA')
  })

  it('以 2x 白底渲染 PNG，并以底稿编号命名触发端侧下载', async () => {
    const clicked: HTMLAnchorElement[] = []
    const realCreate = document.createElement.bind(document)
    const createSpy = vi
      .spyOn(document, 'createElement')
      .mockImplementation(((tag: string) => {
        const el = realCreate(tag)
        if (tag === 'a') {
          ;(el as HTMLAnchorElement).click = () => clicked.push(el as HTMLAnchorElement)
        }
        return el
      }) as typeof document.createElement)

    try {
      const fakeNode = realCreate('div')
      await saveWorkpaperImage(fakeNode, 'SOLO-AUDIT-202611-2651')

      // 渲染参数：底稿节点 + 2x 清晰度 + 白底（与底稿纸张风格一致）
      expect(toPngMock).toHaveBeenCalledTimes(1)
      const [node, opts] = toPngMock.mock.calls[0] as [HTMLElement, Record<string, unknown>]
      expect(node).toBe(fakeNode)
      expect(opts.pixelRatio).toBe(2)
      expect(opts.backgroundColor).toBe('#ffffff')

      // 下载锚点：文件名含底稿编号，href 为生成的 dataUrl
      expect(clicked).toHaveLength(1)
      expect(clicked[0].download).toBe('SoloBooks财税穿透底稿_SOLO-AUDIT-202611-2651.png')
      expect(clicked[0].href).toBe('data:image/png;base64,AAA')
    } finally {
      createSpy.mockRestore()
    }
  })
})
