/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 *
 * 短信验证码发送的回归测试：失败时必须透出后端 detail（限流秒数、
 * 短信服务错误），不能退回笼统的「验证码发送失败」——期初确认、反结账、
 * 历史导入三处曾各自吞掉详细原因，用户无法判断是限流还是号码问题。
 */

import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { MessageApi } from 'naive-ui'
import { sendPhoneCode } from '@/api/auth'
import { sendVerificationCode } from '@/utils/verificationCode'

vi.mock('@/api/auth', () => ({ sendPhoneCode: vi.fn() }))

function fakeMessage(): MessageApi {
  return {
    success: vi.fn(),
    warning: vi.fn(),
    error: vi.fn(),
  } as unknown as MessageApi
}

describe('sendVerificationCode', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('手机号为空时只提示，不请求后端', async () => {
    const message = fakeMessage()
    const outcome = await sendVerificationCode(message, '   ', '请输入当前账号已绑定的手机号')

    expect(sendPhoneCode).not.toHaveBeenCalled()
    expect(message.warning).toHaveBeenCalledWith('请输入当前账号已绑定的手机号')
    expect(outcome).toEqual({ ok: false, bizId: '' })
  })

  it('发送成功返回 bizId 并提示已发送', async () => {
    const message = fakeMessage()
    vi.mocked(sendPhoneCode).mockResolvedValue({ biz_id: 'biz-1' } as never)

    const outcome = await sendVerificationCode(message, ' 13800000000 ', '请输入手机号')

    expect(sendPhoneCode).toHaveBeenCalledWith({ phone: '13800000000' })
    expect(message.success).toHaveBeenCalledWith('验证码已发送')
    expect(outcome).toEqual({ ok: true, bizId: 'biz-1' })
  })

  it('后端 429 的剩余秒数要原样展示，而不是「验证码发送失败」', async () => {
    const message = fakeMessage()
    vi.mocked(sendPhoneCode).mockRejectedValue({
      response: {
        status: 429,
        data: { detail: '发送过于频繁，请 42 秒后再试' },
      },
    })

    const outcome = await sendVerificationCode(message, '13800000000', '请输入手机号')

    expect(outcome.ok).toBe(false)
    expect(message.error).toHaveBeenCalledWith('发送过于频繁，请 42 秒后再试')
    expect(message.error).not.toHaveBeenCalledWith('验证码发送失败')
  })

  it('阿里云错误信息也透出给用户', async () => {
    const message = fakeMessage()
    vi.mocked(sendPhoneCode).mockRejectedValue({
      response: {
        status: 400,
        data: { detail: '短信服务错误: isv.BUSINESS_LIMIT_CONTROL' },
      },
    })

    await sendVerificationCode(message, '13800000000', '请输入手机号')

    expect(message.error).toHaveBeenCalledWith('短信服务错误: isv.BUSINESS_LIMIT_CONTROL')
  })
})
