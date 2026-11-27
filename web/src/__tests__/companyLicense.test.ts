/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import type { LicenseOcrResult } from '@/api/types'
import {
  applyLicensePrefill,
  mapLicenseEntityType,
  type LicenseTargetForm,
} from '@/utils/companyLicense'
import { etaxRegionFromAddress } from '@/utils/etaxPortal'

const BASE: LicenseOcrResult = {
  name: '杭州芥子星辰科技有限公司',
  taxId: '91330110MA2KEHHR0T',
  taxIdValid: true,
  entityType: '有限责任公司(自然人独资)',
  incorporationDate: '2019-05-20',
  address: '浙江省杭州市余杭区仓前街道文一西路 1500 号',
  businessScope: '技术服务',
  legalRepresentative: '张三',
  confidence: 0.92,
  warnings: [],
}

function emptyForm(): LicenseTargetForm {
  return { name: '', taxId: '', type: '', incorporationDate: null, region: '' }
}

describe('执照主体类型映射', () => {
  it('按关键词归并到受支持的类型，内测中的个体户/个独给出提示不自动填入', () => {
    expect(mapLicenseEntityType('有限责任公司(自然人独资)').type).toBe('有限责任公司')
    expect(mapLicenseEntityType('有限责任公司').type).toBe('有限责任公司')

    const sp = mapLicenseEntityType('个人独资企业')
    expect(sp.type).toBe('')
    expect(sp.note).toContain('内测中')
    expect(sp.note).toContain('加微信')

    const ind = mapLicenseEntityType('个体工商户')
    expect(ind.type).toBe('')
    expect(ind.note).toContain('内测中')
    expect(ind.note).toContain('加微信')
  })

  it('股份有限公司不因带「公司」被错归为有限责任公司', () => {
    const mapping = mapLicenseEntityType('股份有限公司(非上市)')
    expect(mapping.type).toBe('')
    expect(mapping.note).toContain('股份有限公司(非上市)')
    expect(mapping.note).toContain('手动选择')
  })

  it('空原文不填也不提示', () => {
    expect(mapLicenseEntityType('')).toEqual({ type: '', raw: '', note: '' })
    expect(mapLicenseEntityType(null).note).toBe('')
  })
})

describe('执照住所 → 注册地', () => {
  it('认得出省份（含不带「省」的写法）', () => {
    expect(etaxRegionFromAddress('浙江省杭州市余杭区文一西路 1500 号')).toBe('浙江省')
    expect(etaxRegionFromAddress('浙江杭州余杭区文一西路')).toBe('浙江省')
    expect(etaxRegionFromAddress('北京市海淀区中关村大街 1 号')).toBe('北京市')
    expect(etaxRegionFromAddress('新疆维吾尔自治区乌鲁木齐市天山区')).toBe('新疆维吾尔自治区')
  })

  it('计划单列市优先按市局落户，不能落到省局', () => {
    expect(etaxRegionFromAddress('广东省深圳市南山区科技园')).toBe('广东省|深圳市')
    expect(etaxRegionFromAddress('浙江宁波市鄞州区')).toBe('浙江省|宁波市')
  })

  it('认不出或为空时留空，交给用户手选', () => {
    expect(etaxRegionFromAddress('')).toBe('')
    expect(etaxRegionFromAddress(null)).toBe('')
    expect(etaxRegionFromAddress('香港特别行政区中西区')).toBe('')
  })
})

describe('识别结果预填新建主体表单', () => {
  it('空表单全部填入，并提示核对推断出的注册地与归并后的类型', () => {
    const { form, filled, kept, notices } = applyLicensePrefill(emptyForm(), BASE)

    expect(form).toMatchObject({
      name: '杭州芥子星辰科技有限公司',
      taxId: '91330110MA2KEHHR0T',
      type: '有限责任公司',
      incorporationDate: '2019-05-20',
      region: '浙江省',
    })
    expect(filled).toEqual(['公司名称', '纳税人识别号', '主体类型', '公司成立日期', '注册地'])
    expect(kept).toEqual([])
    expect(notices.join('；')).toContain('有限责任公司(自然人独资)')
    expect(notices.join('；')).toContain('推断注册地为「浙江省」')
  })

  it('用户已填的字段一个都不覆盖', () => {
    const form: LicenseTargetForm = {
      name: '我自己填的公司',
      taxId: '91330100MA2TEST001',
      type: '个体工商户',
      incorporationDate: '2020-01-01',
      region: '',
    }
    const { form: next, filled, kept } = applyLicensePrefill(form, BASE)

    expect(next.name).toBe('我自己填的公司')
    expect(next.taxId).toBe('91330100MA2TEST001')
    expect(next.type).toBe('个体工商户')
    expect(next.incorporationDate).toBe('2020-01-01')
    // 只有注册地是空的，仍然填入
    expect(next.region).toBe('浙江省')
    expect(filled).toEqual(['注册地'])
    expect(kept).toEqual(['公司名称', '纳税人识别号', '主体类型', '公司成立日期'])
  })

  it('不改动传入的表单对象', () => {
    const form = emptyForm()
    applyLicensePrefill(form, BASE)
    expect(form.name).toBe('')
    expect(form.region).toBe('')
  })

  it('不支持的主体类型留空并提示，不猜一个像的', () => {
    const { form, notices } = applyLicensePrefill(
      { ...emptyForm(), type: '有限责任公司' }, // 表单原有值（下拉默认值）保持不动
      { ...BASE, entityType: '合伙企业(有限合伙)' },
    )
    expect(form.type).toBe('有限责任公司')
    expect(notices.join('；')).toContain('合伙企业(有限合伙)')
  })

  it('识别值与表单值相同不产生「已填/已保留」噪音', () => {
    const { filled, kept } = applyLicensePrefill(
      { ...emptyForm(), type: '有限责任公司' },
      BASE,
    )
    expect(filled).not.toContain('主体类型')
    expect(kept).not.toContain('主体类型')
  })

  it('住所认不出、税号也认不出时明确要求手选', () => {
    const { form, notices } = applyLicensePrefill(emptyForm(), {
      ...BASE,
      taxId: '',
      address: '香港特别行政区中西区皇后大道 1 号',
    })
    expect(form.region).toBe('')
    expect(notices.join('；')).toContain('没从执照住所和统一社会信用代码认出注册地')
  })

  it('住所漏扫时用税号行政区划码兜底，并说明依据', () => {
    // 真实执照：住所在照片右半边被裁掉，模型只抄到税号 913301066680073925
    const { form, notices } = applyLicensePrefill(emptyForm(), {
      ...BASE,
      taxId: '913301066680073925',
      address: '',
    })
    expect(form.region).toBe('浙江省')
    expect(notices.join('；')).toContain('已按统一社会信用代码推断为「浙江省」')
  })

  it('住所已能认出注册地时不以税号覆盖', () => {
    const { form } = applyLicensePrefill(emptyForm(), {
      ...BASE,
      taxId: '91440300MA5ABCDE1X', // 深圳
      address: '浙江省杭州市西湖区西园九路 8 号',
    })
    expect(form.region).toBe('浙江省')
  })

  it('后端 warning 与识别把握低的提示会透出', () => {
    const { notices } = applyLicensePrefill(emptyForm(), {
      ...BASE,
      confidence: 0.4,
      warnings: ['统一社会信用代码校验位不通过，请逐位核对'],
    })
    expect(notices).toContain('统一社会信用代码校验位不通过，请逐位核对')
    expect(notices.join('；')).toContain('识别把握较低')
  })
})
