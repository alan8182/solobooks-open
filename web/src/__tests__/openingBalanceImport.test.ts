/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it } from 'vitest'
import * as XLSX from 'xlsx'
import {
  parseOpeningRows,
  parseFixedAssetRows,
  parseOpeningWorkbook,
  sheetToRows,
  suggestMappingTarget,
  isProfitLossCode,
  detectStatementPeriod,
  expectedOpeningPeriod,
  describeExpectedPeriod,
  checkOpeningPeriod,
  buildOpeningTemplateWorkbook,
} from '@/utils/openingBalanceImport'

describe('opening balance Excel import and matching', () => {
  const mockExistingAccounts = [
    { account_code: '1001', account_name: '库存现金' },
    { account_code: '1002', account_name: '银行存款' },
    { account_code: '1122', account_name: '应收账款' },
    { account_code: '2202', account_name: '应付账款' },
    { account_code: '3001', account_name: '实收资本' },
    { account_code: '4104', account_name: '利润分配' },
  ]

  it('matches standard columns with code and separate debit/credit', () => {
    const rows = [
      { '科目代码': '1002', '科目名称': '银行存款', '期初借方': '50000.00', '期初贷方': '' },
      { '科目代码': '3001', '科目名称': '实收资本', '期初借方': '', '期初贷方': '50000.00' },
      { '科目代码': '合计', '科目名称': '总计', '期初借方': '50000.00', '期初贷方': '50000.00' },
    ]

    const result = parseOpeningRows(rows, mockExistingAccounts)
    expect(result.totalDebit).toBe(50000)
    expect(result.totalCredit).toBe(50000)
    expect(result.isBalanced).toBe(true)
    expect(result.difference).toBe(0)
    expect(result.matchedCount).toBe(2)

    const bankAcc = result.items.find(i => i.account_code === '1002')
    expect(bankAcc?.debit_balance).toBe(50000)
    expect(bankAcc?.credit_balance).toBe(0)

    const capitalAcc = result.items.find(i => i.account_code === '3001')
    expect(capitalAcc?.credit_balance).toBe(50000)
  })

  it('resolves accounts by name when code has formatting difference (e.g. 1002.01 or missing code)', () => {
    const rows = [
      { '科目编码': '1002.01', '科目名称': '银行存款', '借方余额': '30000' },
      { '科目编码': '', '科目名称': '库存现金', '借方余额': '5000' },
      { '科目编码': '3001', '科目名称': '实收资本', '贷方余额': '35000' },
    ]

    const result = parseOpeningRows(rows, mockExistingAccounts)
    expect(result.totalDebit).toBe(35000)
    expect(result.totalCredit).toBe(35000)
    expect(result.isBalanced).toBe(true)

    const cashAcc = result.items.find(i => i.account_code === '1001')
    expect(cashAcc?.debit_balance).toBe(5000)
  })

  it('handles direction + single amount column pattern', () => {
    const rows = [
      { '代码': '1002', '名称': '银行存款', '方向': '借', '余额': '20000' },
      { '代码': '3001', '名称': '实收资本', '方向': '贷', '余额': '20000' },
    ]

    const result = parseOpeningRows(rows, mockExistingAccounts)
    expect(result.totalDebit).toBe(20000)
    expect(result.totalCredit).toBe(20000)
    expect(result.isBalanced).toBe(true)
  })

  it('nets debit and credit when both are present on the same account to avoid conflict', () => {
    const rows = [
      { '科目代码': '1122', '科目名称': '应收账款', '借方': '1000', '贷方': '300' },
      { '科目代码': '2202', '科目名称': '应付账款', '借方': '', '贷方': '700' },
    ]

    const result = parseOpeningRows(rows, mockExistingAccounts)
    const arAcc = result.items.find(i => i.account_code === '1122')
    expect(arAcc?.debit_balance).toBe(700)
    expect(arAcc?.credit_balance).toBe(0)
    expect(result.isBalanced).toBe(true)
  })

  it('warns about unmatched accounts and unbalance', () => {
    const rows = [
      { '科目代码': '9999', '科目名称': '未知特定科目', '借方': '888' },
      { '科目代码': '1002', '科目名称': '银行存款', '借方': '100' },
      { '科目代码': '3001', '科目名称': '实收资本', '贷方': '100' },
    ]

    const result = parseOpeningRows(rows, mockExistingAccounts)
    expect(result.unmatchedCount).toBe(1)
    expect(result.warnings.length).toBeGreaterThan(0)
    expect(result.warnings[0]).toContain('未知特定科目')
  })

  it('extracts partner name and splits mid-year expense/revenue YTD items', () => {
    const extendedAccounts = [
      ...mockExistingAccounts,
      { account_code: '5001', account_name: '主营业务收入' },
      { account_code: '5602999', account_name: '管理费用-其他' },
    ]
    const rows = [
      { '科目代码': '1122', '科目名称': '应收账款', '借方': '10000', '客商': '华为技术有限公司' },
      { '科目代码': '2202', '科目名称': '应付账款', '贷方': '10000', '往来单位': '阿里云计算有限公司' },
      // 损益科目
      { '科目代码': '5001', '科目名称': '主营业务收入', '本年累计贷方': '60000' },
      { '科目代码': '5602999', '科目名称': '管理费用-其他', '本年累计借方': '25000' },
    ]

    const result = parseOpeningRows(rows, extendedAccounts)
    expect(result.isBalanced).toBe(true)
    expect(result.totalDebit).toBe(10000)
    expect(result.totalCredit).toBe(10000)

    // 有客商的行现在独立成行（同科目多单位拆多行），按客商找而不是按科目找
    const ar = result.items.find(i => i.partner_name === '华为技术有限公司')
    expect(ar?.account_code).toBe('1122')
    expect(ar?.debit_balance).toBe(10000)

    const ap = result.items.find(i => i.partner_name === '阿里云计算有限公司')
    expect(ap?.account_code).toBe('2202')
    expect(ap?.credit_balance).toBe(10000)

    expect(result.ytdItems.length).toBeGreaterThanOrEqual(2)
    const rev = result.ytdItems.find(i => i.account_code === '5001')
    expect(rev?.credit_balance).toBe(60000)
    const exp = result.ytdItems.find(i => i.account_code === '5602999')
    expect(exp?.debit_balance).toBe(25000)
  })

  it('parses fixed assets rows and multi-sheet workbooks', () => {
    const faRows = [
      { '资产名称': 'MacBook Pro', '资产类别': '电子设备', '购入日期': '2025-06-01', '原值': '15000', '累计折旧': '5000', '预计使用月数': '36', '残值率': '5%' },
      { '资产名称': '办公椅', '资产类别': '家具用具', '原值': '2000', '累计折旧': '400' },
    ]
    const assets = parseFixedAssetRows(faRows)
    expect(assets.length).toBe(2)
    expect(assets[0].name).toBe('MacBook Pro')
    expect(assets[0].cost).toBe(15000)
    expect(assets[0].accumulated_depreciation).toBe(5000)
    expect(assets[0].salvage_rate).toBe(0.05)
    expect(assets[1].cost).toBe(2000)

    // 测试多 Sheet 工作簿
    const wb = XLSX.utils.book_new()
    const ws1 = XLSX.utils.json_to_sheet([
      { '科目代码': '1002', '科目名称': '银行存款', '期初借方': '10000' },
      { '科目代码': '3001', '科目名称': '实收资本', '期初贷方': '10000' },
    ])
    XLSX.utils.book_append_sheet(wb, ws1, '资产负债期初余额')

    const ws2 = XLSX.utils.json_to_sheet(faRows)
    XLSX.utils.book_append_sheet(wb, ws2, '固定资产期初卡片')

    const res = parseOpeningWorkbook(wb, mockExistingAccounts)
    expect(res.isBalanced).toBe(true)
    expect(res.totalDebit).toBe(10000)
    expect(res.fixedAssets.length).toBe(2)
  })

  it('supports custom mapping overrides and captures unmatched rows', () => {
    const rows = [
      { '科目代码': '9999', '科目名称': '特殊往来户', '期初借方': '12000' },
      { '科目代码': '3001', '科目名称': '实收资本', '期初贷方': '12000' },
    ]

    // 未提供 mapping 时，9999 无法匹配
    const unmapped = parseOpeningRows(rows, mockExistingAccounts)
    expect(unmapped.unmatchedCount).toBe(1)
    expect(unmapped.unmatchedRows?.length).toBe(1)
    expect(unmapped.unmatchedRows?.[0].originalCode).toBe('9999')
    expect(unmapped.isBalanced).toBe(false)

    // 提供 mapping 后，9999 映射到 1122 应收账款
    const mapped = parseOpeningRows(rows, mockExistingAccounts, { '9999': '1122' })
    expect(mapped.unmatchedCount).toBe(0)
    expect(mapped.isBalanced).toBe(true)
    const matchedReceivable = mapped.items.find(i => i.account_code === '1122')
    expect(matchedReceivable?.debit_balance).toBe(12000)
  })

  // 根因回归：第三方软件（自记账等）导出的余额表是「上级合计行 + 下级明细行」，
  // 两层都入账会把同一笔金额双重计入期初（金额事故）
  it('keeps the parent rollup row and skips file children to avoid double counting', () => {
    const rows = [
      { '科目代码': '1002', '科目名称': '银行存款', '期初余额借方': '5124.30', '期初余额贷方': '0' },
      { '科目代码': '1002004', '科目名称': '银行存款-招商银行5719', '期初余额借方': '5124.30', '期初余额贷方': '0' },
      { '科目代码': '3001', '科目名称': '实收资本', '期初余额借方': '0', '期初余额贷方': '5124.30' },
    ]

    const result = parseOpeningRows(rows, mockExistingAccounts)
    const bank = result.items.find(i => i.account_code === '1002')
    expect(bank?.debit_balance).toBe(5124.3) // 只入一次，不是 10248.60
    expect(result.totalDebit).toBe(5124.3)
    expect(result.isBalanced).toBe(true)
    expect(result.warnings.some(w => w.includes('已跳过 1 行冗余层级科目行'))).toBe(true)
  })

  // 下级科目能匹配系统科目时，让位规则反转：丢弃上级合计行，保留更细的下级
  it('drops the parent rollup row when a file child matches a system account', () => {
    const accounts = [
      ...mockExistingAccounts,
      { account_code: '220201', account_name: '应付账款-暂估应付款' },
    ]
    const rows = [
      { '科目代码': '2202', '科目名称': '应付账款', '期初余额借方': '0', '期初余额贷方': '3000' },
      { '科目代码': '220201', '科目名称': '应付账款-暂估应付款', '期初余额借方': '0', '期初余额贷方': '3000' },
      { '科目代码': '1002', '科目名称': '银行存款', '期初余额借方': '3000', '期初余额贷方': '0' },
    ]

    const result = parseOpeningRows(rows, accounts)
    const parent = result.items.find(i => i.account_code === '2202')
    const child = result.items.find(i => i.account_code === '220201')
    expect(parent?.credit_balance ?? 0).toBe(0) // 上级让位，不能与下级相加成 6000
    expect(child?.credit_balance).toBe(3000)
    expect(result.totalCredit).toBe(3000)
    expect(result.isBalanced).toBe(true)
  })

  // 根因回归（规则三）：上级匹配不上系统科目（如容器科目 2211 应付职工薪酬，
  // 系统里必须落 221101/221102/221104 子级）时，文件自带的下级明细行才是无损
  // 拆分依据——父级聚合行应被跳过、子级行进映射列表，而不是父级整体去映射
  it('surfaces file children for mapping when the unmatchable parent rolls them up', () => {
    const accounts = [
      ...mockExistingAccounts,
      { account_code: '221101', account_name: '应付职工薪酬-工资' },
      { account_code: '221102', account_name: '应付职工薪酬-单位社保' },
      { account_code: '221104', account_name: '应付职工薪酬-代扣社保公积金' },
    ]
    const rows = [
      { '科目代码': '2211', '科目名称': '应付职工薪酬', '期初余额借方': '0', '期初余额贷方': '15,320.00' },
      { '科目代码': '2211001', '科目名称': '应付职工薪酬-职工薪酬', '期初余额借方': '0', '期初余额贷方': '11,500.00' },
      { '科目代码': '2211002', '科目名称': '应付职工薪酬-职工社保费', '期初余额借方': '0', '期初余额贷方': '2,620.00' },
      { '科目代码': '2211003', '科目名称': '应付职工薪酬-住房公积金', '期初余额借方': '0', '期初余额贷方': '1,200.00' },
      { '科目代码': '1002', '科目名称': '银行存款', '期初余额借方': '15,320.00', '期初余额贷方': '0' },
    ]

    const result = parseOpeningRows(rows, accounts)
    const codes = (result.unmatchedRows || []).map(r => r.originalCode)
    expect(codes).toEqual(['2211001', '2211002', '2211003']) // 父级 2211 不进映射列表
    expect(result.unmatchedRows?.map(r => r.credit)).toEqual([11500, 2620, 1200])
    expect(result.warnings.some(w => w.includes('已跳过 1 行冗余层级科目行'))).toBe(true)
  })

  // 未匹配行要标出「是否影响期初平衡」：损益类只影响累计发生额，把它也算进
  // 差额解释里会让用户去改一张本来就平的资产负债表。
  it('flags unmatched rows that affect the opening balance', () => {
    const rows = [
      { '科目代码': '9999', '科目名称': '历史往来户', '期初余额借方': '0', '期初余额贷方': '500' },
      { '科目代码': '5602021', '科目名称': '管理费用-社保费', '期初余额借方': '100', '期初余额贷方': '100' },
    ]
    const result = parseOpeningRows(rows, mockExistingAccounts)
    const byCode = Object.fromEntries((result.unmatchedRows || []).map(r => [r.originalCode, r]))
    expect(byCode['9999'].affects_balance).toBe(true)
    expect(byCode['5602021'].affects_balance).toBe(false)
  })

  // 规则三边界：下级全为零时聚合行是唯一金额载体，必须保留上级进映射列表
  it('keeps the unmatchable parent when all its file children are zero', () => {
    const rows = [
      { '科目代码': '9999', '科目名称': '历史往来户', '期初余额借方': '0', '期初余额贷方': '500' },
      { '科目代码': '9999001', '科目名称': '历史往来户-明细', '期初余额借方': '0', '期初余额贷方': '0' },
      { '科目代码': '1002', '科目名称': '银行存款', '期初余额借方': '500', '期初余额贷方': '0' },
    ]

    const result = parseOpeningRows(rows, mockExistingAccounts)
    const codes = (result.unmatchedRows || []).map(r => r.originalCode)
    expect(codes).toEqual(['9999'])
    expect(codes).not.toContain('9999001')
  })

  // 根因回归：第三方余额表把往来单位拼在子级科目名里（「应收账款-梧桐树保险经纪
  // 有限公司」），而客商只挂在子级行上。父级合计行若保留、子级被覆盖，老板在账龄
  // 表上就只能看到一个「未指定往来单位」的大组，看不出欠谁钱。
  it('keeps partner-named child rows and drops the redundant rollup parent', () => {
    const rows = [
      { '科目编码': '1122', '科目名称': '应收账款', '期初余额借方': '2157.42', '期初余额贷方': '0' },
      { '科目编码': '1122001', '科目名称': '应收账款-梧桐树保险经纪有限公司', '期初余额借方': '543.30', '期初余额贷方': '0' },
      { '科目编码': '1122010', '科目名称': '应收账款-东方大地（武汉）保险经纪有限公司', '期初余额借方': '1614.12', '期初余额贷方': '0' },
      { '科目编码': '3001', '科目名称': '实收资本', '期初余额借方': '0', '期初余额贷方': '2157.42' },
    ]

    const result = parseOpeningRows(rows, mockExistingAccounts)
    const parents = result.items.filter(i => i.account_code === '1122' && !i.partner_name)
    const withPartner = result.items.filter(i => i.account_code === '1122' && i.partner_name)

    expect(withPartner.map(i => i.partner_name).sort()).toEqual([
      '东方大地（武汉）保险经纪有限公司',
      '梧桐树保险经纪有限公司',
    ])
    // 合计行是子行的纯汇总，不能让两个单位行再加一次
    expect(parents.every(p => p.debit_balance === 0)).toBe(true)
    expect(result.isBalanced).toBe(true)
    expect(result.totalDebit).toBe(2157.42)
  })

  // 净值口径的根因回归：父级期初是**净额**，子行里有借有贷时毛值相加对不上。
  // 曾经用毛值比对，2202 被判「不平」而父行与子行同时保留 → 金额双计。
  // 这里用真实的 2202 数据：子行毛值合计 22574.32，净额 −4469 才等于父级。
  it('matches a parent rollup against children by net amount, not gross', () => {
    const rows = [
      { '科目编码': '2202', '科目名称': '应付账款', '期初余额借方': '0', '期初余额贷方': '4469.00' },
      { '科目编码': '2202002', '科目名称': '应付账款-杭州公瑾财务咨询有限公司', '期初余额借方': '2400.00', '期初余额贷方': '0' },
      { '科目编码': '2202003', '科目名称': '应付账款-深圳市腾讯计算机系统有限公司', '期初余额借方': '0', '期初余额贷方': '600.00' },
      { '科目编码': '2202004', '科目名称': '应付账款-北京九洲科瑞科技有限公司', '期初余额借方': '0', '期初余额贷方': '6249.00' },
      { '科目编码': '2202005', '科目名称': '应付账款-支付宝（中国）网络技术有限公司客户备付金', '期初余额借方': '230.00', '期初余额贷方': '0' },
      { '科目编码': '2202006', '科目名称': '应付账款-河南山南水北人力资源服务有限公司', '期初余额借方': '6422.66', '期初余额贷方': '0' },
      { '科目编码': '2202007', '科目名称': '应付账款-河南山南水北科技有限公司', '期初余额借方': '0', '期初余额贷方': '6422.66' },
      { '科目编码': '2202010', '科目名称': '应付账款-招商银行股份有限公司杭州分行', '期初余额借方': '0', '期初余额贷方': '250.00' },
      { '科目编码': '1002', '科目名称': '银行存款', '期初余额借方': '4469.00', '期初余额贷方': '0' },
    ]

    const result = parseOpeningRows(rows, mockExistingAccounts)
    const parents = result.items.filter(i => i.account_code === '2202' && !i.partner_name)
    const childRows = result.items.filter(i => i.account_code === '2202' && i.partner_name)
    // 父行让位给子行，不能与子行同时计入
    expect(parents.every(p => p.credit_balance === 0 && p.debit_balance === 0)).toBe(true)
    expect(childRows.length).toBe(7)
    // 子行保留借贷两侧（有的往来单位是借方余额），净额必须仍等于父级净额 −4469
    const net = Math.round(childRows.reduce(
      (s, i) => s + i.debit_balance - i.credit_balance, 0,
    ) * 100) / 100
    expect(net).toBe(-4469)
  })

  // 非往来单位后缀不建档案：社保费/公积金/工资是费用税费明细，不是「欠谁钱」的对手方
  it('does not invent partners for non-entity suffixes', () => {
    const accounts = [
      ...mockExistingAccounts,
      { account_code: '2241', account_name: '其他应付款' },
    ]
    const rows = [
      { '科目编码': '2241', '科目名称': '其他应付款', '期初余额借方': '0', '期初余额贷方': '34001.42' },
      { '科目编码': '2241001', '科目名称': '其他应付款-社保费', '期初余额借方': '0', '期初余额贷方': '0' },
      { '科目编码': '2241002', '科目名称': '其他应付款-公积金', '期初余额借方': '0', '期初余额贷方': '0' },
      { '科目编码': '2241037', '科目名称': '其他应付款-工资', '期初余额借方': '0', '期初余额贷方': '0' },
      { '科目编码': '2241038', '科目名称': '其他应付款-杨云飞', '期初余额借方': '0', '期初余额贷方': '35162.42' },
      { '科目编码': '2241040', '科目名称': '其他应付款-李晴', '期初余额借方': '1200.00', '期初余额贷方': '0' },
      { '科目编码': '2241041', '科目名称': '其他应付款-王源源', '期初余额借方': '0', '期初余额贷方': '39.00' },
      { '科目编码': '1002', '科目名称': '银行存款', '期初余额借方': '34001.42', '期初余额贷方': '0' },
    ]

    const result = parseOpeningRows(rows, accounts)
    const names = result.items.filter(i => i.partner_name).map(i => i.partner_name)
    expect(names).toContain('杨云飞')
    expect(names).toContain('李晴')
    expect(names).toContain('王源源')
    expect(names).not.toContain('社保费')
    expect(names).not.toContain('公积金')
    expect(names).not.toContain('工资')
    expect(result.isBalanced).toBe(true)
  })

  it('reads YTD amounts from composite two-tier header columns', () => {
    const accounts = [
      ...mockExistingAccounts,
      { account_code: '5001', account_name: '主营业务收入' },
    ]
    const rows = [
      { '科目代码': '5001', '科目名称': '主营业务收入', '期初余额借方': '0', '期初余额贷方': '0', '本年累计发生额借方': '0', '本年累计发生额贷方': '60000' },
    ]

    const result = parseOpeningRows(rows, accounts)
    const rev = result.ytdItems.find(i => i.account_code === '5001')
    expect(rev?.credit_balance).toBe(60000)
  })

  // 根因回归：自记账等第三方导出的余额表顶部有标题/期间/单位行，真实表头在
  // 第 4-5 行且是两层（「期初余额」合并跨列 + 下层「借方/贷方」）。旧逻辑把
  // 第 1 行当表头，列名全成 __EMPTY，结果 0 匹配 0 金额。
  // 期末列与期初列故意不同：期初建账要的是查询期末（建账前一月末）的时点余额，
  // 取期初会少掉前几个月的资产负债变动。同时必须能抓到合并表头补齐后的列名。
  it('detects shifted two-tier headers in third-party exports and parses opening balances', () => {
    const wb = XLSX.utils.book_new()
    const ws = XLSX.utils.aoa_to_sheet([
      ['科目余额表', '', '', '', '', '', '', '', '', '', ''],
      ['', '', '期间：2026年9月~9月', '', '', '', '', '', '', '', ''],
      ['核算单位: 某公司', '', '', '', '', '', '', '', '', '单位: 元', ''],
      ['科目编码', '科目名称', '期初余额', '', '本期发生额', '', '本年累计发生额', '', '期末余额', '', ''],
      ['', '', '借方', '贷方', '借方', '贷方', '借方', '贷方', '借方', '贷方', ''],
      ['1001', '库存现金', '50.00', '0.00', '0.00', '0.00', '0.00', '0.00', '77.77', '0.00', ''],
      ['1002', '银行存款', '5,124.30', '0.00', '24,289.79', '17,149.00', '158,900.42', '164,963.04', '12,265.09', '0.00', ''],
      ['1002004', '银行存款-招商银行5719', '5,124.30', '0.00', '24,289.79', '17,149.00', '158,900.42', '164,963.04', '12,265.09', '0.00', ''],
      ['2202', '应付账款', '0.00', '2,174.30', '5,000.00', '5,000.00', '20,000.00', '20,000.00', '0.00', '9,999.99', ''],
      ['3001', '实收资本', '0.00', '3,000.00', '0.00', '0.00', '0.00', '0.00', '0.00', '2,342.87', ''],
      ['合计', '', '5,174.30', '5,174.30', '', '', '', '', '', '', ''],
    ])
    XLSX.utils.book_append_sheet(wb, ws, '科目余额表')

    const result = parseOpeningWorkbook(wb, mockExistingAccounts)
    expect(result.matchedCount).toBe(4) // 1001 / 1002 / 2202 / 3001，下级明细已跳过
    expect(result.totalDebit).toBe(12342.86) // 期末借，不是期初的 5174.30
    expect(result.totalCredit).toBe(12342.86) // 期末贷，不是期初的 5174.30
    expect(result.isBalanced).toBe(true)
    const bank = result.items.find(i => i.account_code === '1002')
    expect(bank?.debit_balance).toBe(12265.09) // 期末，不是期初的 5124.30
    const ap = result.items.find(i => i.account_code === '2202')
    expect(ap?.credit_balance).toBe(9999.99) // 期末，不是期初的 2174.30
    expect(result.period).toMatchObject({
      startYear: 2026,
      startMonth: 9,
      endYear: 2026,
      endMonth: 9,
    })
    expect(result.warnings.some(w => w.includes('已跳过 1 行冗余层级科目行'))).toBe(true)
  })

  // 回归：2241 下既有往来单位明细，又有不挂单位的公积金明细。上级合计行
  // 已被层级去重跳过，剩下的公积金明细却被拿去和客商行「对账」，弹窗误报
  // 「科目 2241 的合计行金额与往来单位明细行之和不一致」。
  it('does not warn when a non-partner detail shares the account with partner rows', () => {
    const accounts = [{ account_code: '2241', account_name: '其他应付款' }]
    const rows = [
      { '科目编码': '2241', '科目名称': '其他应付款', '期末余额借方': '0.00', '期末余额贷方': '3,000.00' },
      { '科目编码': '2241001', '科目名称': '其他应付款-甲公司', '期末余额借方': '0.00', '期末余额贷方': '3,500.00' },
      { '科目编码': '2241002', '科目名称': '其他应付款-公积金', '期末余额借方': '500.00', '期末余额贷方': '0.00' },
    ]

    const result = parseOpeningRows(rows, accounts)
    const plain = result.items.find(i => i.account_code === '2241' && !i.partner_name)
    expect(plain?.debit_balance).toBe(500)
    expect(result.items.filter(i => i.partner_name).map(i => i.partner_name)).toEqual(['甲公司'])
    expect(result.warnings.some(w => w.includes('2241') && w.includes('合计行'))).toBe(false)
  })

  // 旧账导入 Tab 复用同一个表头识别：此前它拿第一行（"科目余额表"）当表头，
  // 真实导出的 79 行全变成 __EMPTY_*，后端按空行跳过——看起来就是「没功能」。
  it('locates the real header row for the legacy import tab', () => {
    const ws = XLSX.utils.aoa_to_sheet([
      ['科目余额表', '', '', '', '', ''],
      ['', '', '期间：2026年9月~9月', '', '', ''],
      ['核算单位: 某公司', '', '', '', '', ''],
      ['科目编码', '科目名称', '期初余额', '', '本年累计发生额', ''],
      ['', '', '借方', '贷方', '借方', '贷方'],
      ['1002', '银行存款', '5,000.00', '0.00', '1,200.00', '0.00'],
    ])

    const rows = sheetToRows(ws, { raw: false })
    expect(rows).toHaveLength(1)
    expect(rows[0]['科目编码']).toBe('1002')
    expect(rows[0]['期初余额借方']).toBe('5,000.00')
    expect(rows[0]['本年累计发生额借方']).toBe('1,200.00')
  })
})

