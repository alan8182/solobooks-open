/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */
/**
 * closing-wizard 三类导入流程（Step 1）：微信账单（明细/汇总双模式）、发票
 * Excel（税局格式 + 模糊列名映射双路径）、银行流水。从 index.vue 拆出。
 *
 * 共享数据（rawStatements/bankStatements/invoicesList 及对账过滤）仍由父页
 * loadAllData 统一重建；本模块只管「解析 → 预览 → 确认导入 → 提示」，
 * 写成功后统一 reloadAll。
 */
import { ref, type Ref } from 'vue'
import { useMessage } from 'naive-ui'
import * as XLSX from 'xlsx'
import apiClient from '@/api/client'
import { importInvoices } from '@/api/invoice'
import {
  fetchPlatformBillRecords,
  importPlatformBill,
  parsePlatformBill,
  type PlatformBillPlatform,
} from '@/api/platform-bill'
import { buildBankRows, detectBankColumnMapping, parseBankWorkbookData } from '@/utils/bankImport'
import { invoiceFileRejection } from '@/utils/invoiceImport'
import { emptyBillWarning } from '@/utils/platformBill'
import { summarizeWechatImportFailures, type WechatImportFailureGroup } from '@/utils/wechatImport'
import { accountingErrorText } from '@/utils/errors'
import { useBackfillConfirm, isBackfillCancelled } from '@/composables/backfillConfirm'
import type { useEntryStore } from '@/stores/entry'

