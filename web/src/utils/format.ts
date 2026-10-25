/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import dayjs from 'dayjs'

export function formatMoney(amount: number): string {
  const isNegative = amount < 0
  const abs = Math.abs(amount)
  const formatted = abs.toLocaleString('zh-CN', {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })
  return `${isNegative ? '-' : ''}¥${formatted}`
}

/**
 * 金额转人民币大写（凭证合计行用）：19622.55 → 壹万玖仟陆佰贰拾贰圆伍角伍分
 * ponytail: 整数支持到万亿、两位小数（toFixed 四舍五入），凭证合计场景足够
 */
export function amountToChineseCapital(amount: number): string {
  const digits = '零壹贰叁肆伍陆柒捌玖'
  const smallUnits = ['', '拾', '佰', '仟']
  const bigUnits = ['', '万', '亿', '万亿']
  const value = Math.abs(Number(amount) || 0)
  const [intRaw, dec = '00'] = value.toFixed(2).split('.')
  const intStr = String(Number(intRaw))
  const jiao = Number(dec[0])
  const fen = Number(dec[1])

  // 整数部分按 4 位一组，从高位组往低位组拼
  const groups: string[] = []
  let rest = intStr
  while (rest.length > 4) {
    groups.unshift(rest.slice(-4))
    rest = rest.slice(0, -4)
  }
  groups.unshift(rest)

  let intText = ''
  groups.forEach((group, gi) => {
    let groupText = ''
    let zeroPending = false
    for (let i = 0; i < group.length; i++) {
      const d = Number(group[i])
      if (d === 0) {
        zeroPending = true
        continue
      }
      if (zeroPending && (intText || groupText)) groupText += '零'
      zeroPending = false
      groupText += digits[d] + smallUnits[group.length - 1 - i]
    }
    if (groupText) intText += groupText + bigUnits[groups.length - 1 - gi]
  })

  let decText = ''
  if (jiao === 0 && fen === 0) {
    decText = '整'
  } else {
    if (jiao > 0) decText += digits[jiao] + '角'
    else if (fen > 0 && intText) decText += '零'
    if (fen > 0) decText += digits[fen] + '分'
  }

  const sign = Number(amount) < 0 ? '负' : ''
  return `${sign}${intText || '零'}圆${decText}`
}

export function formatDate(date: string | Date, format = 'YYYY-MM-DD'): string {
  return dayjs(date).format(format)
}

export function formatDateTime(date: string | Date): string {
  return dayjs(date).format('YYYY-MM-DD HH:mm')
}

export function formatMonth(date: string | Date): string {
  return dayjs(date).format('YYYY年M月')
}

export function getCurrentMonth(): string {
  return dayjs().format('YYYY-MM')
}

export function getMonthRange(month: string): { start: string; end: string } {
  const start = dayjs(month + '-01').format('YYYY-MM-DD')
  const end = dayjs(month + '-01').endOf('month').format('YYYY-MM-DD')
  return { start, end }
}
