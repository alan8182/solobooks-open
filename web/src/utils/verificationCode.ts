/**
 * Copyright (c) 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 短信验证码发送的统一入口。
 *
 * 三个二次验证场景（期初确认、反结账、历史导入）原先各自复制了一份
 * `sendPhoneCode` 调用，且 catch 里统一吞成「验证码发送失败」：后端返回的
 * 429「发送过于频繁，请 X 秒后再试」、503「短信服务未配置」、阿里云
 * `isv.*` 错误都变成了同一句空话，用户无从判断。这里统一走
 * `reportErrorToast`，把后端 detail 原样透出。
 */
import type { MessageApi } from 'naive-ui'
import { sendPhoneCode } from '@/api/auth'
import { reportErrorToast } from '@/utils/errorPresenter'

export interface VerificationCodeOutcome {
  ok: boolean
  bizId: string
}

/**
 * 发送验证码并处理提示。
 *
 * @param message naive-ui 的 message 实例
 * @param phone 用户填写的手机号
 * @param emptyHint 手机号为空时的提示（各页面文案不同）
 */
export async function sendVerificationCode(
  message: MessageApi,
  phone: string,
  emptyHint: string,
): Promise<VerificationCodeOutcome> {
  const value = (phone || '').trim()
  if (!value) {
    message.warning(emptyHint)
    return { ok: false, bizId: '' }
  }
  try {
    const result = await sendPhoneCode({ phone: value })
    message.success('验证码已发送')
    return { ok: true, bizId: result.biz_id || '' }
  } catch (error) {
    reportErrorToast(message, error, '验证码发送失败')
    return { ok: false, bizId: '' }
  }
}