export function useClosingImports(options: {
  month: Ref<string>
  loading: Ref<boolean>
  entryStore: ReturnType<typeof useEntryStore>
  reloadAll: () => Promise<void>
}) {
  const { month, loading, entryStore, reloadAll } = options
  const message = useMessage()
  const backfillConfirm = useBackfillConfirm()

  // 平台账单（微信 / 支付宝）
  const platformBillPlatform = ref<PlatformBillPlatform | ''>('')
  const wechatBillRows = ref<any[]>([])
  const wechatBillSummary = ref<Record<string, any>>({})
  const wechatBillImported = ref(false)
  const wechatBillImporting = ref(false)
  const wechatImportMode = ref<'summary' | 'detail'>('summary')
  const wechatBillFailures = ref<WechatImportFailureGroup[]>([])
  const wechatBillRecords = ref<any[]>([])
  const wechatBillRecordsSummary = ref<Record<string, any>>({})
  // 银行上传结果
  const bankUploadResult = ref<{ imported: number; errors: number; skipped: number; rows: any[] } | null>(null)

  function safeFloat(val: any): number {
    if (val === null || val === undefined || val === '') return 0
    const n = Number(val)
    return Number.isFinite(n) ? n : 0
  }

  // Parse platform bill CSV (WeChat / Alipay, auto-detected; preview only)
  async function processWechatBillFile(file: File) {
    // 直接拖入新文件时也要退出上一份账单的「已导入」态，否则成功提示会盖住新文件
    wechatBillImported.value = false
    wechatBillFailures.value = []
    loading.value = true
    try {
      const res = await parsePlatformBill(file)
      if (!res.valid || res.valid.length === 0) {
        // 解析成功但 0 行 ≠ 格式错误：最常见是导出的期间里本来就没有交易，
        // 说「请检查文件格式」会让人去核对一个没问题的文件。文案收敛在 utils。
        message.warning(emptyBillWarning(res.summary))
        return
      }
      platformBillPlatform.value = res.platform
      wechatBillRows.value = res.valid
      wechatBillSummary.value = res.summary || {}
      wechatBillImported.value = false
      wechatBillFailures.value = []
      const label = res.platform === 'alipay' ? '支付宝' : '微信'
      message.success(`已解析 ${res.valid.length} 条${label}交易记录，请确认后点击导入`)
      if (res.warnings?.length) {
        message.warning(res.warnings.map((item) => item.reason).join('；'))
      }
    } catch (e) {
      message.error(accountingErrorText(e, '平台账单文件解析失败，请确认为微信支付或支付宝商家后台导出的 CSV 格式'))
    } finally {
      loading.value = false
    }
  }

  // Remove a single row from wechat bill preview
  function removeWechatRow(index: number) {
    wechatBillRows.value.splice(index, 1)
  }

  // Confirm import of previewed wechat bill rows
  async function confirmWechatBillImport() {
    const isSummaryMode = wechatImportMode.value === 'summary'
    const importableRows = wechatBillRows.value.filter((row) => !row.is_duplicate)
    if (wechatBillImporting.value) return
    if (!isSummaryMode && importableRows.length === 0) return

    // 计算发生日期：取账单最晚一条日期或目标月份最后一天
    let latestDate = ''
    for (const r of wechatBillRows.value) {
      if (r.date && (!latestDate || r.date > latestDate)) latestDate = r.date
    }
    const occurDate = latestDate || (month.value ? `${month.value}-28` : '')

    const platform = platformBillPlatform.value || 'wechat'
    const platformLabel = platform === 'alipay' ? '支付宝' : '微信'
    loading.value = true
    wechatBillImporting.value = true
    try {
      const res: any = await backfillConfirm.withBackfill((allowBackfill, backfillReason) =>
        importPlatformBill(platform, isSummaryMode ? [] : importableRows, {
          allowBackfill,
          backfillReason,
          importMode: wechatImportMode.value,
          summary: wechatBillSummary.value,
          occurDate,
        }),
      )
      wechatBillFailures.value = summarizeWechatImportFailures(res.results || [])
      wechatBillImported.value = true
      wechatBillRows.value = []
      let msg = isSummaryMode
        ? `${platformLabel}账单汇总完成：已生成 1 张账单期间综合凭证（收入已价税分离计提增值税）。`
        : `${platformLabel}账单导入完成：${res.imported} 条交易已入账（收入已价税分离计提增值税）。`
      if (res.ignored_duplicates > 0) {
        msg += `（已过滤 ${res.ignored_duplicates} 条已存在账目）`
      }
      if (res.failed > 0) {
        msg += `，${res.failed} 条失败（原因见下方提示）`
      }
      if (res.imported > 0) {
        message.success(msg)
      } else {
        message.warning(msg)
      }
      await reloadAll()
    } catch (e) {
      if (!isBackfillCancelled(e)) message.error(accountingErrorText(e, '导入失败，请重试'))
    } finally {
      wechatBillImporting.value = false
      loading.value = false
    }
  }

  // Load previously imported platform bill records for the target month (merged)
  async function loadWechatBillRecords() {
    try {
      const res: any = await fetchPlatformBillRecords(month.value)
      wechatBillRecords.value = Array.isArray(res.items) ? res.items : []
      wechatBillRecordsSummary.value = res.summary || {}
    } catch {
      wechatBillRecords.value = []
      wechatBillRecordsSummary.value = {}
    }
  }

  async function readFileAsArrayBuffer(file: File): Promise<ArrayBuffer> {
    if (typeof file.arrayBuffer === 'function') {
      return await file.arrayBuffer()
    }
    return await new Promise<ArrayBuffer>((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = (ev) => resolve(ev.target?.result as ArrayBuffer)
      reader.onerror = () => reject(reader.error || new Error('文件读取失败'))
      reader.readAsArrayBuffer(file)
    })
  }

  async function importSingleInvoiceFile(file: File): Promise<{
    ok: boolean
    imported: number
    ignored: number
    offsets: any[]
  }> {
    try {
      const data = new Uint8Array(await readFileAsArrayBuffer(file))
      const workbook = XLSX.read(data, { type: 'array' })
      const sheet = workbook.Sheets[workbook.SheetNames[0]]
      const json = XLSX.utils.sheet_to_json(sheet, { defval: '' })
      if (json.length === 0) {
        message.warning(`${file.name}：Excel 文件为空`)
        return { ok: false, imported: 0, ignored: 0, offsets: [] }
      }

      const columns = Object.keys(json[0] as any)
      // 文件级闸门：银行流水/资金明细不能进发票导入。模糊映射会把「账户余额」
      // 当成金额，后端若只看金额就会整批入账（2026-11 真实事故）。
      const rejection = invoiceFileRejection(columns)
      if (rejection) {
        message.error(`${file.name}：${rejection}`)
        return { ok: false, imported: 0, ignored: 0, offsets: [] }
      }
      const isTaxBureauFormat = columns.includes('数电发票号码') || columns.includes('销方名称') || columns.includes('购买方名称')

      let mapped: any[] = []
      if (isTaxBureauFormat) {
        mapped = json.map((row: any) => {
          const invoiceNum = String(row['数电发票号码'] || row['发票号码'] || row['发票代码'] || '').trim()

          const isNegative = String(row['是否正数发票'] || '').trim() === '否' || String(row['发票状态'] || '').trim() === '红冲'

          let amountVal = safeFloat(row['金额'] || row['不含税金额'] || 0)
          let totalAmountVal = safeFloat(row['价税合计'] || row['含税金额'] || row['总金额'] || 0)
          let taxAmountVal = safeFloat(row['税额'] || 0)

          if (isNegative) {
            amountVal = -Math.abs(amountVal)
            totalAmountVal = -Math.abs(totalAmountVal)
            taxAmountVal = -Math.abs(taxAmountVal)
          }

          return {
            invoice_number: invoiceNum,
            invoice_code: String(row['发票代码'] || '').trim(),
            invoice_date: String(row['开票日期'] || row['开票时间'] || row['发票日期'] || '').trim(),
            seller_name: String(row['销方名称'] || '').trim(),
            buyer_name: String(row['购买方名称'] || '').trim(),
            seller_tax_id: String(row['销方识别号'] || '').trim(),
            buyer_tax_id: String(row['购方识别号'] || '').trim(),
            total_amount: totalAmountVal,
            amount: amountVal || (totalAmountVal - taxAmountVal),
            tax_amount: taxAmountVal || (totalAmountVal - amountVal),
            tax_rate: String(row['税率'] || '').trim(),
            item_name: String(row['货物或应税劳务名称'] || '').trim(),
            specification: String(row['规格型号'] || '').trim(),
            unit: String(row['单位'] || '').trim(),
            quantity: row['数量'] ? safeFloat(row['数量']) : null,
            unit_price: row['单价'] ? safeFloat(row['单价']) : null,
            invoice_source: String(row['发票来源'] || '').trim(),
            invoice_type: String(row['发票票种'] || '').trim(),
            invoice_status: String(row['发票状态'] || '').trim(),
            drawer: String(row['开票人'] || '').trim(),
            remark: String(row['备注'] || '').trim(),
          }
        })
      } else {
        // Fallback to fuzzy regex mapping
        const map: Record<string, string> = {}
        for (const col of columns) {
          const lc = col.toLowerCase()
          if (/发票号码|发票代码|invoice.*no|invoice.*num|发票编号/.test(lc)) map[col] = 'invoice_number'
          else if (/开票日期|发票日期|date|开票时间/.test(lc)) map[col] = 'invoice_date'
          else if (/销售方|卖方|seller|供应商|供货方/.test(lc)) map[col] = 'seller_name'
          else if (/购买方|买方|buyer|客户/.test(lc)) map[col] = 'buyer_name'
          else if (/价税合计|总金额|total.*amount|含税金额|合计/.test(lc)) map[col] = 'total_amount'
          else if (/金额|amount|不含税/.test(lc) && !/税额/.test(lc)) map[col] = 'amount'
          else if (/税额|tax/.test(lc)) map[col] = 'tax_amount'
          else if (/名称|货物|item|商品/.test(lc)) map[col] = 'item_name'
        }

        mapped = json.map((row: any) => {
          const out: Record<string, any> = {}
          for (const [col, field] of Object.entries(map)) {
            if (field) out[field] = row[col]
          }

          const isNegative = Object.entries(row).some(([k, v]) => {
            const key = String(k).trim()
            const val = String(v).trim()
            return (key === '是否正数发票' && val === '否') || (key === '发票状态' && val === '红冲')
          })

          if (isNegative) {
            if (out.amount !== undefined) out.amount = -Math.abs(safeFloat(out.amount))
            if (out.total_amount !== undefined) out.total_amount = -Math.abs(safeFloat(out.total_amount))
            if (out.tax_amount !== undefined) out.tax_amount = -Math.abs(safeFloat(out.tax_amount))
          }

          return out
        })
      }

      const parseRes: any = await apiClient.post('/api/invoice/parse', { rows: mapped })
      if (parseRes.valid.length === 0) {
        const reason = Array.isArray(parseRes.errors) ? parseRes.errors[0]?.reason : ''
        message.error(`${file.name}：${reason || '未在文件中解析到有效发票行'}`)
        return { ok: false, imported: 0, ignored: 0, offsets: [] }
      }

      const rows = parseRes.valid.map((r: any) => {
        const direction = r.direction || 'expense'
        // 只有明确分类或品名规则建议过的分类才提交；两者都没有时不要兜底成
        // 「其他支出」——交给后端按品名规则/LLM 补分类（classify=true）。
        const category = String(r.category ?? '').trim() || String(r.suggestedCategory ?? '').trim()
        return {
          ...r,
          direction,
          ...(category ? { category } : {}),
        }
      })

      const importRes: any = await importInvoices(rows, true)
      return {
        ok: true,
        imported: Number(importRes.imported) || 0,
        ignored: Number(importRes.ignored_duplicates) || 0,
        offsets: Array.isArray(importRes.offsets) ? importRes.offsets : [],
      }
    } catch (err) {
      message.error(accountingErrorText(err, `${file.name} 导入解析失败`))
      return { ok: false, imported: 0, ignored: 0, offsets: [] }
    }
  }

  // Parse and upload one or multiple invoice files (开票表 / 收票表可一次或分批导入)
  async function processInvoiceFiles(files: File[]) {
    if (!Array.isArray(files) || files.length === 0) return
    loading.value = true
    try {
      let okCount = 0
      let totalImported = 0
      let totalIgnored = 0
      const allOffsets: any[] = []

      for (const file of files) {
        const res = await importSingleInvoiceFile(file)
        if (!res.ok) continue
        okCount++
        totalImported += res.imported
        totalIgnored += res.ignored
        if (res.offsets.length > 0) allOffsets.push(...res.offsets)
      }

      if (okCount > 0) {
        let msg =
          files.length === 1
            ? `成功导入 ${totalImported} 张新发票！请在对账步骤查看候选建议并手动确认。`
            : `已处理 ${okCount}/${files.length} 个文件，共导入 ${totalImported} 张新发票！请在对账步骤查看候选建议并手动确认。`
        if (totalIgnored > 0) {
          msg += `（系统已自动过滤 ${totalIgnored} 张已存在的发票记录）`
        }
        if (allOffsets.length > 0) {
          msg += `；已自动冲抵 ${allOffsets.length} 笔历史往来暂付款（${allOffsets.map((o: any) => `¥${o.amount}`).join('、')}），凭证往来侧已直接对冲`
        }
        message.success(msg)
        await reloadAll()
      }
    } finally {
      loading.value = false
    }
  }

  async function processInvoiceFile(file: File) {
    await processInvoiceFiles([file])
  }

  // Parse and upload bank statements CSV/Excel
  async function processBankFile(file: File) {
    loading.value = true

    const reader = new FileReader()
    reader.onload = async (ev) => {
      try {
        const data = new Uint8Array(ev.target?.result as ArrayBuffer)
        const json = parseBankWorkbookData(data)
        if (json.length === 0) {
          message.warning('流水文件为空')
          return
        }

        const { rows: importable, skippedInvalidDate } = buildBankRows(json, detectBankColumnMapping(Object.keys(json[0] as any)))
        if (importable.length === 0) {
          message.error('未在文件中解析到带有效交易日期的流水行')
          return
        }

        const importRes: any = await apiClient.post('/api/bank/import-json', {
          rows: importable,
          statement_id: `bs-upload-${Date.now()}`,
        })

        bankUploadResult.value = {
          imported: importRes.imported,
          errors: importRes.errors,
          skipped: skippedInvalidDate,
          rows: importable,
        }

        message.success(`成功导入 ${importRes.imported} 条银行流水！请在对账步骤手动确认匹配或分类。`)
        await reloadAll()
      } catch (err) {
        message.error(accountingErrorText(err, '银行流水文件解析导入失败'))
      } finally {
        loading.value = false
      }
    }
    reader.readAsArrayBuffer(file)
  }

  async function handleDeleteInvoice(id: string) {
    loading.value = true
    try {
      await entryStore.removeEntry(id)
      message.success('已成功删除该发票，不予入账')
      await reloadAll()
    } catch (err) {
      const detail = (err as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail
      const reason = typeof detail === 'string' ? detail : (detail as { message?: string } | undefined)?.message
      message.error(reason || '删除发票失败')
    } finally {
      loading.value = false
    }
  }

  async function toggleArchiveMode(id: string, isArchive: boolean) {
    loading.value = true
    try {
      await apiClient.patch(`/api/record/entries/${id}`, {
        is_archive_only: isArchive,
        isArchiveOnly: isArchive,
      })
      message.success(isArchive ? '已将该发票设为“仅归档不记账”，不再影响科目余额' : '已将该发票设为“参与记账”')
      await reloadAll()
    } catch (err) {
      message.error(accountingErrorText(err, '切换记账模式失败'))
    } finally {
      loading.value = false
    }
  }

  return {
    // 平台账单（微信 / 支付宝）
    platformBillPlatform,
    wechatBillRows,
    wechatBillSummary,
    wechatBillImported,
    wechatBillImporting,
    wechatImportMode,
    wechatBillFailures,
    wechatBillRecords,
    wechatBillRecordsSummary,
    loadWechatBillRecords,
    processWechatBillFile,
    removeWechatRow,
    confirmWechatBillImport,
    // 发票
    processInvoiceFile,
    processInvoiceFiles,
    handleDeleteInvoice,
    toggleArchiveMode,
    // 银行
    bankUploadResult,
    processBankFile,
  }
}
