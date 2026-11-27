/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { NCheckbox } from 'naive-ui'

/**
 * 微信开放平台网站应用扫码登录（前端）回归测试。
 *
 * 重点防的是跨层不变量，而不是像素：
 * - message 只接受「同源 + 来自本次出码 iframe」，否则恶意页面可以顶替扫码结果；
 * - 消息里没有 ticket，exchange 失败时绝不写 token；
 * - 首次登录返回 phoneRequired 时不能落登录态（不创建账号）；
 * - 默认已勾选协议，切到微信 tab 立即自动出码（无需再手动点击勾选）；
 * - 取消勾选协议则清码且不出码（后端 authorize-url 强制校验 privacy_accepted_at）；
 * - 二维码按需现做：切到微信 tab 才调 authorize-url，可手动刷新；
 * - 回调页在 iframe 内只 postMessage 给 parent，不自行跳转。
 */

const ORIGIN = 'https://solobooks.cn'

const mocks = vi.hoisted(() => {
  const fn = () => vi.fn()
  return {
    sendPhoneCode: fn(),
    loginByPhoneCode: fn(),
    loginByPassword: fn(),
    resetPassword: fn(),
    getWechatWebConfig: fn(),
    getWechatWebAuthorizeUrl: fn(),
    exchangeWechatWebTicket: fn(),
    bindWechatWebPhone: fn(),
    setSession: fn(),
    fetchProfile: fn(),
    routerReplace: fn(),
    routerPush: fn(),
    message: { success: fn(), error: fn(), warning: fn(), info: fn() },
    route: { query: {} as Record<string, unknown> } as {
      query: Record<string, unknown>
      path?: string
    },
  }
})

vi.mock('@/api/auth', () => ({
  sendPhoneCode: mocks.sendPhoneCode,
  loginByPhoneCode: mocks.loginByPhoneCode,
  loginByPassword: mocks.loginByPassword,
  resetPassword: mocks.resetPassword,
  getWechatWebConfig: mocks.getWechatWebConfig,
  getWechatWebAuthorizeUrl: mocks.getWechatWebAuthorizeUrl,
  exchangeWechatWebTicket: mocks.exchangeWechatWebTicket,
  bindWechatWebPhone: mocks.bindWechatWebPhone,
}))

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({
    setSession: mocks.setSession,
    fetchProfile: mocks.fetchProfile,
  }),
}))

vi.mock('vue-router', () => ({
  useRoute: () => mocks.route,
  useRouter: () => ({ replace: mocks.routerReplace, push: mocks.routerPush }),
  RouterLink: {
    name: 'RouterLink',
    props: ['to'],
    template: '<a class="router-link-stub"><slot /></a>',
  },
}))

vi.mock('naive-ui', async (importOriginal) => ({
  ...(await importOriginal<typeof import('naive-ui')>()),
  useMessage: () => mocks.message,
}))

import LoginPage from '@/pages/auth/login.vue'
import WechatCallbackPage from '@/pages/auth/wechat-callback.vue'

type AnyMock = ReturnType<typeof vi.fn>

const originalLocation = window.location
const originalOpener = window.opener
const originalParent = window.parent

interface StubbedLocation {
  origin: string
  href: string
  pathname: string
  search: string
  hash: string
  replace: AnyMock
  assign: AnyMock
  reload: AnyMock
}

let locationStub: StubbedLocation

function installLocation(): StubbedLocation {
  const stub: StubbedLocation = {
    origin: ORIGIN,
    href: '',
    pathname: '/login',
    search: '',
    hash: '',
    replace: vi.fn(),
    assign: vi.fn(),
    reload: vi.fn(),
  }
  Object.defineProperty(window, 'location', { configurable: true, value: stub })
  return stub
}

