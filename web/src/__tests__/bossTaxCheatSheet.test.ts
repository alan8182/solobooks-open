/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import BossTaxCheatSheet from '@/components/business/BossTaxCheatSheet.vue'

vi.mock('naive-ui', async (importOriginal) => ({
  ...await importOriginal<typeof import('naive-ui')>(),
  useMessage: () => ({ warning: vi.fn(), error: vi.fn(), success: vi.fn(), info: vi.fn() }),
}))

describe('BossTaxCheatSheet electronic tax bureau cheat sheet', () => {
  const defaultPkg = {
    periodType: 'quarter',
    vat: {
      totalSales: 45200,
      isBelowThreshold: true,
      totalVatPayable: 0,
    },
    cit: {
      totalProfit: 15000,
      preferentialTax: 0,
      preferentialRelief: 3000,
    },
    summary: {
      vatPayable: 0,
      citPayable: 0,
    },
    checklist: [
      // 字段名对齐后端 _checklist 真实输出：name/taxKind + 征期状态合并的 deadlineId
      { taxKind: 'stamp_duty', name: '印花税申报', deadlineId: 'dl-stamp-1', status: 'pending', dueDate: '2026-10-26' },
      { taxKind: 'iit_withholding', name: '个人所得税扣缴申报', deadlineId: 'dl-iit-1', status: 'pending', dueDate: '2026-10-26' },
    ],
  }

  it('renders micro-business tax exemption reassuring banner when sales under 300k', () => {
    const wrapper = mount(BossTaxCheatSheet, {
      props: {
        pkg: defaultPkg,
        period: '2026-Q3',
      },
      // 步骤 4 的个税指引是 RouterLink（GuideTrigger），单测不挂路由表
      global: { stubs: { RouterLink: true } },
    })

    // 免税结论收进增值税步骤标签（hero banner 已并入小抄步骤流）
    expect(wrapper.text()).toContain('免税申报 · 应缴 ¥0.00')
    expect(wrapper.text()).toContain('45,200.00')
    // 免税场景的主表核对要点：销售额自动归入第 10 栏小微企业免税销售额
    expect(wrapper.text()).toContain('第 10 栏【小微企业免税销售额】')
  })

  it('renders step-by-step electronic tax bureau guide and copy amounts', () => {
    const wrapper = mount(BossTaxCheatSheet, {
      props: {
        pkg: defaultPkg,
        period: '2026-Q3',
      },
      // 步骤 4 的个税指引是 RouterLink（GuideTrigger），单测不挂路由表
      global: { stubs: { RouterLink: true } },
    })

    expect(wrapper.text()).toContain('增值税及附加税费申报')
    expect(wrapper.text()).toContain('第 1 栏【本期应税销售额】')
    expect(wrapper.text()).toContain('45,200.00')
    expect(wrapper.text()).toContain('企业所得税季度预缴')
    // 栏次对齐 A200000 真实行号（第 25 实际利润额 / 第 28 减免所得税额）
    expect(wrapper.text()).toContain('第 25 栏【实际利润额】')
    expect(wrapper.text()).toContain('15,000.00')
    expect(wrapper.text()).toContain('第 28 栏【减免所得税额】')
    expect(wrapper.text()).toContain('3,000.00')
  })

  it('renders item names in filed buttons and emits their deadlineId on click', async () => {
    const wrapper = mount(BossTaxCheatSheet, {
      props: {
        pkg: defaultPkg,
        period: '2026-Q3',
      },
      // 步骤 4 的个税指引是 RouterLink（GuideTrigger），单测不挂路由表
      global: { stubs: { RouterLink: true } },
    })

    // 按钮文案必须带出事项名（历史 bug：读不存在的 title 字段，引号里一片空白）
    const markBtn = wrapper.findAll('button').find((b) => b.text().includes('印花税申报'))!
    expect(markBtn.text()).toContain('标记「印花税申报」已申报办结')

    await markBtn.trigger('click')
    expect(wrapper.emitted('markFiled')).toBeDefined()
    // 必须回传 deadlineId（父组件凭它调办结接口）；历史 bug 传的是不存在的 id 字段
    expect(wrapper.emitted('markFiled')![0]).toEqual(['dl-stamp-1'])
  })

  it('puts the social insurance guide next to the pending social task, not on filed rows', () => {
    const makePkg = (status: string) => ({
      ...defaultPkg,
      checklist: [
        ...defaultPkg.checklist,
        { taxKind: 'social_insurance', name: '社保费申报缴纳（8 月）', deadlineId: 'dl-ss-1', status, dueDate: '2026-08-15' },
      ],
    })

    // 未办结的社保行要有「社保怎么申报？」入口（老板按小抄去税局办事）
    const pending = mount(BossTaxCheatSheet, {
      props: { pkg: makePkg('pending'), period: '2026-Q3' },
      global: { stubs: { RouterLink: true } },
    })
    expect(pending.text()).toContain('社保费申报缴纳（8 月）')
    expect(pending.text()).toContain('社保怎么申报？')

    // 已办结的行不再挂指引，避免办完后还引着去申报
    const filed = mount(BossTaxCheatSheet, {
      props: { pkg: makePkg('filed'), period: '2026-Q3' },
      global: { stubs: { RouterLink: true } },
    })
    expect(filed.text()).toContain('社保费申报缴纳（8 月）')
    expect(filed.text()).not.toContain('社保怎么申报？')
  })

  it('displays blocking issues warning box and allows filing VAT, CIT, and exporting financial statements', async () => {
    const pkgWithBlockersAndDeadlines = {
      periodType: 'quarter',
      vat: {
        totalSales: 77.23,
        isBelowThreshold: true,
        totalVatPayable: 0,
        deadline: { deadlineId: 'dl-vat-q2', status: 'pending', dueDate: '2026-07-15' },
      },
      cit: {
        revenue: 77.23,
        cost: 20.0,
        totalProfit: -1200.5,
        preferentialTax: 0,
        preferentialRelief: 0,
        deadline: { deadlineId: 'dl-cit-q2', status: 'pending', dueDate: '2026-07-15' },
      },
      summary: {
        vatPayable: 0,
        citPayable: 0,
        blockingIssues: ['1笔销售缺少发票类型或号码，需确认是否开具专票、放弃免税或未开票'],
      },
      checklist: [
        { taxKind: 'fin_statements', name: '财务报表报送', deadlineId: 'dl-fin-q2', status: 'pending', dueDate: '2026-07-15' },
      ],
    }

    const wrapper = mount(BossTaxCheatSheet, {
      props: {
        pkg: pkgWithBlockersAndDeadlines,
        period: '2026-Q2',
      },
      global: { stubs: { RouterLink: true } },
    })

    // 阻断项已上移到父页面导航卡（「存在阻断」标签）与底稿核对区，小抄本身专注申报步骤，
    // 有阻断时步骤流仍需完整可渲染
    expect(wrapper.text()).toContain('增值税及附加税费申报')

    // 企税必填三项（营业收入、营业成本、利润总额）齐全
    expect(wrapper.text()).toContain('第 1 栏【营业收入】（本年累计）')
    expect(wrapper.text()).toContain('第 2 栏【营业成本】（本年累计）')
    expect(wrapper.text()).toContain('20.00')

    // 增值税、企业所得税、财务报表均可直接在对应步骤办结或导出
    const vatBtn = wrapper.findAll('button').find((b) => b.text().includes('标记「增值税及附加税费」已申报办结'))!
    await vatBtn.trigger('click')
    expect(wrapper.emitted('markFiled')![0]).toEqual(['dl-vat-q2'])

    const citBtn = wrapper.findAll('button').find((b) => b.text().includes('标记「企业所得税预缴」已申报办结'))!
    await citBtn.trigger('click')
    expect(wrapper.emitted('markFiled')![1]).toEqual(['dl-cit-q2'])

    const exportBtn = wrapper.findAll('button').find((b) => b.text().includes('一键导出标准财务三表 (XLS)'))!
    await exportBtn.trigger('click')
    expect(wrapper.emitted('exportXls')).toBeDefined()
  })

  it('orders pending tasks above filed ones and marks done step cards to sink below', () => {
    const wrapper = mount(BossTaxCheatSheet, {
      props: {
        pkg: {
          ...defaultPkg,
          vat: { ...defaultPkg.vat, deadline: { deadlineId: 'dl-vat', status: 'filed' } },
          cit: { ...defaultPkg.cit, deadline: { deadlineId: 'dl-cit', status: 'filed' } },
          checklist: [
            { taxKind: 'fin_statements', name: '财务报表报送', deadlineId: 'dl-fin', status: 'filed' },
            { taxKind: 'stamp_duty', name: '印花税', deadlineId: 'dl-stamp', status: 'filed' },
            { taxKind: 'iit_withholding', name: '个税扣缴（7 月）', deadlineId: 'dl-iit-7', status: 'pending' },
            { taxKind: 'culture_fee', name: '文化事业建设费', deadlineId: 'dl-culture', status: 'waived' },
            { taxKind: 'iit_withholding', name: '个税扣缴（8 月）', deadlineId: 'dl-iit-8', status: 'pending' },
          ],
        },
        period: '2026-Q3',
      },
      global: { stubs: { RouterLink: true } },
    })

    // 步骤 4 仍有未办结事项，不应标为 step-done（保持排在前面）；前三个已办结步骤均标为 step-done（CSS order: 1 下沉）
    const stepCards = wrapper.findAll('.step-card')
    expect(stepCards.slice(0, 3).every((c) => c.classes().includes('step-done'))).toBe(true)
    expect(stepCards[3].classes()).not.toContain('step-done')

    // 其他税种清单内：未完成的排在前面，已办结/免征的沉到后面，且组内保持原顺序
    const titles = wrapper.findAll('.other-task-title strong').map((el) => el.text())
    expect(titles).toEqual(['个税扣缴（7 月）', '个税扣缴（8 月）', '印花税', '文化事业建设费'])
  })

  it('collapses done steps by default with expand toggle and shows celebration when all near-term tasks are done', async () => {
    const wrapper = mount(BossTaxCheatSheet, {
      props: {
        pkg: {
          ...defaultPkg,
          vat: { ...defaultPkg.vat, deadline: { deadlineId: 'dl-vat', status: 'filed' } },
          cit: { ...defaultPkg.cit, deadline: { deadlineId: 'dl-cit', status: 'filed' } },
          checklist: [
            { taxKind: 'fin_statements', name: '财务报表报送', deadlineId: 'dl-fin', status: 'filed' },
            { taxKind: 'stamp_duty', name: '印花税', deadlineId: 'dl-stamp', status: 'filed' },
          ],
        },
        period: '2026-Q3',
      },
      global: { stubs: { RouterLink: true } },
    })

    // 全部近期待办均已办结：顶部展示称赞横幅
    expect(wrapper.find('.all-done-celebration').exists()).toBe(true)
    expect(wrapper.find('.celebration-title').text().length).toBeGreaterThan(0)

    // 已办结卡片默认折叠，不直接铺开增值税第 10 栏指引
    expect(wrapper.text()).not.toContain('第 10 栏【小微企业免税销售额】')

    // 点击增值税卡片的「展开核对」后可回看填报金额，再点「收起」恢复折叠
    const vatCard = wrapper.findAll('.step-card')[1]
    const toggleBtn = vatCard.findAll('button').find((b) => b.text().includes('展开核对'))!
    await toggleBtn.trigger('click')
    expect(wrapper.text()).toContain('第 10 栏【小微企业免税销售额】')
    await toggleBtn.trigger('click')
    expect(wrapper.text()).not.toContain('第 10 栏【小微企业免税销售额】')
  })

  it('renders the third-party demo as a safe external link', () => {
    const wrapper = mount(BossTaxCheatSheet, {
      props: { pkg: defaultPkg, period: '2026-Q3' },
      global: { stubs: { RouterLink: true } },
    })

    const link = wrapper.find('a[href*="h5.hzdykj.cn"]')
    expect(link.exists()).toBe(true)
    expect(link.attributes('target')).toBe('_blank')
    expect(link.attributes('rel')).toContain('noopener')
    expect(link.text()).toContain('第三方')
  })

  it('switches the VAT instruction to the 填表式 form path when vatMode is form', () => {
    const wrapper = mount(BossTaxCheatSheet, {
      props: {
        pkg: {
          ...defaultPkg,
          vat: { ...defaultPkg.vat, uninvoicedPositiveSales: 5000 },
        },
        period: '2026-Q3',
        vatMode: 'form',
      },
      global: { stubs: { RouterLink: true } },
    })

    // 回归：填表式不能再让用户去找「本期销售情况」弹窗
    expect(wrapper.text()).toContain('报表列表')
    expect(wrapper.text()).toContain('附1 第 2 列')
    expect(wrapper.text()).toContain('5,000.00')
    expect(wrapper.text()).not.toContain('在「本期销售情况」弹窗填入')
  })

  it('shows the 减免税明细表 checkpoints for 填表式 above the exemption threshold', () => {
    const wrapper = mount(BossTaxCheatSheet, {
      props: {
        pkg: {
          ...defaultPkg,
          vat: {
            ...defaultPkg.vat,
            totalSales: 350000,
            isBelowThreshold: false,
            totalVatPayable: 3500,
            vatReduction: 7000,
          },
          summary: { ...defaultPkg.summary, vatPayable: 3500 },
        },
        period: '2026-Q3',
        vatMode: 'form',
      },
      global: { stubs: { RouterLink: true } },
    })

    expect(wrapper.text()).toContain('减免税申报明细表')
    expect(wrapper.text()).toContain('7,000.00')
    expect(wrapper.text()).toContain('3,500.00')
  })
})
