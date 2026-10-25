/**
 * Copyright (c) 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { readdirSync, readFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { RESCUE_RULES, RESCUE_EXCLUDED } from '@/utils/rescueRules'
import { accountingErrorText, rescueRuleFor } from '@/utils/errors'
import { presentAccountingError, reportErrorToast } from '@/utils/errorPresenter'
import {
  clearRescueQueue,
  muteCurrentRescue,
  offerRescue,
  rescueCurrent,
  rescueNext,
} from '@/composables/useRescue'
import { readGuideState } from '@/stores/guideState'

function errWith(code: string, message = '出错了', context: Record<string, unknown> = {}) {
  return { response: { data: { detail: { code, message, context } } } }
}

const message = {
  error: vi.fn(),
  warning: vi.fn(),
  success: vi.fn(),
  info: vi.fn(),
} as never

describe('rescue rules', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    clearRescueQueue()
    vi.clearAllMocks()
  })

  afterEach(() => {
    clearRescueQueue()
  })

  it('resolves a rule for every code in the table', () => {
    Object.keys(RESCUE_RULES).forEach((code) => {
      const match = rescueRuleFor(errWith(code))
      expect(match, `规则表里的 ${code} 应当能被命中`).not.toBeNull()
      expect(match?.rule.code).toBe(code)
    })
  })

  it('ignores errors that are not structured accounting errors', () => {
    expect(rescueRuleFor(new Error('boom'))).toBeNull()
    expect(rescueRuleFor(null)).toBeNull()
    expect(rescueRuleFor({ response: { data: { detail: '纯文本错误' } } })).toBeNull()
    expect(rescueRuleFor(errWith('SOMETHING_UNKNOWN'))).toBeNull()
  })

  it('never writes a rule whose last action is not a way out', () => {
    Object.entries(RESCUE_RULES).forEach(([code, rule]) => {
      const actions = rule.actions({})
      expect(actions.length, `${code} 至少要有一个出路`).toBeGreaterThan(0)
      expect(actions[actions.length - 1].kind, `${code} 最后一个出路必须是 ghost`).toBe('ghost')
    })
  })

  it('keeps PERIOD_CLOSED and friends out of the table', () => {
    // 这三个码各有归属：补记确认 / 现成短信 UI / 纯信息告知
    RESCUE_EXCLUDED.forEach((code) => {
      expect(RESCUE_RULES[code as string], `${code} 不该进救援表`).toBeUndefined()
    })
    expect(rescueRuleFor(errWith('PERIOD_CLOSED'))).toBeNull()
  })
})

describe('rescue context quality', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
  })

  it('explains which line differs for an unbalanced voucher', () => {
    const match = rescueRuleFor(
      errWith('UNBALANCED_ENTRY', '凭证借贷不平衡', {
        difference: '2000.00',
        lines: [{ lineNo: 3, accountCode: '1002' }],
      }),
    )
    const reason =
      typeof match!.rule.reason === 'function' ? match!.rule.reason(match!.ctx) : match!.rule.reason
    expect(reason).toContain('2000.00')
    expect(reason).toContain('1002')
  })

  it('offers replacement children for a container account', () => {
    const match = rescueRuleFor(
      errWith('INVALID_ACCOUNT', '凭证包含无效或非末级科目', {
        lines: [
          {
            accountCode: '2211',
            accountName: '应付职工薪酬',
            reason: 'not_leaf',
            suggestedChildren: [{ code: '221101', name: '应付工资' }],
          },
        ],
      }),
    )
    const reason = typeof match!.rule.reason === 'function' ? match!.rule.reason(match!.ctx) : ''
    expect(reason).toContain('221101')
  })
})

describe('rescue queue', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    clearRescueQueue()
    vi.clearAllMocks()
  })

  afterEach(() => {
    clearRescueQueue()
  })

  it('keeps every offered error instead of overwriting', () => {
    expect(offerRescue(errWith('UNBALANCED_ENTRY'))).toBe(true)
    expect(offerRescue(errWith('DUPLICATE_VOUCHER_NO'))).toBe(true)

    expect(rescueCurrent()?.rule.code).toBe('UNBALANCED_ENTRY')
    rescueNext()
    expect(rescueCurrent()?.rule.code).toBe('DUPLICATE_VOUCHER_NO')
    rescueNext()
    expect(rescueCurrent()).toBeNull()
  })

  it('drops a muted code but keeps telling the user what went wrong', () => {
    offerRescue(errWith('UNBALANCED_ENTRY'))
    muteCurrentRescue()

    expect(rescueCurrent()).toBeNull()
    // 再来一次同样错误：不再弹窗，但也不是静默吞掉
    expect(offerRescue(errWith('UNBALANCED_ENTRY'))).toBe(false)
    const handled = presentAccountingError(message, errWith('UNBALANCED_ENTRY'))
    expect(handled).toBe(false)
    expect((message as unknown as { error: ReturnType<typeof vi.fn> }).error).toHaveBeenCalledTimes(1)
  })

  it('cools the same code down inside 24 hours', () => {
    expect(offerRescue(errWith('ENTRY_POSTED_IMMUTABLE'))).toBe(true)
    rescueNext()
    expect(offerRescue(errWith('ENTRY_POSTED_IMMUTABLE'))).toBe(false)
    // 换个码不受影响
    expect(offerRescue(errWith('REVIEW_REQUIRED'))).toBe(true)
  })

  it('stops after the daily limit but still falls back to a toast', () => {
    expect(offerRescue(errWith('UNBALANCED_ENTRY'))).toBe(true)
    rescueNext()
    expect(offerRescue(errWith('DUPLICATE_VOUCHER_NO'))).toBe(true)
    rescueNext()
    expect(offerRescue(errWith('REVIEW_REQUIRED'))).toBe(false)

    presentAccountingError(message, errWith('REVIEW_REQUIRED'))
    expect((message as unknown as { error: ReturnType<typeof vi.fn> }).error).toHaveBeenCalledTimes(1)
  })
})

describe('presentAccountingError', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    clearRescueQueue()
    vi.clearAllMocks()
  })

  it('restores the backend message instead of a generic failure', () => {
    reportErrorToast(message, errWith('STEP_UP_REQUIRED', '需要短信验证'))

    const call = (message as unknown as { error: ReturnType<typeof vi.fn> }).error
    expect(call).toHaveBeenCalledWith('需要短信验证')
  })

  it('never toasts twice when the rescue dialog took over', () => {
    const handled = presentAccountingError(message, errWith('UNBALANCED_ENTRY'))

    expect(handled).toBe(true)
    expect(rescueCurrent()?.rule.code).toBe('UNBALANCED_ENTRY')
    expect((message as unknown as { error: ReturnType<typeof vi.fn> }).error).not.toHaveBeenCalled()
  })

  it('steps aside for the backfill confirmation flow', () => {
    const handled = presentAccountingError(
      message,
      errWith('PERIOD_CLOSED', '期间已关账', { canBackfill: true, suggestedPostingPeriod: '2026-04' }),
    )

    expect(handled).toBe(false)
    expect(rescueCurrent()).toBeNull()
    // 让位给 backfillConfirm：这里不该抢着弹任何东西
    expect((message as unknown as { error: ReturnType<typeof vi.fn> }).error).not.toHaveBeenCalled()
  })

  it('falls back to the caller wording for non-accounting errors', () => {
    expect(accountingErrorText(new Error(''), '保存失败')).toBe('保存失败')
    expect(accountingErrorText({ response: { status: 500 } }, '保存失败')).toContain('服务')
  })
})

/**
 * 腐烂守卫（本批最重要的一条）。
 *
 * 框架 §7.2 初版写的 `UNBALANCED_JOURNAL` / `ACCOUNT_NOT_LEAF` /
 * `OPENING_NOT_BALANCED` / `TAX_REGISTRATION_MISSING` 在后端 grep 命中 0 条。
 * 照抄会产出永不命中的死规则，而且单测会假绿——所以这里直接去后端源码里核。
 */