function installMatchMedia(wide: boolean, coarse: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: vi.fn((query: string) => ({
      matches: query.includes('min-width') ? wide : coarse,
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  })
}

function installOpener(opener: unknown) {
  Object.defineProperty(window, 'opener', {
    configurable: true,
    writable: true,
    value: opener,
  })
}

function installParent(parent: unknown) {
  Object.defineProperty(window, 'parent', {
    configurable: true,
    writable: true,
    value: parent,
  })
}

function closeStub() {
  const close = vi.fn()
  Object.defineProperty(window, 'close', { configurable: true, writable: true, value: close })
  return close
}

function qrIframeWindow(wrapper: VueWrapper): unknown {
  const frame = wrapper.find('iframe.wechat-qr-frame').element as HTMLIFrameElement | undefined
  return frame?.contentWindow ?? null
}

function dispatchWechatMessage(
  payload: unknown,
  options: { origin?: string; source?: unknown } = {},
) {
  const event = new MessageEvent('message', { data: payload })
  Object.defineProperty(event, 'origin', { value: options.origin ?? ORIGIN, configurable: true })
  Object.defineProperty(event, 'source', { value: options.source ?? null, configurable: true })
  window.dispatchEvent(event)
}

const AUTH_RESPONSE = {
  access_token: 'jwt-token',
  token_type: 'bearer',
  user_id: 'u_1',
  company_id: 'c_1',
  expires_in: '86400',
}

async function mountLogin(options: { wide?: boolean; coarse?: boolean; enabled?: boolean } = {}) {
  installMatchMedia(options.wide !== false, options.coarse === true)
  mocks.getWechatWebConfig.mockResolvedValue({ enabled: options.enabled !== false })
  const wrapper = mount(LoginPage, {
    global: { stubs: { teleport: true, RouterLink: true } },
  })
  await flushPromises()
  return wrapper
}

function tabLabels(wrapper: VueWrapper) {
  return wrapper.findAll('.n-tabs-tab').map((tab) => tab.text())
}

async function switchToWechatTab(wrapper: VueWrapper) {
  const tab = wrapper.findAll('.n-tabs-tab').find((item) => item.text().includes('微信扫码登录'))
  expect(tab, '微信扫码登录 tab 应该存在').toBeTruthy()
  await tab!.trigger('click')
  await flushPromises()
}

async function setAgreement(wrapper: VueWrapper, checked: boolean) {
  wrapper.findComponent(NCheckbox).vm.$emit('update:checked', checked)
  await flushPromises()
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.exchangeWechatWebTicket.mockReset()
  mocks.bindWechatWebPhone.mockReset()
  mocks.getWechatWebConfig.mockReset()
  mocks.getWechatWebAuthorizeUrl.mockReset()
  mocks.route.query = {}
  locationStub = installLocation()
  installOpener(null)
  mocks.getWechatWebConfig.mockResolvedValue({ enabled: true })
  mocks.getWechatWebAuthorizeUrl.mockResolvedValue({ url: 'https://open.weixin.qq.com/qr?state=x' })
  mocks.fetchProfile.mockResolvedValue({})
  mocks.sendPhoneCode.mockResolvedValue({ biz_id: 'biz_1' })
})

afterEach(() => {
  Object.defineProperty(window, 'location', { configurable: true, value: originalLocation })
  Object.defineProperty(window, 'opener', { configurable: true, value: originalOpener })
  Object.defineProperty(window, 'parent', { configurable: true, value: originalParent })
  vi.restoreAllMocks()
})