describe('mapping suggestions from legacy account names', () => {
  // 镜像系统科目表（chart_of_accounts.json）的叶子子集
  const chartLeaves = [
    { account_code: '221101', account_name: '应付职工薪酬-工资' },
    { account_code: '221102', account_name: '应付职工薪酬-单位社保' },
    { account_code: '221103', account_name: '应付职工薪酬-职工福利费' },
    { account_code: '221104', account_name: '应付职工薪酬-代扣社保公积金' },
    { account_code: '221105', account_name: '应付职工薪酬-住房公积金' },
    { account_code: '222100101', account_name: '进项税额' },
    { account_code: '222100105', account_name: '销项税额' },
    { account_code: '2221006', account_name: '应交所得税' },
    { account_code: '2221008', account_name: '应交城市维护建设税' },
    { account_code: '5602014', account_name: '管理费用-职工福利费' },
    { account_code: '5602015', account_name: '管理费用-信息技术服务费' },
    { account_code: '5602016', account_name: '管理费用-单位社保' },
    { account_code: '5602017', account_name: '管理费用-住房公积金' },
    { account_code: '5602999', account_name: '管理费用-其他' },
  ]

  it('disambiguates same-named leaves by source code family (社保 → 负债侧 or 费用侧)', () => {
    expect(suggestMappingTarget('2211002', '应付职工薪酬-职工社保费', chartLeaves)).toBe('221102')
    expect(suggestMappingTarget('5602021', '管理费用-社保费', chartLeaves)).toBe('5602016')
  })

  it('suggests VAT leaves by name keyword, then by balance direction for bare 增值税', () => {
    expect(suggestMappingTarget('2221001005', '应交税费-应交增值税-销项税额', chartLeaves)).toBe('222100105')
    expect(suggestMappingTarget('2221001', '应交税费-应交增值税', chartLeaves, 'debit')).toBe('222100101')
    expect(suggestMappingTarget('2221001', '应交税费-应交增值税', chartLeaves, 'credit')).toBe('222100105')
    expect(suggestMappingTarget('2221001', '应交税费-应交增值税', chartLeaves)).toBeUndefined()
  })

  // 回归：子串命中会同时带出「销项税额」与「待转销项税额」，精确同名必须优先
  it('prefers the exact-name leaf over near-name hits', () => {
    const withPendingVat = [
      ...chartLeaves,
      { account_code: '2221021', account_name: '待转销项税额' },
    ]
    expect(suggestMappingTarget('2221001005', '应交税费-应交增值税-销项税额', withPendingVat)).toBe('222100105')
  })

  it('maps welfare and tech-service expense leaves to the standard chart', () => {
    expect(suggestMappingTarget('5602038', '管理费用-员工福利费', chartLeaves)).toBe('5602014')
    expect(suggestMappingTarget('5602028', '管理费用-技术服务费', chartLeaves)).toBe('5602015')
  })

  it('maps income tax by name even when the legacy code differs', () => {
    expect(suggestMappingTarget('2221006', '应交税费-应交企业所得税', chartLeaves)).toBe('2221006')
  })

  // 公积金已补齐标准科目（221105 负债侧单位公积金 / 5602017 费用侧），
  // 家族消歧按来源编码把同名叶子拆到正确侧
  it('suggests housing fund leaves per source code family', () => {
    expect(suggestMappingTarget('2211003', '应付职工薪酬-住房公积金', chartLeaves)).toBe('221105')
    expect(suggestMappingTarget('5602022', '管理费用-住房公积金', chartLeaves)).toBe('5602017')
  })

  it('returns undefined for unrelated names', () => {
    expect(suggestMappingTarget('8888', '莫名其妙科目', chartLeaves)).toBeUndefined()
  })
})