describe('rescue rule rot guard', () => {
  it('every rule code really exists in the backend sources', () => {
    const root = resolve(process.cwd(), '../backend')
    const chunks: string[] = []

    const walk = (dir: string) => {
      readdirSync(dir).forEach((name) => {
        const full = join(dir, name)
        if (name === '__pycache__' || name === 'venv' || name === 'node_modules') return
        if (name.endsWith('.py')) {
          chunks.push(readFileSync(full, 'utf8'))
          return
        }
        try {
          const entries = readdirSync(full)
          if (entries.length >= 0) walk(full)
        } catch {
          /* ignore */
        }
      })
    }
    walk(root)
    const sources = chunks.join('\n')

    Object.keys(RESCUE_RULES).forEach((code) => {
      expect(
        sources.includes(`"${code}"`) || sources.includes(`'${code}'`) || sources.includes(code),
        `规则表里的 ${code} 在 backend 源码里找不到——它永远不会命中`,
      ).toBe(true)
    })
  })

  it('records the mute into the same per-company guide state', () => {
    setActivePinia(createPinia())
    offerRescue(errWith('DUPLICATE_VOUCHER_NO'), { companyId: 'c-mute' }, new Date())
    muteCurrentRescue('c-mute')

    expect(readGuideState('c-mute').rescueMutedCodes).toContain('DUPLICATE_VOUCHER_NO')
  })
})
