/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { afterEach, describe, expect, it } from 'vitest'
import { applySeo } from '@/router/seo'

function routeJsonLd() {
  const el = document.getElementById('seo-jsonld')
  return el?.textContent ? JSON.parse(el.textContent) : null
}

afterEach(() => {
  document.getElementById('seo-jsonld')?.remove()
  document.querySelector('link[rel="canonical"]')?.remove()
  document.querySelector('meta[name="robots"]')?.remove()
})

describe('路由级 JSON-LD', () => {
  it('官网首页输出 FAQPage', () => {
    applySeo({ name: 'Home', path: '/' })
    const data = routeJsonLd()
    expect(data?.['@type']).toBe('FAQPage')
    expect(data?.mainEntity?.length).toBeGreaterThan(0)
  })

  it('免费专题页输出 FAQPage 与两个 0 元 Offer', () => {
    applySeo({ name: 'FreeSoftware', path: '/free' })
    const data = routeJsonLd()
    const nodes = data?.['@graph'] ?? []
    const app = nodes.find((node: { '@type': string }) => node['@type'] === 'SoftwareApplication')
    expect(app?.offers).toHaveLength(2)
    expect(app?.offers.every((offer: { price: string }) => offer.price === '0')).toBe(true)
    expect(nodes.some((node: { '@type': string }) => node['@type'] === 'FAQPage')).toBe(true)
    expect(document.querySelector('link[rel="canonical"]')?.getAttribute('href')).toBe(
      'https://solobooks.cn/free',
    )
  })

  it('没有路由级结构化数据的页面会移除上一次注入', () => {
    applySeo({ name: 'Home', path: '/' })
    expect(routeJsonLd()).not.toBeNull()
    applySeo({ name: 'Login', path: '/login' })
    expect(routeJsonLd()).toBeNull()
  })
})
