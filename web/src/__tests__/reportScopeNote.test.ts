/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import ReportScopeNote from '@/components/business/ReportScopeNote.vue'

describe('ReportScopeNote', () => {
  it('renders the non-statutory statement boundary', () => {
    const wrapper = mount(ReportScopeNote)
    const text = wrapper.find('[data-testid="report-scope-note"]').text()
    expect(text).toContain('不作为法定财务报表')
    expect(text).toContain('不含税口径')
    expect(text).toContain('纳税调增')
  })

  it('supports a per-report scope prefix', () => {
    const wrapper = mount(ReportScopeNote, {
      props: { scope: '本表按已关账期间编制' },
    })
    expect(wrapper.find('[data-testid="report-scope-note"]').text()).toContain('本表按已关账期间编制')
  })
})
