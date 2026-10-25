/**
 * Copyright (c) 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import SoloFieldHelp from '@/components/ui/SoloFieldHelp.vue'
import { GLOSSARY } from '@/utils/glossary'

// NTooltip 的内容 hover 时才懒挂载（teleport 到 body），插槽文案进不了测试 DOM，
// 所以这里断言「有解释才渲染触发器」这个行为；文案正确性由 glossary.test.ts 覆盖。
describe('SoloFieldHelp', () => {
  it('renders the trigger for a known glossary term', () => {
    const wrapper = mount(SoloFieldHelp, { props: { term: '期初余额' } })

    expect(wrapper.find('.field-help-icon').exists()).toBe(true)
  })

  it('renders the trigger for an inline explanation', () => {
    const wrapper = mount(SoloFieldHelp, {
      props: { text: '货到了发票没到时，填合同或订单作为估价来源' },
    })

    expect(wrapper.find('.field-help-icon').exists()).toBe(true)
  })

  it('renders nothing when the term is unknown and no text is given', () => {
    const wrapper = mount(SoloFieldHelp, { props: { term: '不存在的词' } })

    expect(wrapper.find('.field-help-icon').exists()).toBe(false)
    expect(wrapper.text()).toBe('')
  })

  it('covers every glossary term that pages actually reference', () => {
    // 页面里用到的词条必须都能出 tooltip，否则 ? 图标都不渲染，等于没解释
    ;['期初余额', '销项', '进项'].forEach((term) => {
      const wrapper = mount(SoloFieldHelp, { props: { term } })
      expect(wrapper.find('.field-help-icon').exists(), `${term} 应能渲染`).toBe(true)
    })
  })

  it('never ships a tooltip the user cannot read in one breath', () => {
    GLOSSARY.forEach((entry) => {
      expect(entry.plain.length).toBeLessThanOrEqual(50)
    })
  })
})
