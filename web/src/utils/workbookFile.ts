/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 上传文件 → SheetJS 工作簿的统一入口（编码感知）。
 *
 * 背景：体检与旧账导入原本直接 `file.arrayBuffer()` + `XLSX.read({type:'array'})`。
 * 实测 SheetJS 对 CSV 字节输入按 latin1 逐字节解码——GBK 无 BOM（财务软件导出常态）
 * 与 UTF-8 无 BOM 的中文表头全部变成乱码，「识别表头」第一步即失败。
 * 见 balanceFileParse.test.ts 编码矩阵用例。
 *
 * 策略：
 * · xlsx/xls（ZIP `PK`、OLE 复合文档 `\xD0\xCF\x11\xE0` 签名）→ 原字节交给 SheetJS；
 * · 其余按文本（CSV 等）处理：BOM 判码 → 无 BOM 先严格 UTF-8（fatal），失败转 GBK。
 *   顺序不能反：GBK 序列几乎全部合法，先试 GBK 会把合法 UTF-8 错解。
 */
import * as XLSX from 'xlsx'

/** OLE 复合文档（.xls）的 8 字节魔数前缀。 */
const OLE_SIGNATURE = [0xd0, 0xcf, 0x11, 0xe0] as const

function hasBinarySignature(bytes: Uint8Array): boolean {
  // ZIP（xlsx / xlsm / ods）：PK\x03\x04
  if (bytes[0] === 0x50 && bytes[1] === 0x4b && (bytes[2] === 0x03 || bytes[2] === 0x05 || bytes[2] === 0x07)) return true
  // OLE2（xls）
  if (OLE_SIGNATURE.every((b, i) => bytes[i] === b)) return true
  return false
}

function decodeText(bytes: Uint8Array): string {
  // UTF-16LE / UTF-16BE BOM
  if (bytes[0] === 0xff && bytes[1] === 0xfe) return new TextDecoder('utf-16le').decode(bytes)
  if (bytes[0] === 0xfe && bytes[1] === 0xff) return new TextDecoder('utf-16be').decode(bytes)
  // UTF-8 BOM
  if (bytes[0] === 0xef && bytes[1] === 0xbb && bytes[2] === 0xbf) return new TextDecoder('utf-8').decode(bytes.subarray(3))
  // 无 BOM：先严格 UTF-8，失败转 GBK（含 GB2312/GBK 子集与多数 GB18030 文本）
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes)
  } catch {
    return new TextDecoder('gbk').decode(bytes)
  }
}

export async function readWorkbookFromFile(file: File): Promise<XLSX.WorkBook> {
  const bytes = new Uint8Array(await file.arrayBuffer())
  if (hasBinarySignature(bytes)) {
    return XLSX.read(bytes, { type: 'array' })
  }
  return XLSX.read(decodeText(bytes), { type: 'string' })
}
