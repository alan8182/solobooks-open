/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

const CLOSING_WIZARD_ROUTE = /(^|[\s：:])(\/closing-wizard\?month=(\d{4}-(?:0[1-9]|1[0-2]))(?:&(?:amp;)?step=\d+)?)(?=$|[\s，。；;）)])/gm

/**
 * Older assistant replies often contain a bare closing-wizard path. Markdown
 * treats that as plain text, so convert only this known internal route into a
 * sanitized link at render time.
 */
export function linkifyClosingWizardRoutes(raw: string): string {
  return raw.replace(CLOSING_WIZARD_ROUTE, (_match, prefix: string, route: string) => {
    const normalizedRoute = route.replace(/&amp;/g, '&')
    const href = /(?:[?&])step=\d+/.test(normalizedRoute)
      ? normalizedRoute
      : `${normalizedRoute}&step=3`
    return `${prefix}[${normalizedRoute}](${href})`
  })
}

/**
 * Convert a trusted/rendered closing-wizard link into a local router path.
 * The web application always navigates on its own current origin and only
 * accepts a valid month; other links are left untouched.
 */
export function localClosingWizardPath(rawUrl: string): string | null {
  try {
    const parsed = new URL(rawUrl.replace(/&amp;/g, '&'), 'http://solobooks.local')
    if (parsed.pathname !== '/closing-wizard') return null
    const month = parsed.searchParams.get('month') || ''
    if (!/^\d{4}-(0[1-9]|1[0-2])$/.test(month)) return null
    const query = new URLSearchParams({ month, step: '3' })
    return `${parsed.pathname}?${query.toString()}`
  } catch {
    return null
  }
}
