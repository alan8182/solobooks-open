/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'

const mockFetchOrder = vi.fn()

vi.mock('@/api/billing', () => ({
  fetchBillingOrder: (...args: unknown[]) => mockFetchOrder(...args),
}))

vi.mock('naive-ui', async (importOriginal) => {
  const actual = await importOriginal<typeof import('naive-ui')>()
  return {
    ...actual,
    useMessage: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
  }
})

vi.mock('qrcode', () => ({
  default: {
    toDataURL: vi.fn().mockResolvedValue('data:image/png;base64,FAKEQR'),
  },
}))

import WechatPayQrModal from '@/pages/mine/WechatPayQrModal.vue'

const baseProps = {
  show: true,
  orderNo: 'SB261119WECHAT01',
  codeUrl: 'weixin://wxpay/bizpayurl?pr=TEST01',
  amount: '598.00',
  planName: '云端标准版',
  expiresAt: new Date(Date.now() + 30 * 60 * 1000).toISOString(),
}

function mountedModal(props: Record<string, unknown> = {}) {
  return mount(WechatPayQrModal, {
    props: { ...baseProps, ...props },
    // NModal 默认 teleport 到 body：stub 掉让 wrapper.text() 可断言
    global: { stubs: { teleport: true } },
  })
}

function orderPayload(status: string) {
  return {
    orderNo: baseProps.orderNo,
    planCode: 'standard',
    subject: '云端标准版（年付）',
    amount: '598.00',
    status,
    channel: 'wechat' as const,
  }
}

describe('WechatPayQrModal（微信扫码支付弹窗）', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    mockFetchOrder.mockReset()
    mockFetchOrder.mockResolvedValue(orderPayload('created'))
  })

  afterEach(() => {
    vi.useRealTimers()
    document.body.innerHTML = ''
  })

  it('renders the weixin code_url as a QR image and keeps polling while scanning', async () => {
    const { default: QRCode } = await import('qrcode')
    const wrapper = mountedModal()
    await flushPromises()

    const img = wrapper.find('img.wxpay-qr')
    expect(img.exists()).toBe(true)
    expect(img.attributes('src')).toBe('data:image/png;base64,FAKEQR')
    expect(wrapper.text()).toContain('请使用微信扫码支付')
    expect(wrapper.text()).toContain('二维码有效期剩余')
    expect(QRCode.toDataURL).toHaveBeenCalledWith(
      baseProps.codeUrl,
      expect.objectContaining({ width: 224 }),
    )

    // 轮询进行中（2.5s 间隔），订单仍 created：保持扫码态
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()
    expect(mockFetchOrder).toHaveBeenCalledWith(baseProps.orderNo)
    expect(wrapper.find('img.wxpay-qr').exists()).toBe(true)
    wrapper.unmount()
  })

  it('emits success exactly once when order becomes activated and stops polling', async () => {
    mockFetchOrder.mockResolvedValue(orderPayload('activated'))
    const wrapper = mountedModal()
    await flushPromises()

    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()

    expect(wrapper.emitted('success')).toHaveLength(1)
    expect(wrapper.text()).toContain('支付成功，套餐已生效')

    // 终态后停止轮询：时间再推进也不新增请求
    const callsAfterPaid = mockFetchOrder.mock.calls.length
    await vi.advanceTimersByTimeAsync(10000)
    await flushPromises()
    expect(mockFetchOrder.mock.calls.length).toBe(callsAfterPaid)
    // 成功只提示一次（防重复 accept）
    expect(wrapper.emitted('success')).toHaveLength(1)
    wrapper.unmount()
  })

  it('shows failed state when order is closed', async () => {
    mockFetchOrder.mockResolvedValue(orderPayload('closed'))
    const wrapper = mountedModal()
    await flushPromises()

    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()

    expect(wrapper.emitted('success')).toBeUndefined()
    expect(wrapper.text()).toContain('订单未完成支付；如已付款请联系客服核实')
    wrapper.unmount()
  })

  it('triggers an immediate gateway-backed query from the manual check button', async () => {
    const wrapper = mountedModal()
    await flushPromises()
    const initialCalls = mockFetchOrder.mock.calls.length

    const checkBtn = wrapper.findAll('button').find((b) => b.text().includes('我已支付'))
    expect(checkBtn).toBeDefined()
    await checkBtn!.trigger('click')
    await flushPromises()

    expect(mockFetchOrder.mock.calls.length).toBeGreaterThan(initialCalls)
    // 手动查询后轮询周期继续（保持 2.5s 节奏）
    await vi.advanceTimersByTimeAsync(2500)
    await flushPromises()
    expect(mockFetchOrder.mock.calls.length).toBeGreaterThan(initialCalls + 1)
    wrapper.unmount()
  })

  it('expires the QR code at expiresAt and refuses to keep polling', async () => {
    const wrapper = mountedModal({ expiresAt: new Date(Date.now() + 1000).toISOString() })
    await flushPromises()

    await vi.advanceTimersByTimeAsync(1100)
    await flushPromises()

    expect(wrapper.text()).toContain('二维码已过期，请关闭后重新下单')
    const callsAfterExpired = mockFetchOrder.mock.calls.length
    await vi.advanceTimersByTimeAsync(10000)
    await flushPromises()
    expect(mockFetchOrder.mock.calls.length).toBe(callsAfterExpired)
    wrapper.unmount()
  })

  it('renders bottom terms and stops polling when switching to Alipay', async () => {
    const wrapper = mountedModal({
      canSwitchAlipay: true,
      termsTitle: '充值服务条款',
      termsLines: ['AI 点数为虚拟商品，支付成功后立即到账且永久有效。'],
    })
    await flushPromises()

    expect(wrapper.text()).toContain('充值服务条款')
    expect(wrapper.text()).toContain('AI 点数为虚拟商品')
    expect(wrapper.text()).toContain('《服务协议（含付费订阅与退款规则）》')

    const switchBtn = wrapper.find('.wxpay-switch-alipay')
    expect(switchBtn.exists()).toBe(true)
    await switchBtn.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('switch-alipay')).toHaveLength(1)
    const callsAfterSwitch = mockFetchOrder.mock.calls.length
    await vi.advanceTimersByTimeAsync(10000)
    await flushPromises()
    expect(mockFetchOrder.mock.calls.length).toBe(callsAfterSwitch)
    wrapper.unmount()
  })
})
