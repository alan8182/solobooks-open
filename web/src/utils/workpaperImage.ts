// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
// Licensed under the SoloBooks License v2.0.

/**
 * 审计底稿「保存为图片」工具。
 *
 * 纯端侧实现：html-to-image 以 foreignObject 将底稿 DOM 渲染为 PNG 后直接触发下载，
 * 不经过任何服务器——与体检页「免存即焚 / 零持久化」的产品承诺一致。
 * html-to-image 按需动态引入，避免拖累落地页首屏包体。
 */

const DEFAULT_PIXEL_RATIO = 2

export async function saveWorkpaperImage(
  node: HTMLElement,
  serial: string,
  pixelRatio = DEFAULT_PIXEL_RATIO,
): Promise<void> {
  const { toPng } = await import('html-to-image')
  const dataUrl = await toPng(node, {
    pixelRatio,
    backgroundColor: '#ffffff',
  })

  const link = document.createElement('a')
  link.download = `SoloBooks财税穿透底稿_${serial}.png`
  link.href = dataUrl
  link.click()
}