// 根因回归：损益类判定曾用硬编码枚举，漏掉明细科目后收入被分进资产负债期初桶，
// 而页面 isBalanceAccount 又按前缀把它滤掉——金额凭空消失、试算差额对不上。
// 判定必须与后端 is_profit_loss_code 同口径：5xxx 全段，4301 费用化损益、资本化资产。
describe('profit/loss classification matches the backend single source of truth', () => {
  it('treats the whole 5xxx segment as profit/loss, including detail leaves', () => {
    for (const code of ['5001', '5001001', '5051', '5301012', '5403001', '5602028', '5603001', '5801']) {
      expect(isProfitLossCode(code), `${code} 应判为损益`).toBe(true)
    }
  })

  it('splits 4301 into expensed (profit/loss) and capitalized (asset)', () => {
    expect(isProfitLossCode('4301')).toBe(true)
    expect(isProfitLossCode('430100101')).toBe(true)
    // 资本化支出尚未转入无形资产，不进当期损益，必须留在资产负债桶
    expect(isProfitLossCode('4301002')).toBe(false)
  })

  it('keeps balance-sheet accounts out of the profit/loss bucket', () => {
    for (const code of ['1001', '1122', '2202', '3103', '221105', '']) {
      expect(isProfitLossCode(code), `${code} 不应判为损益`).toBe(false)
    }
  })

  it('routes a 5xxx income leaf into ytdItems instead of the balance sheet', () => {
    const accounts = [
      { account_code: '5001001', account_name: '咨询服务' },
      { account_code: '1001', account_name: '库存现金' },
    ]
    const rows = [
      { '科目代码': '5001001', '科目名称': '咨询服务', '本年累计借方发生额': '156324.12', '本年累计贷方发生额': '156324.12' },
      { '科目代码': '1001', '科目名称': '库存现金', '期初余额借方': '50', '期初余额贷方': '0' },
    ]

    const result = parseOpeningRows(rows, accounts)
    expect(result.ytdItems.some(i => i.account_code === '5001001')).toBe(true)
    expect(result.items.some(i => i.account_code === '5001001')).toBe(false)
    // 资产负债期初只含真实资产，不被收入撑大
    expect(result.totalDebit).toBe(50)
  })
})

