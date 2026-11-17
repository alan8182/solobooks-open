// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
// Licensed under the SoloBooks License v2.0.

import { describe, expect, it } from 'vitest'
import * as XLSX from 'xlsx'
import { parseBalanceFile, parseBalanceFileWithPreview } from '@/api/healthScan'

function makeFile(rows: unknown[][], sheetName = '科目余额表'): File {
  const ws = XLSX.utils.aoa_to_sheet(rows)
  const wb = XLSX.utils.book_new()
  XLSX.utils.book_append_sheet(wb, ws, sheetName)
  const out = XLSX.write(wb, { bookType: 'xlsx', type: 'array' }) as ArrayBuffer
  return new File([out], 'balance.xlsx')
}

describe('parseBalanceFile 公开工具健壮性（真实导出格式回归）', () => {
  it('解析两级合并表头导出（大标题+期间+核算单位+借/贷子行），取期末余额列并剔除合计行', async () => {
    // 复刻 SoloBooks 自家「科目余额表」导出版式：合并大标题行 + 两级表头 + 千分位金额 + 合计行
    const rows = [
      ['科目余额表', '', '', '', '', '', '', '', '', '', ''],
      ['', '', '期间：2026年9月~9月', '', '', '', '', '', '', '', ''],
      ['核算单位: 某某网络科技有限公司', '', '', '', '', '', '', '', '', '单位: 元', ''],
      ['科目编码', '科目名称', '期初余额', '', '本期发生额', '', '本年累计发生额', '', '期末余额', '', ''],
      ['', '', '借方', '贷方', '借方', '贷方', '借方', '贷方', '借方', '贷方', ''],
      ['1001', '库存现金', '50.00', '0.00', '0.00', '0.00', '0.00', '0.00', '50.00', '0.00', ''],
      ['1002', '银行存款', '5,124.30', '0.00', '24,289.79', '17,149.00', '158,900.42', '164,963.04', '12,265.09', '0.00', ''],
      ['1221', '其他应收款-张三丰', '0.00', '0.00', '80,000.00', '0.00', '80,000.00', '0.00', '80,000.00', '0.00', ''],
      ['4001', '实收资本', '0.00', '0.00', '0.00', '0.00', '0.00', '0.00', '0.00', '100,000.00', ''],
      ['合计', '', '5,174.30', '100,000.00', '104,289.79', '117,112.00', '238,900.42', '164,963.04', '92,315.09', '100,000.00', ''],
    ]
    const parsed = await parseBalanceFile(makeFile(rows))

    // 标题/期间/核算单位/两级表头/合计行全部剔除，只留 4 个科目行
    expect(parsed).toHaveLength(4)
    const byCode = Object.fromEntries(parsed.map(r => [r.account_code, r]))
    // 金额取「期末余额」列（而非期初），千分位字符串正确转数值
    expect(byCode['1001'].debit_balance).toBe(50)
    expect(byCode['1002'].debit_balance).toBe(12265.09)
    expect(byCode['4001'].credit_balance).toBe(100000)
    // 前端预脱敏仍然生效
    expect(byCode['1221'].account_name).toBe('其他应收款')
  })

  it('解析单行表头（期初借方余额/期初贷方余额，期初建账模板版式）', async () => {
    const rows = [
      ['科目代码', '科目名称', '期初借方余额', '期初贷方余额', '填写指引'],
      ['1001', '库存现金', 5000, 0, ''],
      ['1002', '银行存款', 120000, 0, ''],
      ['3001', '实收资本', 0, 125000, ''],
    ]
    const parsed = await parseBalanceFile(makeFile(rows))
    expect(parsed).toHaveLength(3)
    const byCode = Object.fromEntries(parsed.map(r => [r.account_code, r]))
    expect(byCode['1001'].debit_balance).toBe(5000)
    expect(byCode['3001'].credit_balance).toBe(125000)
  })

  it('解析「方向 + 期末余额」单列式导出，按方向拆借贷', async () => {
    const rows = [
      ['科目编码', '科目名称', '方向', '期末余额'],
      ['1001', '库存现金', '借', 800],
      ['2203', '预收账款', '贷', 2000],
      ['合计', '', '', 2800],
    ]
    const parsed = await parseBalanceFile(makeFile(rows))
    expect(parsed).toHaveLength(2)
    const byCode = Object.fromEntries(parsed.map(r => [r.account_code, r]))
    expect(byCode['1001'].debit_balance).toBe(800)
    expect(byCode['1001'].credit_balance).toBe(0)
    expect(byCode['2203'].credit_balance).toBe(2000)
    expect(byCode['2203'].debit_balance).toBe(0)
  })

  it('剔除与末级明细并存的父级科目行，避免双计金额造成假性试算不平', async () => {
    // 复刻真实事故：导出同时含 1002（一级，12,265.09）与 1002004（末级，12,265.09），
    // 双计会让借贷合计差出 ¥18,348.05，误报 R-04 借贷不平衡
    const rows = [
      ['科目编码', '科目名称', '期初余额', '', '期末余额', '', ''],
      ['', '', '借方', '贷方', '借方', '贷方', ''],
      ['1001', '库存现金', '50.00', '0.00', '50.00', '0.00'],
      ['1002', '银行存款', '12,265.09', '0.00', '12,265.09', '0.00'],
      ['1002004', '银行存款-招商银行5719', '12,265.09', '0.00', '12,265.09', '0.00'],
      ['2203', '预收账款', '0.00', '12,315.09', '0.00', '12,315.09'],
      ['4001', '实收资本', '0.00', '0.00', '0.00', '0.00'],
    ]
    const parsed = await parseBalanceFile(makeFile(rows))
    const codes = parsed.map(r => r.account_code)
    // 父级 1002 被剔除，末级 1002004 保留
    expect(codes).not.toContain('1002')
    expect(codes).toContain('1002004')
    expect(codes).toContain('1001')
    // 借方合计 = 贷方合计 = 12,315.09，试算恢复平衡
    const debit = parsed.reduce((s, r) => s + Number(r.debit_balance), 0)
    const credit = parsed.reduce((s, r) => s + Number(r.credit_balance), 0)
    expect(debit).toBe(12315.09)
    expect(credit).toBe(12315.09)
  })

  it('剔除数据区中的重复表头行（多页导出）', async () => {
    const header = ['科目编码', '科目名称', '期末借方余额', '期末贷方余额']
    const rows = [
      header,
      ['1001', '库存现金', 100, 0],
      header,
      ['1002', '银行存款', 200, 0],
    ]
    const parsed = await parseBalanceFile(makeFile(rows))
    expect(parsed).toHaveLength(2)
    expect(parsed.map(r => r.account_code)).toEqual(['1001', '1002'])
  })

  it('全零允许解析但预览明确要求确认', async () => {
    const rows = [
      ['科目代码', '科目名称', '期初借方余额', '期初贷方余额'],
      ['1001', '库存现金', 0, 0],
      ['1002', '银行存款', 0, 0],
    ]
    const result = await parseBalanceFileWithPreview(makeFile(rows))
    expect(result.rows).toHaveLength(2)
    expect(result.preview.hasAmbiguity).toBe(true)
    expect(result.preview.warnings.join(' ')).toContain('金额全部为 0')
    expect(result.preview.balanceType).toBe('opening')
  })

  it('预览返回实际所选期末列、期间、单位与准确合计', async () => {
    const result = await parseBalanceFileWithPreview(makeFile([
      ['期间：2026年9月~9月', '单位: 元'],
      ['科目编码', '科目名称', '期初借方余额', '期初贷方余额', '期末借方余额', '期末贷方余额'],
      ['1001', '库存现金', 1, 0, 0.1, 0],
      ['1002', '银行存款', 2, 0, 0.2, 0],
    ]))
    expect(result.preview).toMatchObject({ periodLabel: '2026年9月~9月', balanceType: 'ending', unit: 'yuan', debitTotal: 0.3, creditTotal: 0, hasAmbiguity: false })
    expect(result.preview.selectedColumns).toMatchObject({ debit: 4, credit: 5 })
  })

  it.each([['', ''], ['—', '-'], ['', '—']])('双侧空白或不确定值 %s / %s：混合时跳过该行并提示，不作为零提交', async (debit, credit) => {
    const { rows, preview } = await parseBalanceFileWithPreview(makeFile([
      ['科目编码', '科目名称', '期末借方余额', '期末贷方余额'],
      ['1001', '库存现金', debit, credit], ['1002', '银行存款', 10, ''],
    ]))
    expect(rows.map(r => r.account_code)).toEqual(['1002'])
    expect(rows[0].debit_balance).toBe(10)
    expect(preview.warnings.join(' ')).toContain('借贷余额均为空')
  })

  it('全部科目行余额均为空或未确定时报错（防错选空白列）', async () => {
    await expect(parseBalanceFile(makeFile([
      ['科目编码', '科目名称', '期末借方余额', '期末贷方余额'],
      ['1001', '库存现金', '', '—'],
    ]))).rejects.toThrow('余额为空或未确定')
  })

  it.each(['', '—', '-'])('方向单列空值 %s：唯一行空值时报错提示', async (amount) => {
    await expect(parseBalanceFileWithPreview(makeFile([
      ['科目编码', '科目名称', '方向', '期末余额'], ['1001', '库存现金', '平', amount],
    ]))).rejects.toThrow('余额为空或未确定')
  })

  it('单侧有明确金额时另一侧空白按零处理', async () => {
    const { rows, preview } = await parseBalanceFileWithPreview(makeFile([
      ['科目编码', '科目名称', '期末借方余额', '期末贷方余额'], ['1001', '库存现金', 10, ''],
      ['合计', '', '', ''],
    ]))
    expect(rows).toHaveLength(1)
    expect(rows[0]).toMatchObject({ debit_balance: 10, credit_balance: 0 })
    expect(preview.warnings.join(' ')).toContain('1 个科目行单侧余额为空或占位，已按零处理')
  })

  it('整数分正确处理负数和小数', async () => {
    const { rows, preview } = await parseBalanceFileWithPreview(makeFile([
      ['科目编码', '科目名称', '期末借方余额', '期末贷方余额'],
      ['1001', '库存现金', '-1.15', 0], ['1002', '银行存款', '0.05', 0],
    ]))
    expect(rows[0].debit_balance).toBe(-1.15)
    expect(preview.debitTotal).toBe(-1.1)
  })

  it('单列余额优先期末且绝不选择发生额', async () => {
    const result = await parseBalanceFileWithPreview(makeFile([
      ['科目编码', '科目名称', '方向', '期初余额', '本期发生额', '期末余额'],
      ['1001', '库存现金', '借', 5, 20, 25],
    ]))
    expect(result.rows[0].debit_balance).toBe(25)
    expect(result.preview).toMatchObject({ balanceType: 'ending', selectedColumns: { balance: 5 } })
  })

  it.each(['12abc', '1,2', 'NaN', '1.234', '1e3', '(--2)'])('拒绝无效金额 %s', async (value) => {
    await expect(parseBalanceFile(makeFile([
      ['科目编码', '科目名称', '期末借方余额', '期末贷方余额'], ['1001', '库存现金', value, 0],
    ]))).rejects.toThrow('无效金额')
  })

  it.each(['', '未知', '借贷', '平'])('拒绝非零金额的未知方向 %s', async (direction) => {
    await expect(parseBalanceFile(makeFile([
      ['科目编码', '科目名称', '方向', '期末余额'], ['1001', '库存现金', direction, 20],
    ]))).rejects.toThrow('方向未知')
  })

  it('平向零余额有效', async () => {
    const { rows } = await parseBalanceFileWithPreview(makeFile([
      ['科目编码', '科目名称', '方向', '期末余额'], ['1001', '库存现金', '平', 0],
    ]))
    expect(rows[0].debit_balance).toBe(0)
  })

  it('父子余额不一致时保留父级合计行（明细缺失不低估金额）并提示核对', async () => {
    const { rows, preview } = await parseBalanceFileWithPreview(makeFile([
      ['科目编码', '科目名称', '期末借方余额', '期末贷方余额'],
      ['1002', '银行存款', 200, 0], ['1002001', '银行存款-张三', 100, 0],
    ]))
    expect(rows.map(r => r.account_code)).toEqual(['1002'])
    expect(preview.debitTotal).toBe(200)
    expect(preview.warnings.join(' ')).toContain('不一致')
  })

  it('异向子级按借贷净额逐级核对，保留明细两侧金额', async () => {
    const result = await parseBalanceFileWithPreview(makeFile([
      ['科目编码', '科目名称', '期末借方余额', '期末贷方余额'],
      ['1221', '其他应收款', 70, 0],
      ['122101', '其他应收款', 100, 0],
      ['12210101', '其他应收款', 120, 0],
      ['12210102', '其他应收款', 0, 20],
      ['122102', '其他应收款', 0, 30],
    ]))
    expect(result.rows.map(row => row.account_code)).toEqual(['12210101', '12210102', '122102'])
    expect(result.preview).toMatchObject({ debitTotal: 120, creditTotal: 50 })
    expect(result.preview.warnings.join(' ')).toContain('净余额一致')
  })

  it('异向子级净额不一致时保留父级合计并提示核对', async () => {
    const { rows, preview } = await parseBalanceFileWithPreview(makeFile([
      ['科目编码', '科目名称', '期末借方余额', '期末贷方余额'],
      ['1221', '其他应收款', 71, 0], ['122101', '其他应收款', 100, 0], ['122102', '其他应收款', 0, 30],
    ]))
    expect(rows.map(r => r.account_code)).toEqual(['1221'])
    expect(preview.debitTotal).toBe(71)
    expect(preview.warnings.join(' ')).toContain('不一致')
  })

  it('多层父子逐级核对并提示去重', async () => {
    const result = await parseBalanceFileWithPreview(makeFile([
      ['科目编码', '科目名称', '期末借方余额', '期末贷方余额'],
      ['1002', '银行存款', 100, 0], ['100201', '银行存款', 100, 0], ['10020101', '银行存款-张三', 100, 0],
    ]))
    expect(result.rows.map(row => row.account_code)).toEqual(['10020101'])
    expect(result.preview.warnings.join(' ')).toContain('核验并排除 2 个')
  })

  it('名称脱敏不泄露姓名或企业且不猜测股东身份', async () => {
    const { rows } = await parseBalanceFileWithPreview(makeFile([
      ['科目编码', '科目名称', '期末借方余额', '期末贷方余额'],
      ['122101', '其他应收款-张三总', 100, 0],
      ['122102', '其他应收款-张三贸易有限公司', 200, 0],
      ['122103', '股东-李四', 300, 0],
    ]))
    const data = JSON.stringify(rows)
    expect(data).not.toMatch(/张三|李四|有限公司|股东|总/)
    expect(rows[0].account_name).toBe('其他应收款')
  })

  it('非数字科目编码不上传原文，避免夹带身份信息', async () => {
    const { rows } = await parseBalanceFileWithPreview(makeFile([
      ['科目编码', '科目名称', '期末借方余额', '期末贷方余额'],
      ['1221-张三', '其他应收款-李四', 10, 0],
    ]))
    expect(rows[0].account_code).toBe('ACC-1')
    expect(JSON.stringify(rows)).not.toMatch(/张三|李四/)
  })

  it('通用借贷表头标记余额类型未知', async () => {
    const { preview } = await parseBalanceFileWithPreview(makeFile([
      ['科目编码', '科目名称', '借方', '贷方'], ['1001', '库存现金', 1, 0],
    ]))
    expect(preview.balanceType).toBe('unknown')
  })

  it('上年末只属于期初口径，不冒充期末', async () => {
    const { preview } = await parseBalanceFileWithPreview(makeFile([
      ['科目编码', '科目名称', '上年末借方余额', '上年末贷方余额'], ['1001', '库存现金', 1, 0],
    ]))
    expect(preview.balanceType).toBe('opening')
    expect(preview.hasAmbiguity).toBe(true)
  })

  it('拒绝非元单位和不完整的借贷列', async () => {
    await expect(parseBalanceFile(makeFile([
      ['单位：万元'], ['科目编码', '科目名称', '期末借方余额', '期末贷方余额'], ['1001', '库存现金', 1, 0],
    ]))).rejects.toThrow('不是元')
    await expect(parseBalanceFile(makeFile([
      ['科目编码', '科目名称', '期末借方余额', '期初贷方余额'], ['1001', '库存现金', 1, 0],
    ]))).rejects.toThrow('借贷列不完整')
  })

  it('无任何金额列时报错并给出格式指引', async () => {
    const rows = [
      ['科目编码', '科目名称'],
      ['1001', '库存现金'],
    ]
    await expect(parseBalanceFile(makeFile(rows))).rejects.toThrow('未识别到余额金额列')
  })

  it('无表头行（纯数据）时报错并给出格式指引', async () => {
    const rows = [
      ['库存现金', 100],
      ['银行存款', 200],
    ]
    await expect(parseBalanceFile(makeFile(rows))).rejects.toThrow('未识别到表头行')
  })

  it('第三方导出（期初/本期/累计/期末单行表头 + 大量零余额空行 + 父子层级）完整解析', async () => {
    // 复刻财务软件单行表头导出：全空明细行不阻断，父级汇总行剔除，期末列汇总平衡
    const rows = [
      ['科目编码', '科目名称', '科目级别', '期初余额-借方', '期初余额-贷方', '本期发生额-借方', '本期发生额-贷方', '本年累计发生额-借方', '本年累计发生额-贷方', '期末余额-借方', '期末余额-贷方'],
      ['1001', '库存现金', '1', '50', '', '', '', '', '', '50', ''],
      ['1002', '银行存款', '1', '18327.71', '', '158900.42', '164963.04', '158900.42', '164963.04', '12265.09', ''],
      ['1002001', '银行存款-招商银行', '2', '18327.71', '', '158900.42', '164963.04', '158900.42', '164963.04', '12265.09', ''],
      ['1002002', '银行存款-工商银行', '2', '', '', '', '', '', '', '', ''],
      ['1122', '应收账款', '1', '2157.42', '', '', '', '', '', '2157.42', ''],
      ['1122001', '应收账款-甲公司', '2', '543.3', '', '', '', '', '', '543.3', ''],
      ['1122002', '应收账款-乙公司', '2', '', '', '', '', '', '', '', ''],
      ['1122003', '应收账款-丙公司', '2', '1614.12', '', '', '', '', '', '1614.12', ''],
      ['2202', '应付账款', '1', '', '4219', '', '', '', '', '', '4469'],
      ['2202001', '应付账款-丁公司', '2', '', '2400', '', '', '', '', '', '2400'],
      ['2202002', '应付账款-戊公司', '2', '', '2069', '', '', '', '', '', '2069'],
      ['2202003', '应付账款-己公司', '2', '', '', '', '', '', '', '', ''],
      ['4001', '实收资本', '1', '', '', '', '', '', '', '', '10003.51'],
    ]
    const { rows: parsed, preview } = await parseBalanceFileWithPreview(makeFile(rows))
    // 父级 1002/1122/2202 剔除、全空行 1002002/1122002/2202003 跳过，剩 7 个叶行
    expect(parsed).toHaveLength(7)
    const byCode = Object.fromEntries(parsed.map(r => [r.account_code, r]))
    expect(byCode['1002001'].debit_balance).toBe(12265.09)
    expect(byCode['2202002'].credit_balance).toBe(2069)
    expect(byCode['1002']).toBeUndefined()
    expect(preview.balanceType).toBe('ending')
    expect(preview.debitTotal).toBeCloseTo(14472.51, 2)
    expect(preview.creditTotal).toBeCloseTo(14472.51, 2)
    expect(preview.warnings.some(w => w.includes('借贷余额均为空'))).toBe(true)
    expect(preview.warnings.some(w => w.includes('已核验并排除 3 个'))).toBe(true)
  })

  it('父子金额不一致时保留父级合计（明细缺失不低估金额）并提示核对', async () => {
    const rows = [
      ['科目编码', '科目名称', '期末余额-借方', '期末余额-贷方'],
      ['1001', '库存现金', '50', ''],
      ['1401', '长期股权投资', '', '8000'],
      ['140101', '长期股权投资-A公司', '', '5000'],
      ['140102', '长期股权投资-B公司', '', '1000'],
    ]
    const { rows: parsed, preview } = await parseBalanceFileWithPreview(makeFile(rows))
    // 明细合计 6000 ≠ 父级 8000 → 保留父级 1401，剔除 140101/140102
    const byCode = Object.fromEntries(parsed.map(r => [r.account_code, r]))
    expect(byCode['1401'].credit_balance).toBe(8000)
    expect(byCode['140101']).toBeUndefined()
    expect(preview.debitTotal).toBe(50)
    expect(preview.creditTotal).toBe(8000)
    expect(preview.warnings.some(w => w.includes('不一致'))).toBe(true)
  })

  it('父级为零而明细有数时以明细为准', async () => {
    const rows = [
      ['科目编码', '科目名称', '期末余额-借方', '期末余额-贷方'],
      ['1601', '固定资产', '0.00', '0.00'],
      ['160101', '固定资产-设备', '', '3000'],
    ]
    const { rows: parsed } = await parseBalanceFileWithPreview(makeFile(rows))
    const byCode = Object.fromEntries(parsed.map(r => [r.account_code, r]))
    expect(byCode['160101'].credit_balance).toBe(3000)
    expect(byCode['1601']).toBeUndefined()
  })
})
