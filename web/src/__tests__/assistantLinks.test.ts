/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { linkifyClosingWizardRoutes, localClosingWizardPath } from '@/utils/assistantLinks'

describe('assistant closing-wizard links', () => {
  it('turns a bare closing route into a clickable markdown link', () => {
    const rendered = linkifyClosingWizardRoutes(
      '点击或访问： /closing-wizard?month=2026-05',
    )
    expect(rendered).toContain(
      '[/closing-wizard?month=2026-05](/closing-wizard?month=2026-05&step=3)',
    )
  })

  it('does not rewrite an existing full or markdown link', () => {
    const full = 'https://solobooks.cn/closing-wizard?month=2026-05&step=3'
    const markdown = `[打开结账向导](${full})`
    expect(linkifyClosingWizardRoutes(full)).toBe(full)
    expect(linkifyClosingWizardRoutes(markdown)).toBe(markdown)
  })

  it('navigates only valid closing routes on the current product origin', () => {
    expect(
      localClosingWizardPath('https://solobooks.cn/closing-wizard?month=2026-05&step=2'),
    ).toBe('/closing-wizard?month=2026-05&step=3')
    expect(
      localClosingWizardPath('/closing-wizard?month=2026-13'),
    ).toBeNull()
    expect(
      localClosingWizardPath('https://example.com/other?month=2026-05'),
    ).toBeNull()
  })
})