describe('opening balance column selection', () => {
  const accounts = [
    { account_code: '1002', account_name: '银行存款' },
    { account_code: '3001', account_name: '实收资本' },
  ]

  it('prefers ending balance columns over opening columns', () => {
    const rows = [
      { '科目代码': '1002', '科目名称': '银行存款', '期初余额借方': '100', '期初余额贷方': '0', '期末余额借方': '150', '期末余额贷方': '0' },
      { '科目代码': '3001', '科目名称': '实收资本', '期初余额借方': '0', '期初余额贷方': '100', '期末余额借方': '0', '期末余额贷方': '150' },
    ]

    const result = parseOpeningRows(rows, accounts)
    expect(result.totalDebit).toBe(150)
    expect(result.items.find(i => i.account_code === '1002')?.debit_balance).toBe(150)
    expect(result.items.find(i => i.account_code === '3001')?.credit_balance).toBe(150)
  })

  it('falls back to opening columns when ending columns are absent', () => {
    const rows = [
      { '科目代码': '1002', '科目名称': '银行存款', '期初余额借方': '100', '期初余额贷方': '0' },
      { '科目代码': '3001', '科目名称': '实收资本', '期初余额借方': '0', '期初余额贷方': '100' },
    ]

    const result = parseOpeningRows(rows, accounts)
    expect(result.totalDebit).toBe(100)
    expect(result.items.find(i => i.account_code === '1002')?.debit_balance).toBe(100)
  })

  it('reads profit/loss amounts from accumulated columns even when period-end balance differs', () => {
    const pnlAccounts = [{ account_code: '5001', account_name: '主营业务收入' }]
    // 未结转损益时期末余额可能带着历史残留；本年累计发生额才是建账要的口径。
    const rows = [
      { '科目代码': '5001', '科目名称': '主营业务收入', '期末余额贷方': '99999', '本年累计发生额贷方': '60000' },
    ]

    const result = parseOpeningRows(rows, pnlAccounts)
    const revenue = result.ytdItems.find(i => i.account_code === '5001')
    expect(revenue?.credit_balance).toBe(60000)
  })
})

