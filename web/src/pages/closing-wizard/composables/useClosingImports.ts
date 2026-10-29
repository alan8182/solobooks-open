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
import { parseWechatBill, importWechatBill, fetchWechatBillRecords } from '@/api/wechat-bill'
import { buildBankRows, detectBankColumnMapping, parseBankWorkbookData } from '@/utils/bankImport'
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

  // 微信账单
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

  // Parse WeChat bill CSV (preview only, not yet imported)
  async function processWechatBillFile(file: File) {
    loading.value = true
    try {
      const res = await parseWechatBill(file)
      if (!res.valid || res.valid.length === 0) {
        message.warning('未解析到有效的微信支付交易记录，请检查文件格式')
        return
      }
      wechatBillRows.value = res.valid
      wechatBillSummary.value = res.summary || {}
      wechatBillImported.value = false
      wechatBillFailures.value = []
      message.success(`已解析 ${res.valid.length} 条交易记录，请确认后点击导入`)
    } catch (e) {
      message.error(accountingErrorText(e, '微信账单文件解析失败，请确认为微信商户后台导出的 CSV 格式'))
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

    loading.value = true
    wechatBillImporting.value = true
    try {
      const res: any = await backfillConfirm.withBackfill((allowBackfill, backfillReason) =>
        importWechatBill(isSummaryMode ? [] : importableRows, {
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
        ? `微信账单月度汇总完成：已生成 1 张月度综合凭证。`
        : `微信账单导入完成：${res.imported} 条交易已入账。`
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

  // Load previously imported wechat bill records for the target month
  async function loadWechatBillRecords() {
    try {
      const res: any = await fetchWechatBillRecords(month.value)
      wechatBillRecords.value = Array.isArray(res.items) ? res.items : []
      wechatBillRecordsSummary.value = res.summary || {}
    } catch {
      wechatBillRecords.value = []
      wechatBillRecordsSummary.value = {}
    }
  }

  // Parse and upload invoices Excel/CSV
  async function processInvoiceFile(file: File) {
    loading.value = true

    const reader = new FileReader()
    reader.onload = async (ev) => {
      try {
        const data = new Uint8Array(ev.target?.result as ArrayBuffer)
        const workbook = XLSX.read(data, { type: 'array' })
        const sheet = workbook.Sheets[workbook.SheetNames[0]]
        const json = XLSX.utils.sheet_to_json(sheet, { defval: '' })
        if (json.length === 0) {
          message.warning('Excel 文件为空')
          return
        }

        const columns = Object.keys(json[0] as any)
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
          message.error('未在文件中解析到有效发票行')
          return
        }

        const rows = parseRes.valid.map((r: any) => ({
          ...r,
          direction: r.direction || 'expense',
          // 类别必须与方向同侧：销项票（income）兜底为主营业务收入，
          // 否则后端生成的挂账凭证会被收支校验拒绝导致整批导入失败
          category: r.category || (r.direction === 'income' ? '主营业务收入' : '其他支出'),
        }))

        const importRes: any = await importInvoices(rows, false)
        let msg = `成功导入 ${importRes.imported} 张新发票！请在对账步骤查看候选建议并手动确认。`
        if (importRes.ignored_duplicates > 0) {
          msg += `（系统已自动过滤 ${importRes.ignored_duplicates} 张已存在的发票记录）`
        }
        message.success(msg)
        await reloadAll()
      } catch (err) {
        message.error(accountingErrorText(err, '发票文件导入解析失败'))
      } finally {
        loading.value = false
      }
    }
    reader.readAsArrayBuffer(file)
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
    // 微信
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
    handleDeleteInvoice,
    toggleArchiveMode,
    // 银行
    bankUploadResult,
    processBankFile,
  }
}
