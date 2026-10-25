/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 错误救援规则表：错误码 → 症状 + 说人话的为什么 + 出路。
 *
 * 三条硬规则：
 *
 * 1. **码必须真实存在于后端**。框架 §7.2 初版写的 `UNBALANCED_JOURNAL` /
 *    `ACCOUNT_NOT_LEAF` / `OPENING_NOT_BALANCED` / `TAX_REGISTRATION_MISSING`
 *    全部 grep 命中 0 条，照抄会产出永不命中的死规则，而且测试会假绿。
 *    `__tests__/rescueRules.test.ts` 里有「扫后端源码 grep 每个码」的腐烂守卫。
 * 2. **出路必须指得到具体位置**。只说「去看凭证」等于没说，所以 actions
 *    取 `ctx.entryId` / `ctx.accountCode` 拼深链；没有 context 的码不出现在这里。
 * 3. **最后一个出路永远是「返回修改」**，用户必须有退路。
 */

export type RescueActionKind = 'primary' | 'secondary' | 'ghost'

export interface RescueAction {
  label: string
  kind: RescueActionKind
  path?: string
  query?: Record<string, string>
  run?: () => void
}

export interface RescueRule {
  code: string
  /** 症状：一句话说清发生了什么 */
  title: string
  /** 为什么，以及错在哪 —— 能带 ctx 就带，别让用户自己猜 */
  reason: string | ((ctx: Record<string, unknown>) => string)
  actions: (ctx: Record<string, unknown>) => RescueAction[]
  severity: 'blocker' | 'recoverable'
}

interface IssueLine {
  accountCode?: string
  accountName?: string
  reason?: string
  suggestedChildren?: Array<{ code?: string; name?: string }>
}

function issues(ctx: Record<string, unknown>): IssueLine[] {
  return Array.isArray(ctx.lines) ? (ctx.lines as IssueLine[]) : []
}

function entryIdOf(ctx: Record<string, unknown>): string {
  return typeof ctx.entryId === 'string' ? ctx.entryId : ''
}