describe('opening statement period detection and validation', () => {
  function statementSheet(meta: string[]): XLSX.WorkSheet {
    return XLSX.utils.aoa_to_sheet([
      ['科目余额表', '', '', '', '', ''],
      meta,
      ['科目编码', '科目名称', '期末余额', '', '本年累计发生额', ''],
      ['', '', '借方', '贷方', '借方', '贷方'],
      ['1002', '银行存款', '100.00', '0.00', '100.00', '0.00'],
    ])
  }

  it('detects labeled, bare and same-year-short period ranges', () => {
    const cases: Array<[string[], Record<string, number>]> = [
      [['期间：2026年1月-2026年5月'], { startYear: 2026, startMonth: 1, endYear: 2026, endMonth: 5 }],
      [['2026-01至2026-05'], { startYear: 2026, startMonth: 1, endYear: 2026, endMonth: 5 }],
      [['2026/01/01-2026/05/31'], { startYear: 2026, startMonth: 1, endYear: 2026, endMonth: 5 }],
      [['2026年9月~9月'], { startYear: 2026, startMonth: 9, endYear: 2026, endMonth: 9 }],
      [['期间：2026年9月'], { startYear: 2026, startMonth: 9, endYear: 2026, endMonth: 9 }],
    ]

    for (const [meta, expected] of cases) {
      expect(detectStatementPeriod(statementSheet(meta)), meta[0]).toMatchObject(expected)
    }
  })

  it('returns null when the header carries no period', () => {
    expect(detectStatementPeriod(statementSheet(['核算单位：某公司']))).toBeNull()
  })

  it('accepts a mid-year statement covering January through the prior month', () => {
    const period = { startYear: 2026, startMonth: 1, endYear: 2026, endMonth: 5, label: '' }
    expect(checkOpeningPeriod(period, 2026, '06').ok).toBe(true)
  })

  it('rejects a statement ending at the start month', () => {
    const period = { startYear: 2026, startMonth: 1, endYear: 2026, endMonth: 6, label: '' }
    const check = checkOpeningPeriod(period, 2026, '06')

    expect(check.ok).toBe(false)
    if (!check.ok) {
      expect(check.reason).toBe('end_mismatch')
      expect(check.message).toContain('6 月会被算两遍')
    }
  })

  it('rejects a mid-year statement that does not start in January', () => {
    const lateStart = { startYear: 2026, startMonth: 2, endYear: 2026, endMonth: 5, label: '' }
    const lateCheck = checkOpeningPeriod(lateStart, 2026, '06')
    expect(lateCheck.ok).toBe(false)
    if (!lateCheck.ok) expect(lateCheck.reason).toBe('start_mismatch')

    const crossYear = { startYear: 2025, startMonth: 6, endYear: 2026, endMonth: 5, label: '' }
    const crossCheck = checkOpeningPeriod(crossYear, 2026, '06')
    expect(crossCheck.ok).toBe(false)
    if (!crossCheck.ok) {
      expect(crossCheck.reason).toBe('start_mismatch')
      expect(crossCheck.message).toContain('多算')
    }
  })

  it('accepts a prior-December statement for a January start', () => {
    const december = { startYear: 2025, startMonth: 12, endYear: 2025, endMonth: 12, label: '' }
    expect(checkOpeningPeriod(december, 2026, '01').ok).toBe(true)

    const january = { startYear: 2026, startMonth: 1, endYear: 2026, endMonth: 1, label: '' }
    const check = checkOpeningPeriod(january, 2026, '01')
    expect(check.ok).toBe(false)
    if (!check.ok) expect(check.message).toContain('上年 12 月')
  })

  it('asks for a cutoff month when the period cannot be detected', () => {
    const check = checkOpeningPeriod(null, 2026, '06')
    expect(check.ok).toBe(false)
    if (!check.ok) {
      expect(check.reason).toBe('unknown')
      expect(describeExpectedPeriod(check.expected)).toBe('2026-01 ～ 2026-05')
    }
    expect(describeExpectedPeriod(expectedOpeningPeriod(2026, '01'))).toBe('2025-12 月末')
  })

  it('exports ending-balance column names in the opening template', () => {
    const wb = buildOpeningTemplateWorkbook(
      [
        { account_code: '1002', account_name: '银行存款' },
        { account_code: '5001', account_name: '主营业务收入' },
      ],
      2026,
      '2026-06',
    )
    const header = (XLSX.utils.sheet_to_json(wb.Sheets['资产负债期初余额'], { header: 1 }) as unknown[][])[0]
    expect(header).toContain('期末余额借方')
    expect(header).toContain('期末余额贷方')
    expect(header).not.toContain('期初借方余额')
  })
})

