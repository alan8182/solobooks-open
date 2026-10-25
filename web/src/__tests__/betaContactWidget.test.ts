/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import BetaContactWidget from '@/components/business/BetaContactWidget.vue'

const wrappers: ReturnType<typeof mount>[] = []

afterEach(() => {
  wrappers.splice(0).forEach((wrapper) => wrapper.unmount())
  document.body.innerHTML = ''
})

describe('BetaContactWidget', () => {
  it('shows the beta entry and opens the WeChat QR dialog', async () => {
    const wrapper = mount(BetaContactWidget, { attachTo: document.body })
    wrappers.push(wrapper)

    expect(wrapper.text()).toContain('内测中')
    expect(wrapper.text()).toContain('加群反馈')

    await wrapper.find('.beta-contact-fab').trigger('click')
    await nextTick()

    expect(document.body.textContent).toContain('梭罗记账 · 内测群')
    expect(document.body.textContent).toContain('备注「记账」')
    const qr = document.body.querySelector('img[alt="微信内测群二维码"]') as HTMLImageElement | null
    expect(qr).not.toBeNull()
    expect(qr?.getAttribute('src')).toContain('wechat-beta-qr.png')
  })
})