describe('登录页 · 微信扫码入口', () => {
  it('桌面宽度且后端开启时显示微信 tab', async () => {
    const wrapper = await mountLogin()
    expect(tabLabels(wrapper)).toContain('微信扫码登录')
    wrapper.unmount()
  })

  it('窄屏与触屏设备隐藏微信 tab（手机浏览器走短信验证码）', async () => {
    const narrow = await mountLogin({ wide: false })
    expect(tabLabels(narrow)).not.toContain('微信扫码登录')
    narrow.unmount()

    const touch = await mountLogin({ wide: true, coarse: true })
    expect(tabLabels(touch)).not.toContain('微信扫码登录')
    touch.unmount()
  })

  it('后端 enabled=false 时隐藏入口，不影响手机号登录', async () => {
    const wrapper = await mountLogin({ enabled: false })
    expect(tabLabels(wrapper)).not.toContain('微信扫码登录')
    expect(tabLabels(wrapper)).toContain('验证码登录')
    wrapper.unmount()
  })

  it('默认已勾选协议，切到微信 tab 即自动出码（iframe 内嵌），携带 redirect 与隐私版本', async () => {
    mocks.route.query = { redirect: '/bank', plan: 'trial' }
    const wrapper = await mountLogin()
    // 仍在验证码 tab 时不提前调授权接口
    expect(mocks.getWechatWebAuthorizeUrl).not.toHaveBeenCalled()

    await switchToWechatTab(wrapper)

    expect(mocks.getWechatWebAuthorizeUrl).toHaveBeenCalledTimes(1)
    const payload = mocks.getWechatWebAuthorizeUrl.mock.calls[0][0]
    expect(payload.redirect).toBe('/bank')
    expect(typeof payload.privacy_accepted_at).toBe('string')
    expect(payload.tos_version).toBeTruthy()
    expect(payload.privacy_version).toBeTruthy()

    const frame = wrapper.find('iframe.wechat-qr-frame')
    expect(frame.exists()).toBe(true)
    expect(frame.attributes('src')).toBe('https://open.weixin.qq.com/qr?state=x')
    wrapper.unmount()
  })

  it('主动取消勾选协议时清码不出码，重新勾选后恢复出码', async () => {
    const wrapper = await mountLogin()
    await switchToWechatTab(wrapper)
    expect(wrapper.find('iframe.wechat-qr-frame').exists()).toBe(true)

    await setAgreement(wrapper, false)
    expect(wrapper.find('iframe.wechat-qr-frame').exists()).toBe(false)

    await setAgreement(wrapper, true)
    expect(mocks.getWechatWebAuthorizeUrl).toHaveBeenCalledTimes(2)
    expect(wrapper.find('iframe.wechat-qr-frame').exists()).toBe(true)
    wrapper.unmount()
  })

  it('刷新二维码重新出码：旧 state 作废，新 src 生效', async () => {
    mocks.getWechatWebAuthorizeUrl
      .mockResolvedValueOnce({ url: 'https://open.weixin.qq.com/qr?state=old' })
      .mockResolvedValueOnce({ url: 'https://open.weixin.qq.com/qr?state=new' })
    const wrapper = await mountLogin()
    await switchToWechatTab(wrapper)

    const refresh = wrapper.findAll('a').find((link) => link.text().includes('刷新二维码'))
    expect(refresh).toBeTruthy()
    await refresh!.trigger('click')
    await flushPromises()

    expect(mocks.getWechatWebAuthorizeUrl).toHaveBeenCalledTimes(2)
    expect(wrapper.find('iframe.wechat-qr-frame').attributes('src')).toBe(
      'https://open.weixin.qq.com/qr?state=new',
    )
    wrapper.unmount()
  })
})