/**
 * 报表「年初余额」列的数据来源。
 *
 * 回归的是报送表年初列与原始资产负债表对不上：年中建账时建账期初是 9 月 30 日
 * 余额，报表却拿它填 1 月 1 日。年初数必须来自文件「年初余额」列，或按
 * 科目余额表恒等式「期末 = 年初 + 本年累计」反推，两者都没有就留空。
 */
describe('年初余额快照', () => {
  const accounts = [
    { account_code: '1002', account_name: '银行存款' },
    { account_code: '2202', account_name: '应付账款' },
    { account_code: '3001', account_name: '实收资本' },
    { account_code: '5001', account_name: '主营业务收入' },
  ]

  it('没有年初列时按「期末 − 本年累计」推算，损益科目不参与', () => {
    const rows = [
      {
        '科目编码': '1002', '科目名称': '银行存款',
        '本年累计发生额借方': '10000.00', '本年累计发生额贷方': '16062.62',
        '期末余额借方': '12315.09', '期末余额贷方': '',
      },
      {
        '科目编码': '2202', '科目名称': '应付账款',
        '本年累计发生额借方': '5000.00', '本年累计发生额贷方': '0',
        '期末余额借方': '', '期末余额贷方': '10000.00',
      },
      {
        '科目编码': '3001', '科目名称': '实收资本',
        '本年累计发生额借方': '0', '本年累计发生额贷方': '0',
        '期末余额借方': '', '期末余额贷方': '2315.09',
      },
      {
        '科目编码': '5001', '科目名称': '主营业务收入',
        '本年累计发生额借方': '0', '本年累计发生额贷方': '500000.00',
        '期末余额借方': '', '期末余额贷方': '',
      },
    ]
    const result = parseOpeningRows(rows, accounts)
    expect(result.yearStartSource).toBe('derived')

    const cash = result.yearStartItems?.find(i => i.account_code === '1002')
    // 12,315.09 − (10,000.00 − 16,062.62) = 18,377.71
    expect(cash?.debit_balance).toBeCloseTo(18377.71, 2)
    expect(cash?.credit_balance).toBe(0)

    const payable = result.yearStartItems?.find(i => i.account_code === '2202')
    // 10,000.00 贷 − 5,000.00 借 = 期初 15,000.00 贷
    expect(payable?.credit_balance).toBeCloseTo(15000, 2)
    expect(payable?.debit_balance).toBe(0)

    expect(result.yearStartItems?.some(i => i.account_code === '5001')).toBe(false)
  })

  it('文件自带「年初余额」列时直接用，不再减本年累计', () => {
    const rows = [
      {
        '科目编码': '1002', '科目名称': '银行存款',
        '年初余额借方': '18377.71', '年初余额贷方': '',
        '本年累计发生额借方': '10000.00', '本年累计发生额贷方': '16062.62',
        '期末余额借方': '12315.09', '期末余额贷方': '',
      },
      {
        '科目编码': '3001', '科目名称': '实收资本',
        '年初余额借方': '', '年初余额贷方': '18377.71',
        '本年累计发生额借方': '6062.62', '本年累计发生额贷方': '0',
        '期末余额借方': '', '期末余额贷方': '12315.09',
      },
    ]
    const result = parseOpeningRows(rows, accounts)
    expect(result.yearStartSource).toBe('explicit')
    expect(result.yearStartItems?.find(i => i.account_code === '1002')?.debit_balance).toBeCloseTo(18377.71, 2)
    expect(result.yearStartItems?.find(i => i.account_code === '3001')?.credit_balance).toBeCloseTo(18377.71, 2)
  })

  it('既无年初列也无本年累计列时不产出年初数，并提示报表年初列留空', () => {
    const rows = [
      { '科目编码': '1002', '科目名称': '银行存款', '期末余额借方': '100', '期末余额贷方': '' },
      { '科目编码': '3001', '科目名称': '实收资本', '期末余额借方': '', '期末余额贷方': '100' },
    ]
    const result = parseOpeningRows(rows, accounts)
    expect(result.yearStartItems).toBeUndefined()
    expect(result.yearStartSource).toBeNull()
    expect(result.warnings.some(w => w.includes('年初'))).toBe(true)
  })

  it('合计行与客商行同时存在时，年初数只计一次', () => {
    const rows = [
      {
        '科目编码': '2202', '科目名称': '应付账款',
        '本年累计发生额借方': '0', '本年累计发生额贷方': '0',
        '期末余额借方': '', '期末余额贷方': '15000.00',
      },
      {
        '科目编码': '2202001', '科目名称': '应付账款-甲公司',
        '本年累计发生额借方': '0', '本年累计发生额贷方': '0',
        '期末余额借方': '', '期末余额贷方': '10000.00',
      },
      {
        '科目编码': '2202002', '科目名称': '应付账款-乙公司',
        '本年累计发生额借方': '0', '本年累计发生额贷方': '0',
        '期末余额借方': '', '期末余额贷方': '5000.00',
      },
    ]
    const result = parseOpeningRows(rows, accounts)
    const payable = result.yearStartItems?.filter(i => i.account_code === '2202') || []
    // 合计行被清零，只留两个客商行（报表要靠客商判断方向）
    expect(payable).toHaveLength(2)
    expect(payable.map(i => i.partner_name).sort()).toEqual(['乙公司', '甲公司'])
    // 合计行 15,000 + 客商行 15,000 若都算就是 30,000
    expect(payable.reduce((s, i) => s + i.credit_balance, 0)).toBeCloseTo(15000, 2)
  })
})
