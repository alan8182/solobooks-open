/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 按业务日期把流水切成「天」。
 *
 * 存在的理由：一张 9000px 长、几十行连排的流水表，用户滚到中段之后
 * 完全不知道自己停在哪一天，只能靠每行重复的日期小字去猜。分组 + 吸顶
 * 组头把这个信息固定在视野里。
 *
 * 约定：
 * - 入参按 occurDate 倒序（最近的在上），与列表的阅读顺序一致；
 *   没有日期的排最后，归到 `''` 组。
 * - 标签用「今天 / 昨天 / 10月18日 / 2025年10月18日」，跨年才带年份。
 * - 星期几单独给，方便组头右对齐做次要信息。
 */

export interface DatedItem {
  occurDate?: string | null
  created_at?: string | null
}

export interface EntryDayGroup<T> {
  /** YYYY-MM-DD；无日期时为空串 */
  date: string
  /** 人化标签：今天 / 昨天 / 10月18日 / 2025年10月18日 */
  label: string
  /** 星期几：周一…周日；无日期时为空串 */
  weekday: string
  items: T[]
}

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

/** 取一条记录的归属日：优先业务日期，其次创建时间 */
export function entryDayKey(item: DatedItem): string {
  const raw = item.occurDate || item.created_at || ''
  return String(raw).slice(0, 10)
}

function pad(n: number): string {
  return String(n).padStart(2, '0')
}

/** 本地时区的今天，YYYY-MM-DD。不能用 toISOString——那是 UTC，晚上会差一天 */
function todayKey(now: Date): string {
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`
}

/**
 * 解析 YYYY-MM-DD 为本地日期。不用 new Date(str)：
 * 那种写法按 UTC 解析，在东八区会把 2026-10-18 变成 10-17。
 */
function parseDayKey(key: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(key)
  if (!m) return null
  return new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]))
}

export function dayLabel(key: string, now = new Date()): { label: string; weekday: string } {
  const d = parseDayKey(key)
  if (!d) return { label: key || '日期未知', weekday: '' }

  const weekday = WEEKDAYS[d.getDay()]
  const today = parseDayKey(todayKey(now))!
  const diffDays = Math.round((today.getTime() - d.getTime()) / 86400000)

  if (diffDays === 0) return { label: '今天', weekday }
  if (diffDays === 1) return { label: '昨天', weekday }
  if (d.getFullYear() === now.getFullYear()) {
    return { label: `${d.getMonth() + 1}月${d.getDate()}日`, weekday }
  }
  return { label: `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`, weekday }
}

/**
 * 分组。保持入参顺序（调用方负责按日期倒序排好），
 * 同日合并，无日期的归到一个空串组并放到最后。
 */
export function groupByDay<T extends DatedItem>(items: T[], now = new Date()): EntryDayGroup<T>[] {
  const map = new Map<string, T[]>()
  for (const item of items) {
    const key = entryDayKey(item)
    const bucket = map.get(key)
    if (bucket) bucket.push(item)
    else map.set(key, [item])
  }

  const groups: EntryDayGroup<T>[] = []
  for (const [date, list] of map) {
    if (date === '') continue
    const { label, weekday } = dayLabel(date, now)
    groups.push({ date, label, weekday, items: list })
  }

  const undated = map.get('')
  if (undated?.length) {
    groups.push({ date: '', label: '日期未知', weekday: '', items: undated })
  }
  return groups
}

/** 按业务日期倒序；无日期的沉底，同日保持原顺序（稳定排序） */
export function sortByDayDesc<T extends DatedItem>(items: T[]): T[] {
  return [...items].sort((a, b) => {
    const ka = entryDayKey(a)
    const kb = entryDayKey(b)
    if (!ka && !kb) return 0
    if (!ka) return 1
    if (!kb) return -1
    return kb.localeCompare(ka)
  })
}