describe('登录页 · 出码 iframe message 校验', () => {
  async function openQrFlow(query: Record<string, string> = {}) {
    mocks.route.query = query
    const wrapper = await mountLogin()
    await switchToWechatTab(wrapper)
    return wrapper
  }

  it('拒绝非同源的 ready 消息', async () => {
    const wrapper = await openQrFlow()
    dispatchWechatMessage(
      { type: 'solo-wechat-login', status: 'ready' },
      { origin: 'https://evil.example', source: qrIframeWindow(wrapper) },
    )
    await flushPromises()

    expect(mocks.exchangeWechatWebTicket).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('拒绝来源不是本次出码 iframe 的 ready 消息', async () => {
    const wrapper = await openQrFlow()
    dispatchWechatMessage(
      { type: 'solo-wechat-login', status: 'ready' },
      { origin: ORIGIN, source: { postMessage: vi.fn() } },
    )
    await flushPromises()

    expect(mocks.exchangeWechatWebTicket).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('忽略 type 不匹配的消息', async () => {
    const wrapper = await openQrFlow()
    dispatchWechatMessage(
      { type: 'other', status: 'ready' },
      { origin: ORIGIN, source: qrIframeWindow(wrapper) },
    )
    await flushPromises()

    expect(mocks.exchangeWechatWebTicket).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('error 消息清码并展示可读错误，等用户手动重新出码', async () => {
    const wrapper = await openQrFlow()
    dispatchWechatMessage(
      { type: 'solo-wechat-login', status: 'error', code: 'access_denied' },
      { origin: ORIGIN, source: qrIframeWindow(wrapper) },
    )
    await flushPromises()

    expect(mocks.exchangeWechatWebTicket).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('你取消了微信授权')
    expect(wrapper.find('iframe.wechat-qr-frame').exists()).toBe(false)
    wrapper.unmount()
  })

  it('同源同 iframe 的 ready 触发 exchange，成功后写入会话并按 redirect 整页跳转', async () => {
    mocks.exchangeWechatWebTicket.mockResolvedValue(AUTH_RESPONSE)
    const wrapper = await openQrFlow({ redirect: '/bank' })

    dispatchWechatMessage(
      { type: 'solo-wechat-login', status: 'ready' },
      { origin: ORIGIN, source: qrIframeWindow(wrapper) },
    )
    await flushPromises()

    expect(mocks.exchangeWechatWebTicket).toHaveBeenCalledTimes(1)
    expect(mocks.setSession).toHaveBeenCalledWith(AUTH_RESPONSE)
    expect(mocks.fetchProfile).toHaveBeenCalled()
    expect(locationStub.replace).toHaveBeenCalledWith('/bank')
    wrapper.unmount()
  })

  it('重复 ready 只换一次（会话幂等标记，防重复 postMessage）', async () => {
    mocks.exchangeWechatWebTicket.mockResolvedValue(AUTH_RESPONSE)
    const wrapper = await openQrFlow()

    dispatchWechatMessage(
      { type: 'solo-wechat-login', status: 'ready' },
      { origin: ORIGIN, source: qrIframeWindow(wrapper) },
    )
    dispatchWechatMessage(
      { type: 'solo-wechat-login', status: 'ready' },
      { origin: ORIGIN, source: qrIframeWindow(wrapper) },
    )
    await flushPromises()

    expect(mocks.exchangeWechatWebTicket).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('exchange 失败不写 token，只在页面提示错误', async () => {
    mocks.exchangeWechatWebTicket.mockRejectedValue({
      response: { data: { detail: 'state_invalid' } },
    })
    const wrapper = await openQrFlow()

    dispatchWechatMessage(
      { type: 'solo-wechat-login', status: 'ready' },
      { origin: ORIGIN, source: qrIframeWindow(wrapper) },
    )
    await flushPromises()

    expect(mocks.setSession).not.toHaveBeenCalled()
    expect(locationStub.replace).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('state_invalid')
    wrapper.unmount()
  })

  it('phoneRequired 只弹绑定手机号，不写 token', async () => {
    mocks.exchangeWechatWebTicket.mockResolvedValue({ phoneRequired: true })
    const wrapper = await openQrFlow()

    dispatchWechatMessage(
      { type: 'solo-wechat-login', status: 'ready' },
      { origin: ORIGIN, source: qrIframeWindow(wrapper) },
    )
    await flushPromises()

    expect(mocks.setSession).not.toHaveBeenCalled()
    expect(mocks.fetchProfile).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('微信登录首次使用需绑定手机号')
    expect(wrapper.text()).toContain('账套与套餐保留')
    wrapper.unmount()
  })
})

describe('登录页 · 绑定手机号', () => {
  async function openBindModal(wrapper: VueWrapper) {
    mocks.exchangeWechatWebTicket.mockResolvedValue({ phoneRequired: true })
    await switchToWechatTab(wrapper)
    dispatchWechatMessage(
      { type: 'solo-wechat-login', status: 'ready' },
      { origin: ORIGIN, source: qrIframeWindow(wrapper) },
    )
    await flushPromises()
  }

  async function fillBindForm(wrapper: VueWrapper, phone: string, code: string) {
    const modal = wrapper.find('.n-modal')
    expect(modal.exists()).toBe(true)
    const inputs = modal.findAll('input')
    expect(inputs.length).toBeGreaterThanOrEqual(2)
    await inputs[0].setValue(phone)
    await inputs[1].setValue(code)
  }

  it('提交绑定成功后 setSession 并跳转', async () => {
    mocks.bindWechatWebPhone.mockResolvedValue(AUTH_RESPONSE)
    const wrapper = await mountLogin()
    await openBindModal(wrapper)
    await fillBindForm(wrapper, '13800138000', '123456')

    const submit = wrapper.findAll('button').find((button) => button.text().includes('绑定并登录'))
    expect(submit).toBeTruthy()
    await submit!.trigger('click')
    await flushPromises()

    expect(mocks.bindWechatWebPhone).toHaveBeenCalledWith({
      phone: '13800138000',
      code: '123456',
      biz_id: undefined,
    })
    expect(mocks.setSession).toHaveBeenCalledWith(AUTH_RESPONSE)
    expect(locationStub.replace).toHaveBeenCalledWith('/')
    wrapper.unmount()
  })

  it('绑定失败不写 token，保留弹窗供换号重试', async () => {
    mocks.bindWechatWebPhone.mockRejectedValue({
      response: { data: { detail: '该手机号已绑定其他微信账号' } },
    })
    const wrapper = await mountLogin()
    await openBindModal(wrapper)
    await fillBindForm(wrapper, '13800138000', '123456')

    const submit = wrapper.findAll('button').find((button) => button.text().includes('绑定并登录'))
    await submit!.trigger('click')
    await flushPromises()

    expect(mocks.setSession).not.toHaveBeenCalled()
    expect(mocks.message.error).toHaveBeenCalledWith('该手机号已绑定其他微信账号')
    wrapper.unmount()
  })

  it('wechat_pending=1 进入登录页时直接弹绑定框，不再调 exchange', async () => {
    mocks.route.query = { wechat_pending: '1', redirect: '/bank' }
    const wrapper = await mountLogin()

    expect(mocks.exchangeWechatWebTicket).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('微信登录首次使用需绑定手机号')
    // 一次性状态参数被清掉，但 redirect 意向保留
    expect(mocks.routerReplace).toHaveBeenCalled()
    const replacement = mocks.routerReplace.mock.calls[0][0]
    expect(replacement.path).toBe('/login')
    expect(replacement.query).toEqual({ redirect: '/bank' })
    wrapper.unmount()
  })

  it('wechat_error 落回登录页时展示可读错误', async () => {
    mocks.route.query = { wechat_error: 'state_invalid' }
    const wrapper = await mountLogin()

    expect(wrapper.text()).toContain('登录二维码已过期')
    wrapper.unmount()
  })
})

describe('微信回调中转页', () => {
  it('有 opener：只发 ready 不换登录态，并尝试关闭窗口', async () => {
    installOpener({ postMessage: vi.fn() })
    const close = closeStub()
    const wrapper = mount(WechatCallbackPage, { global: { stubs: { teleport: true } } })
    await flushPromises()

    const opener = window.opener as { postMessage: AnyMock }
    expect(opener.postMessage).toHaveBeenCalledWith(
      { type: 'solo-wechat-login', status: 'ready' },
      ORIGIN,
    )
    expect(close).toHaveBeenCalled()
    expect(mocks.exchangeWechatWebTicket).not.toHaveBeenCalled()
    expect(mocks.setSession).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('内嵌 iframe（有 parent 无 opener）：只 postMessage ready 给父窗口，不自行 exchange/跳转', async () => {
    const parentStub = { postMessage: vi.fn() }
    installOpener(null)
    installParent(parentStub)
    mocks.route.query = { redirect: '/reports' }
    const wrapper = mount(WechatCallbackPage, { global: { stubs: { teleport: true } } })
    await flushPromises()

    expect(parentStub.postMessage).toHaveBeenCalledWith(
      { type: 'solo-wechat-login', status: 'ready' },
      ORIGIN,
    )
    expect(mocks.exchangeWechatWebTicket).not.toHaveBeenCalled()
    expect(mocks.setSession).not.toHaveBeenCalled()
    expect(locationStub.replace).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('内嵌 iframe：微信侧报错以 error 消息回传父窗口，不整页跳转', async () => {
    const parentStub = { postMessage: vi.fn() }
    installOpener(null)
    installParent(parentStub)
    mocks.route.query = { wechat_error: 'access_denied' }
    const wrapper = mount(WechatCallbackPage, { global: { stubs: { teleport: true } } })
    await flushPromises()

    expect(parentStub.postMessage).toHaveBeenCalledWith(
      { type: 'solo-wechat-login', status: 'error', code: 'access_denied' },
      ORIGIN,
    )
    expect(mocks.exchangeWechatWebTicket).not.toHaveBeenCalled()
    expect(locationStub.replace).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('弹窗内收到上游错误：导航回登录页展示错误（保持原行为）', async () => {
    installOpener({ postMessage: vi.fn() })
    mocks.route.query = { wechat_error: 'access_denied' }
    const wrapper = mount(WechatCallbackPage, { global: { stubs: { teleport: true } } })
    await flushPromises()

    const opener = window.opener as { postMessage: AnyMock }
    expect(opener.postMessage).not.toHaveBeenCalled()
    expect(locationStub.replace).toHaveBeenCalledWith('/login?wechat_error=access_denied')
    wrapper.unmount()
  })

  it('无 opener：自己 exchange，成功后写入会话并整页跳转', async () => {
    installOpener(null)
    mocks.route.query = { redirect: '/reports' }
    mocks.exchangeWechatWebTicket.mockResolvedValue(AUTH_RESPONSE)
    const wrapper = mount(WechatCallbackPage, { global: { stubs: { teleport: true } } })
    await flushPromises()

    expect(mocks.exchangeWechatWebTicket).toHaveBeenCalledTimes(1)
    expect(mocks.setSession).toHaveBeenCalledWith(AUTH_RESPONSE)
    expect(mocks.fetchProfile).toHaveBeenCalled()
    expect(locationStub.replace).toHaveBeenCalledWith('/reports')
    wrapper.unmount()
  })

  it('无 opener：phoneRequired 跳回登录页弹绑定框', async () => {
    installOpener(null)
    mocks.route.query = { redirect: '/bank' }
    mocks.exchangeWechatWebTicket.mockResolvedValue({ phoneRequired: true })
    const wrapper = mount(WechatCallbackPage, { global: { stubs: { teleport: true } } })
    await flushPromises()

    expect(mocks.setSession).not.toHaveBeenCalled()
    expect(locationStub.replace).toHaveBeenCalledWith('/login?wechat_pending=1&redirect=%2Fbank')
    wrapper.unmount()
  })

  it('无 opener：exchange 失败跳登录页并带错误码', async () => {
    installOpener(null)
    mocks.exchangeWechatWebTicket.mockRejectedValue({
      response: { status: 410, data: { detail: 'ticket_expired' } },
    })
    const wrapper = mount(WechatCallbackPage, { global: { stubs: { teleport: true } } })
    await flushPromises()

    expect(locationStub.replace).toHaveBeenCalledWith('/login?wechat_error=ticket_expired')
    wrapper.unmount()
  })

  it('后端回调直接带 wechat_error 时不再调 exchange', async () => {
    installOpener(null)
    mocks.route.query = { wechat_error: 'access_denied' }
    const wrapper = mount(WechatCallbackPage, { global: { stubs: { teleport: true } } })
    await flushPromises()

    expect(mocks.exchangeWechatWebTicket).not.toHaveBeenCalled()
    expect(locationStub.replace).toHaveBeenCalledWith('/login?wechat_error=access_denied')
    wrapper.unmount()
  })

  it('redirect 参数为外链时回落到首页（防 open redirect）', async () => {
    installOpener(null)
    mocks.route.query = { redirect: '//evil.example/phish' }
    mocks.exchangeWechatWebTicket.mockResolvedValue(AUTH_RESPONSE)
    const wrapper = mount(WechatCallbackPage, { global: { stubs: { teleport: true } } })
    await flushPromises()

    expect(locationStub.replace).toHaveBeenCalledWith('/')
    wrapper.unmount()
  })
})
