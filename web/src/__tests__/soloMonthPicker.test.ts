/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import SoloMonthPicker from '@/components/ui/SoloMonthPicker.vue'

describe('SoloMonthPicker', () => {
  it('renders with placeholder when no value is provided', () => {
    const wrapper = mount(SoloMonthPicker, {
      props: {
        placeholder: '请选择月份',
      },
    })
    expect(wrapper.text()).toContain('请选择月份')
  })

  it('renders current value when value is provided', () => {
    const wrapper = mount(SoloMonthPicker, {
      props: {
        value: '2026-10',
      },
    })
    expect(wrapper.text()).toContain('2026-10')
  })

  it('clears value when clear button is clicked', async () => {
    const wrapper = mount(SoloMonthPicker, {
      props: {
        value: '2026-10',
        clearable: true,
      },
    })

    const clearBtn = wrapper.find('.clear-btn')
    expect(clearBtn.exists()).toBe(true)
    await clearBtn.trigger('click')

    expect(wrapper.emitted('update:value')?.[0]).toEqual([null])
    expect(wrapper.emitted('update:formattedValue')?.[0]).toEqual([null])
    expect(wrapper.emitted('change')?.[0]).toEqual([null])
  })

  it('shows 12 months in the popover and switches years', async () => {
    const wrapper = mount(SoloMonthPicker, {
      props: {
        value: '2026-05',
        to: false,
      },
    })

    // Open popover
    await wrapper.find('.solo-month-picker').trigger('click')

    const yearTitle = wrapper.find('.year-title')
    expect(yearTitle.text()).toBe('2026 年')

    const monthCells = wrapper.findAll('.month-cell')
    expect(monthCells.length).toBe(12)
    expect(monthCells[4].classes()).toContain('is-selected') // 5月 (index 4)

    // Click next year
    const nextBtn = wrapper.find('button[aria-label="下一年"]')
    await nextBtn.trigger('click')
    expect(wrapper.find('.year-title').text()).toBe('2027 年')

    // Select month in 2027
    await monthCells[2].trigger('click') // 3月
    expect(wrapper.emitted('update:value')?.[0]).toEqual(['2027-03'])
  })

  it('supports selecting current month quickly', async () => {
    const wrapper = mount(SoloMonthPicker, {
      props: {
        to: false,
      },
    })

    await wrapper.find('.solo-month-picker').trigger('click')

    const quickBtn = wrapper.find('.quick-this-month')
    expect(quickBtn.exists()).toBe(true)

    await quickBtn.trigger('click')
    const now = new Date()
    const expected = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`
    expect(wrapper.emitted('update:value')?.[0]).toEqual([expected])
  })
})
