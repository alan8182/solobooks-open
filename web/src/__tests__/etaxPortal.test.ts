/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import { resolveGuideEntry } from '@/composables/useGuideEntry'
import { getGuide } from '@/content/guides'
import {
  ETAX_REGIONS,
  etaxPortalUrl,
  etaxRegionFromTaxId,
  etaxRegionKey,
  etaxRegionLabel,
  etaxRegionSelectOptions,
} from '@/utils/etaxPortal'

describe('电子税务局地址解析', () => {
  it('省本级按省拼音拼子域名', () => {
    expect(etaxPortalUrl('浙江省', '')).toBe('https://etax.zhejiang.chinatax.gov.cn')
    expect(etaxPortalUrl('北京市', '')).toBe('https://etax.beijing.chinatax.gov.cn')
    expect(etaxPortalUrl('新疆维吾尔自治区', '')).toBe('https://etax.xinjiang.chinatax.gov.cn')
  })

  it('计划单列市走市局站点，不跟省局混用', () => {
    // 深圳/宁波/厦门/青岛/大连有独立电子税务局，跳省局会让企业登录后找不到自己
    expect(etaxPortalUrl('广东省', '深圳市')).toBe('https://etax.shenzhen.chinatax.gov.cn')
    expect(etaxPortalUrl('浙江省', '宁波市')).toBe('https://etax.ningbo.chinatax.gov.cn')
    expect(etaxPortalUrl('福建省', '厦门市')).toBe('https://etax.xiamen.chinatax.gov.cn')
    expect(etaxPortalUrl('山东省', '青岛市')).toBe('https://etax.qingdao.chinatax.gov.cn')
    expect(etaxPortalUrl('辽宁省', '大连市')).toBe('https://etax.dalian.chinatax.gov.cn')
  })

  it('注册地缺失或不在表里时不给地址，由调用方回落省份地图', () => {
    expect(etaxPortalUrl('', '')).toBeNull()
    expect(etaxPortalUrl(null, null)).toBeNull()
    expect(etaxPortalUrl('火星省', '')).toBeNull()
    // 只有市没有省：拼不出合法键
    expect(etaxPortalUrl('', '深圳市')).toBeNull()
  })

  it('注册地键与显示名成对可逆', () => {
    expect(etaxRegionKey('浙江省', '宁波市')).toBe('浙江省|宁波市')
    expect(etaxRegionKey('浙江省', '')).toBe('浙江省')
    expect(etaxRegionLabel('浙江省', '宁波市')).toBe('浙江省 · 宁波市')
    expect(etaxRegionLabel('火星省', '')).toBeNull()
  })

  it('下拉是完整地名的扁平列表，选中后能看出是哪个省', () => {
    const options = etaxRegionSelectOptions()
    expect(options).toHaveLength(36)
    const labels = options.map((item) => item.label)
    expect(labels).toContain('浙江省')
    expect(labels).toContain('浙江省 · 宁波市')
    // 每个选项都能反解出唯一站点，避免下拉里出现死选项
    ETAX_REGIONS.forEach((region) => {
      expect(etaxPortalUrl(region.province, region.city)).toContain(region.subdomain)
    })
  })
})

describe('统一社会信用代码兜底注册地', () => {
  it('第 3~8 位行政区划码给出省份', () => {
    // 913301066680073925：330106 = 杭州市西湖区
    expect(etaxRegionFromTaxId('913301066680073925')).toBe('浙江省')
    expect(etaxRegionFromTaxId('91110108MA01ABCD12')).toBe('北京市')
  })

  it('计划单列市按前 4 位落到市局', () => {
    expect(etaxRegionFromTaxId('91440300MA5ABCDE1X')).toBe('广东省|深圳市')
    expect(etaxRegionFromTaxId('91330203MA2ABCDE1X')).toBe('浙江省|宁波市')
  })

  it('非 18 位或非数字区间一律不猜', () => {
    expect(etaxRegionFromTaxId('330106000123456')).toBe('')
    expect(etaxRegionFromTaxId('')).toBe('')
    expect(etaxRegionFromTaxId(null)).toBe('')
    expect(etaxRegionFromTaxId('91ABCDEFGHIJKLMNOP')).toBe('')
  })
})

describe('指引入口按主体注册地解析', () => {
  const entry = getGuide('invoice-list')!.entry!

  it('主体填了注册地：直接给本省站点，不用再选一次', () => {
    const resolved = resolveGuideEntry(entry, { province: '浙江省', city: '宁波市' })!
    expect(resolved.url).toBe('https://etax.ningbo.chinatax.gov.cn')
    expect(resolved.hint).toBe('浙江省 · 宁波市电子税务局')
  })

  it('主体没填注册地：回落到税局省份地图', () => {
    const resolved = resolveGuideEntry(entry, { province: '', city: '' })!
    expect(resolved.url).toBe('https://12366.chinatax.gov.cn/bsfw/onlinetaxation/main')
    expect(resolved.hint).toBe(entry.hint)
  })

  it('不按注册地解析的入口原样返回', () => {
    const plain = { label: '去办理', url: 'https://example.gov.cn' }
    expect(resolveGuideEntry(plain, { province: '浙江省' })).toEqual(plain)
    expect(resolveGuideEntry(undefined, { province: '浙江省' })).toBeNull()
  })
})