function text(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

const REASON_LABEL: Record<string, string> = {
  not_found: '不在科目表里，可能是被删了或代码写错了',
  inactive: '已经停用，不能再记账',
  not_leaf: '是上级汇总科目，要记在它下面的末级明细上',
}

export const RESCUE_RULES: Record<string, RescueRule> = {
  UNBALANCED_ENTRY: {
    code: 'UNBALANCED_ENTRY',
    title: '无法入账：借贷不平',
    severity: 'blocker',
    reason: (ctx) => {
      const difference = text(ctx.difference)
      const lines = Array.isArray(ctx.lines) ? (ctx.lines as Array<Record<string, unknown>>) : []
      const base = difference
        ? `借方与贷方合计相差 ¥${difference}`
        : '借方与贷方合计不相等（或是分录不足两行）'
      const first = lines[0]
      const accountCode = first ? text(first.accountCode) : ''
      return accountCode ? `${base}，先看第 ${text(first?.lineNo)} 行 ${accountCode}` : base
    },
    actions: (ctx) => {
      const entryId = entryIdOf(ctx)
      const actions: RescueAction[] = []
      if (entryId) {
        actions.push({ label: '定位这张凭证', kind: 'primary', path: `/vouchers/${entryId}` })
      }
      actions.push({ label: '去记账', kind: entryId ? 'secondary' : 'primary', path: '/copilot' })
      actions.push({ label: '返回修改', kind: 'ghost' })
      return actions
    },
  },

  INVALID_ACCOUNT: {
    code: 'INVALID_ACCOUNT',
    title: '无法入账：科目不能用',
    severity: 'blocker',
    reason: (ctx) => {
      const list = issues(ctx)
      if (!list.length) return '凭证里有无效或非末级科目'
      const parts = list.slice(0, 3).map((item) => {
        const code = text(item.accountCode)
        const name = text(item.accountName)
        const label = name ? `${code}（${name}）` : code
        const why = REASON_LABEL[text(item.reason)] || '不可用'
        const hint = (item.suggestedChildren || [])
          .map((child) => text(child.code))
          .filter(Boolean)
          .slice(0, 3)
          .join('、')
        return hint ? `${label}${why}，可改用 ${hint}` : `${label}${why}`
      })
      const more = list.length > 3 ? ` 等 ${list.length} 个科目` : ''
      return parts.join('；') + more
    },
    actions: (ctx) => {
      const entryId = entryIdOf(ctx)
      const actions: RescueAction[] = []
      if (entryId) {
        actions.push({ label: '定位这张凭证', kind: 'primary', path: `/vouchers/${entryId}` })
      }
      actions.push({ label: '去科目表', kind: 'primary', path: '/mine/accounts' })
      actions.push({ label: '返回修改', kind: 'ghost' })
      return actions
    },
  },

  OPENING_ACCOUNT_INVALID: {
    code: 'OPENING_ACCOUNT_INVALID',
    title: '期初余额录不进',
    severity: 'blocker',
    reason: (ctx) => {
      const code = text(ctx.accountCode)
      const name = text(ctx.accountName)
      const label = name ? `${code}（${name}）` : code || '该科目'
      const why = REASON_LABEL[text(ctx.reason)] || '不能录入期初'
      const hint = (Array.isArray(ctx.suggestedChildren)
        ? (ctx.suggestedChildren as Array<Record<string, unknown>>)
        : []
      )
        .map((child) => text(child.code))
        .filter(Boolean)
        .slice(0, 4)
        .join('、')
      return hint ? `科目 ${label}${why}，可改用 ${hint}` : `科目 ${label}${why}`
    },
    actions: () => [
      { label: '去期初设置', kind: 'primary', path: '/mine/opening' },
      { label: '去科目表', kind: 'secondary', path: '/mine/accounts' },
      { label: '返回修改', kind: 'ghost' },
    ],
  },

  OPENING_SIDE_CONFLICT: {
    code: 'OPENING_SIDE_CONFLICT',
    title: '期初余额填法冲突',
    severity: 'blocker',
    reason: (ctx) => {
      const code = text(ctx.accountCode)
      const name = text(ctx.accountName)
      const label = name ? `${code}（${name}）` : code || '该科目'
      return `科目 ${label} 同时填了借方 ¥${text(ctx.debit)} 和贷方 ¥${text(ctx.credit)}，同一个科目只能二选一`
    },
    actions: () => [
      { label: '去期初设置', kind: 'primary', path: '/mine/opening' },
      { label: '返回修改', kind: 'ghost' },
    ],
  },

  DUPLICATE_VOUCHER_NO: {
    code: 'DUPLICATE_VOUCHER_NO',
    title: '凭证号重复',
    severity: 'blocker',
    reason: (ctx) => {
      const no = text(ctx.voucherNo)
      const occurredOn = text(ctx.occurredOn)
      const used = Array.isArray(ctx.usedVoucherNos)
        ? (ctx.usedVoucherNos as string[]).filter(Boolean)
        : []
      const head = occurredOn
        ? `凭证号 ${no} 在 ${occurredOn} 已被占用`
        : `凭证号 ${no} 已存在`
      return used.length ? `${head}。该月已用：${used.slice(0, 6).join('、')}` : `${head}，请改号或跳过该张`
    },
    actions: () => [
      { label: '去旧账导入改号', kind: 'primary', path: '/mine/legacy-import' },
      { label: '返回修改', kind: 'ghost' },
    ],
  },

  ENTRY_POSTED_IMMUTABLE: {
    code: 'ENTRY_POSTED_IMMUTABLE',
    title: '已过账的凭证不能直接改',
    severity: 'blocker',
    reason: (ctx) => {
      const ref = text(ctx.voucherRef)
      const occurredOn = text(ctx.occurredOn)
      const head = ref ? `凭证「${ref}」` : '这张凭证'
      return `${head}${occurredOn ? `（${occurredOn}）` : ''}已经过账，账簿锁定。改动只能走冲销再重做，这是会计底线`
    },
    actions: (ctx) => {
      const entryId = entryIdOf(ctx)
      const actions: RescueAction[] = []
      if (entryId) {
        actions.push({ label: '去冲销', kind: 'primary', path: `/vouchers/${entryId}` })
      }
      actions.push({ label: '看该月凭证', kind: 'primary', path: '/vouchers' })
      actions.push({ label: '返回', kind: 'ghost' })
      return actions
    },
  },

  PERIOD_BEFORE_BOOK_START: {
    code: 'PERIOD_BEFORE_BOOK_START',
    title: '这个月份在账套启用之前',
    severity: 'recoverable',
    reason: (ctx) => {
      const period = text(ctx.period)
      const start = text(ctx.bookStartMonth)
      return `${period || '该月份'} 早于账套启用月份 ${start || '（未设置）'}，不在本账套的记账范围。`
        + '启用前的业务要么把启用日期前移，要么按「期初余额」录入'
    },
    actions: () => [
      { label: '去改启用日期', kind: 'primary', path: '/mine/companies' },
      { label: '去期初设置', kind: 'secondary', path: '/mine/opening' },
      { label: '返回', kind: 'ghost' },
    ],
  },

  REVIEW_REQUIRED: {
    code: 'REVIEW_REQUIRED',
    title: '需要他人复核',
    severity: 'recoverable',
    reason: (ctx) => {
      const ref = text(ctx.voucherRef)
      const createdBy = text(ctx.createdBy)
      const head = ref ? `凭证「${ref}」` : '这张凭证'
      const who = createdBy ? '它是你自己制的' : '制单人与复核人必须不同'
      return `${head}${who}。这道闸门不能自己绕：提交后由老板或会计复核过账`
    },
    actions: (ctx) => {
      const entryId = entryIdOf(ctx)
      const actions: RescueAction[] = []
      if (entryId) {
        actions.push({ label: '去提交复核', kind: 'primary', path: `/vouchers/${entryId}` })
      }
      actions.push({ label: '看凭证列表', kind: entryId ? 'secondary' : 'primary', path: '/vouchers' })
      actions.push({ label: '返回', kind: 'ghost' })
      return actions
    },
  },
}

/**
 * 刻意排除的码（别加回来）：
 * - `PERIOD_CLOSED` → 归 `composables/backfillConfirm.ts`。它有可等待的 Promise 与
 *   重试语义，救援层插一脚会打断 allowBackfill 流程。
 * - `STEP_UP_REQUIRED` → closing-wizard / legacy-import / opening 已有三处成熟短信 UI。
 * - `OPENING_BALANCE_DERIVED` → 纯信息告知，toast 足够。
 * - `BACKFILL_*` → 同 PERIOD_CLOSED，属补记流程。
 */
export const RESCUE_EXCLUDED = [
  'PERIOD_CLOSED',
  'STEP_UP_REQUIRED',
  'OPENING_BALANCE_DERIVED',
] as const
