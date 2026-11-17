// Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
// Licensed under the SoloBooks License v2.0.

// readWorkbookFromFile 编码矩阵回归：SheetJS 对 CSV 字节按 latin1 解码，
// GBK / 无 BOM UTF-8 的中文表头全部乱码（体检上传真实导出直接失败）。
// D-024 之后新增的统一入口必须对齐四种输入形态。
import { describe, expect, it } from 'vitest'
import * as XLSX from 'xlsx'
import { readWorkbookFromFile } from '@/utils/workbookFile'

const CSV_BODY = '科目编码,科目名称,期末余额-借方,期末余额-贷方\n1001,库存现金,50.00,\n1002,银行存款,,1234.56\n'
// Python base64 预生成：TextEncoder 只支持 UTF-8，浏览器端无法编码 GBK/UTF-16
const GBK_B64 = 'v8bEv7Hgwussv8bEv8P7s8YsxtrEqdPgtu4tvei3vSzG2sSp0+C27i20+7e9CjEwMDEsv+K05s/WvfAsNTAuMDAsCjEwMDIs0vjQ0LTmv+4sLDEyMzQuNTYK'
const UTF16LE_B64 = '//7Ree52Fn8BeCwA0Xnudg1U8HksAB9nK2dZT52YLQAfULllLAAfZytnWU+dmC0AN425ZQoAMQAwADAAMQAsAJNeWFuwc9GRLAA1ADAALgAwADAALAAKADEAMAAwADIALAD2lEyIWFs+aywALAAxADIAMwA0AC4ANQA2AAoA'

function fileFromBytes(bytes: Uint8Array | ArrayBuffer, name: string): File {
  // BlobPart 在本仓 TS 配置下要求 ArrayBuffer 视图；from/atob 构造的是 ArrayBufferLike
  const part = bytes instanceof Uint8Array ? (bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer) : bytes
  return new File([part], name)
}

function firstDataCellText(wb: XLSX.WorkBook): string {
  const grid = XLSX.utils.sheet_to_json(wb.Sheets[wb.SheetNames[0]], { header: 1, defval: '' }) as unknown[][]
  return String(grid[0]?.[0] ?? '')
}

describe('readWorkbookFromFile 编码矩阵（真实导出回归）', () => {
  it('xlsx 二进制走字节路径，中文完好', async () => {
    const ws = XLSX.utils.aoa_to_sheet([['科目编码', '科目名称'], ['1001', '库存现金']])
    const wb = XLSX.utils.book_new()
    XLSX.utils.book_append_sheet(wb, ws, '科目余额表')
    const bytes = XLSX.write(wb, { bookType: 'xlsx', type: 'array' }) as ArrayBuffer
    const read = await readWorkbookFromFile(fileFromBytes(new Uint8Array(bytes), 'a.xlsx'))
    expect(firstDataCellText(read)).toBe('科目编码')
  })

  it('GBK 无 BOM CSV（财务软件导出常态）正确解码', async () => {
    const bytes = Uint8Array.from(atob(GBK_B64), c => c.charCodeAt(0))
    const wb = await readWorkbookFromFile(fileFromBytes(bytes, 'a.csv'))
    expect(firstDataCellText(wb)).toBe('科目编码')
  })

  it('UTF-8 无 BOM CSV 正确解码', async () => {
    const wb = await readWorkbookFromFile(fileFromBytes(new TextEncoder().encode(CSV_BODY), 'a.csv'))
    expect(firstDataCellText(wb)).toBe('科目编码')
  })

  it('UTF-8 BOM 与 UTF-16LE BOM CSV 正确解码', async () => {
    const bomBytes = new Uint8Array([0xef, 0xbb, 0xbf, ...new TextEncoder().encode(CSV_BODY)])
    expect(firstDataCellText(await readWorkbookFromFile(fileFromBytes(bomBytes, 'a.csv')))).toBe('科目编码')
    const utf16 = Uint8Array.from(atob(UTF16LE_B64), c => c.charCodeAt(0))
    expect(firstDataCellText(await readWorkbookFromFile(fileFromBytes(utf16, 'a.csv')))).toBe('科目编码')
  })
})
