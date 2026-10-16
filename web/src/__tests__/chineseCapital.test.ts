/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { amountToChineseCapital } from '@/utils/format'

describe('amountToChineseCapital', () => {
  it('凭证合计样例（对照目标版式）', () => {
    expect(amountToChineseCapital(19622.55)).toBe('壹万玖仟陆佰贰拾贰圆伍角伍分')
  })

  it('整数与角分', () => {
    expect(amountToChineseCapital(100)).toBe('壹佰圆整')
    expect(amountToChineseCapital(10.5)).toBe('壹拾圆伍角')
    expect(amountToChineseCapital(105.3)).toBe('壹佰零伍圆叁角')
    expect(amountToChineseCapital(0.05)).toBe('零圆伍分')
    expect(amountToChineseCapital(0.55)).toBe('零圆伍角伍分')
  })

  it('跨组补零与大数', () => {
    expect(amountToChineseCapital(10001)).toBe('壹万零壹圆整')
    expect(amountToChineseCapital(1005)).toBe('壹仟零伍圆整')
    expect(amountToChineseCapital(1000000)).toBe('壹佰万圆整')
    expect(amountToChineseCapital(0)).toBe('零圆整')
  })

  it('四舍五入到分', () => {
    expect(amountToChineseCapital(19.999)).toBe('贰拾圆整')
  })
})
