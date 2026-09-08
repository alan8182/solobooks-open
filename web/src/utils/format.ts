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
