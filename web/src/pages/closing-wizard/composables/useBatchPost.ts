/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */
/**
 * 批量过账域（closing-wizard Step 3）：单张凭证就地查验/过账弹层、整月批量
 * 过账、逐张走单（walkthrough）与确认弹层。从 index.vue 拆出。
 */
import { computed, ref, type Ref } from 'vue'
import { useMessage } from 'naive-ui'
import {
  postEntry,
  getPostingOptions,
  batchPostEntries as batchPostEntriesApi,
} from '@/api/accounting'
import {
  normalizeVoucherLines,
  generateVoucherLines,
  amountSignForDirection,
  getAccountByCategory,
  type VoucherLine,
} from '@/utils/accounting'
import { formatAccountPreviewLabel } from '@/utils/closingWizard'
import type { useEntryStore } from '@/stores/entry'

export function useBatchPost(options: {
  month: Ref<string>
  /** 父页按记账期间归集的本月分录判定 */
  entryPeriod: (e: any) => string
  entryStore: ReturnType<typeof useEntryStore>
  reloadAll: () => Promise<void>
  /** 过账失败时的错误文案（父页统一 apiErrorMessage，保留救援语义） */
  errorMessage: (err: any, fallback: string) => string
}) {
  const { month, entryPeriod, entryStore, reloadAll, errorMessage } = options
  const message = useMessage()

  const showVoucherModal = ref(false)
  const voucherModalLoading = ref(false)
  const voucherPostingLoading = ref(false)
  const batchPostingLoading = ref(false)
  const batchPostConfirmVisible = ref(false)
  const batchPostWalkthrough = ref(false)
  const batchPostWalkthroughTotal = ref(0)
  const walkthroughIndex = ref(0)
  const currentVoucherEntry = ref<any>(null)
  const currentVoucherPostingOptions = ref<any>(null)

  // 本月账目最终核对清单（关账前确认弹层用）：包含本期所有有效业务凭证（待封存草稿 + 已入账凭证），
  // 剔除归档底稿、已冲销原凭证/红字凭证及期末结转凭证。
  const batchPostEntries = computed(() =>
    entryStore.activeEntries
      .filter((e: any) => {
        const status = String(e.voucherStatus || e.status || '')
        const entryType = String(e.entryType || e.entry_type || '')
        if (entryPeriod(e) !== month.value) return false
        if (e.isArchiveOnly || e.is_archive_only) return false
        if (entryType === 'closing' || entryType === 'reversal' || e.isClosingTransfer || status === 'reversed') return false
        return !status || status === 'draft' || status === 'pending_review' || status === 'posted'
      })
      .map((e: any) => {
        const rawAmount = Number(e.amount) || 0
        const direction = String(e.direction || '')
        const category = String(e.category || '')
        const voucherStatus = String(e.voucherStatus || e.status || 'draft')
        const lines = normalizeVoucherLines(e.voucherLines)
        const bankLine = lines.find((l) => l.code === '1002' || l.code.startsWith('1012'))
        const isOutflow = bankLine
          ? bankLine.side === 'credit'
          : rawAmount < 0
            ? direction !== 'expense'
            : amountSignForDirection(direction, category) === '-' || direction === 'expense'

        const date = String(e.occurDate || '').slice(0, 10)
        const shortDate = date.length >= 10 ? date.slice(5, 10) : date
        const rawTitle = String(e.title || '').trim()
        const displayTitle = rawTitle.replace(/-\d{4}-\d{2}-\d{2}$/, '').trim() || rawTitle

        const counterLine = lines.find(
          (l) => l.code !== '1002' && !l.code.startsWith('1012') && !l.code.startsWith('2221'),
        )
        const targetAcct = counterLine
          ? { code: counterLine.code, name: counterLine.name }
          : e.accountCode || e.accountName
            ? { code: String(e.accountCode || ''), name: String(e.accountName || '') }
            : getAccountByCategory(category, isOutflow ? 'expense' : 'income')
        const accountPreview = formatAccountPreviewLabel(category, targetAcct)
        const subtitle = displayTitle === category
          ? accountPreview || category
          : [category, accountPreview].filter(Boolean).join(' · ')

        return {
          id: e.id,
          title: e.title,
          displayTitle,
          subtitle,
          amount: Math.abs(rawAmount),
          direction: e.direction,
          isOutflow,
          category,
          accountName: e.accountName || '',
          date,
          shortDate,
          voucherStatus,
          isDraft: voucherStatus !== 'posted',
        }
      }),
  )

  async function openVoucherModal(entryId: string, fromWalkthrough = false) {
    if (!entryId) return
    if (!fromWalkthrough) {
      batchPostWalkthrough.value = false
    }
    showVoucherModal.value = true
    voucherModalLoading.value = true
    currentVoucherEntry.value = null
    currentVoucherPostingOptions.value = null
    try {
      let found = entryStore.activeEntries.find((e: any) => e.id === entryId)
      if (!found) {
        await entryStore.fetchEntries()
        found = entryStore.activeEntries.find((e: any) => e.id === entryId)
      }
      currentVoucherEntry.value = found || null
      if (found) {
        try {
          currentVoucherPostingOptions.value = await getPostingOptions(found.id)
        } catch {
          currentVoucherPostingOptions.value = null
        }
      }
    } finally {
      voucherModalLoading.value = false
    }
  }

  async function handleModalPostEntry() {
    if (!currentVoucherEntry.value || voucherPostingLoading.value) return
    voucherPostingLoading.value = true
    try {
      const res = await postEntry(currentVoucherEntry.value.id)
      message.success(`凭证过账成功！凭证号：${res.entry?.voucherRef || ''}`)
      currentVoucherEntry.value = res.entry
      await Promise.all([entryStore.fetchEntries(), reloadAll()])
      if (batchPostWalkthrough.value) {
        const next = batchPostEntries.value[0]
        if (next) {
          openVoucherModal(next.id)
        } else {
          batchPostWalkthrough.value = false
          showVoucherModal.value = false
          message.success('本月待过账凭证已全部过账')
        }
      }
    } catch (err: any) {
      message.error(errorMessage(err, '过账失败'))
    } finally {
      voucherPostingLoading.value = false
    }
  }

  async function handleBatchPostMonth() {
    batchPostingLoading.value = true
    try {
      const res = await batchPostEntriesApi({ month: month.value })
      if (res.postedCount > 0) {
        message.success(`已成功批量过账 ${res.postedCount} 张凭证！`)
      } else {
        message.info('本月暂无符合条件的待过账草稿凭证')
      }
      if (res.failedCount > 0) {
        message.warning(`有 ${res.failedCount} 张凭证过账未通过，请查看明细`)
      }
      await Promise.all([entryStore.fetchEntries(), reloadAll()])
    } catch (err: any) {
      message.error(errorMessage(err, '批量过账失败'))
    } finally {
      batchPostingLoading.value = false
    }
  }

  function openBatchPostConfirm() {
    batchPostConfirmVisible.value = true
  }

  function startWalkthrough() {
    const first = batchPostEntries.value[0]
    if (!first) {
      message.info('本月没有可核对的账目凭证')
      return
    }
    walkthroughIndex.value = 0
    batchPostWalkthrough.value = true
    batchPostWalkthroughTotal.value = batchPostEntries.value.length
    openVoucherModal(first.id, true)
  }

  function advanceWalkthrough() {
    const nextIdx = walkthroughIndex.value + 1
    const next = batchPostEntries.value[nextIdx]
    if (next) {
      walkthroughIndex.value = nextIdx
      openVoucherModal(next.id, true)
    } else {
      batchPostWalkthrough.value = false
      showVoucherModal.value = false
      batchPostConfirmVisible.value = true
      message.success('本月账目已逐笔核对完成，可确认无误并锁账')
    }
  }

  function startWalkthroughFromConfirm() {
    batchPostConfirmVisible.value = false
    startWalkthrough()
  }

  async function confirmBatchPost() {
    batchPostConfirmVisible.value = false
    await handleBatchPostMonth()
  }

  const modalVoucherLines = computed<VoucherLine[]>(() => {
    if (!currentVoucherEntry.value) return []
    const stored = normalizeVoucherLines(currentVoucherEntry.value.voucherLines)
    if (stored.length > 0) return stored
    return generateVoucherLines({
      type: currentVoucherEntry.value.direction as 'income' | 'expense' | 'owner_transfer',
      amount: currentVoucherEntry.value.amount,
      category: currentVoucherEntry.value.category || '',
      payer: currentVoucherEntry.value.payer || 'company_account',
    })
  })

  const modalDebitTotal = computed(() =>
    modalVoucherLines.value.filter((l) => l.side === 'debit').reduce((s, l) => s + l.amount, 0),
  )
  const modalCreditTotal = computed(() =>
    modalVoucherLines.value.filter((l) => l.side === 'credit').reduce((s, l) => s + l.amount, 0),
  )

  return {
    showVoucherModal,
    voucherModalLoading,
    voucherPostingLoading,
    batchPostingLoading,
    batchPostConfirmVisible,
    batchPostWalkthrough,
    batchPostWalkthroughTotal,
    walkthroughIndex,
    currentVoucherEntry,
    currentVoucherPostingOptions,
    batchPostEntries,
    openVoucherModal,
    handleModalPostEntry,
    handleBatchPostMonth,
    openBatchPostConfirm,
    startWalkthrough,
    advanceWalkthrough,
    startWalkthroughFromConfirm,
    confirmBatchPost,
    modalVoucherLines,
    modalDebitTotal,
    modalCreditTotal,
  }
}
