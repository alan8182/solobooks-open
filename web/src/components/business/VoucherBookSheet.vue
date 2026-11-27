<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
/**
 * 仿真册单页渲染：按 BookPage.kind 分发（封面/凭证/发票复现/原件目录/签章）。
 * 纸张由外层阅读器提供，本组件只负责页内版式与页脚（册号 + 第 X 页 共 N 页）。
 */
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import type { VoucherBook, VoucherBookVoucherAttachment } from '@/api/voucherBook'
import { fetchAttachmentPreviewBlob } from '@/api/attachments'
import type { BookPage } from '@/utils/voucherBook'
import { voucherOriginalTypeLabel, voucherSheetEntry } from '@/utils/voucherBook'
import VoucherSheet from '@/components/business/VoucherSheet.vue'
import InvoiceReproSheet from '@/components/business/InvoiceReproSheet.vue'

const props = withDefaults(
  defineProps<{
    page: BookPage
    payload: VoucherBook
    totalPages: number
    signed?: boolean
  }>(),
  { signed: false },
)

const previewUrl = ref('')
const previewLoading = ref(false)
const previewFailed = ref(false)

function revokePreview() {
  if (previewUrl.value && typeof URL.revokeObjectURL === 'function') {
    URL.revokeObjectURL(previewUrl.value)
  }
  previewUrl.value = ''
}

watch(
  () => (props.page.kind === 'attachment' ? props.page.attachment.attachmentId : ''),
  async (attId) => {
    revokePreview()
    previewFailed.value = false
    previewLoading.value = false
    if (!attId || typeof URL.createObjectURL !== 'function') return
    previewLoading.value = true
    try {
      const blob = await fetchAttachmentPreviewBlob(attId)
      if (props.page.kind === 'attachment' && props.page.attachment.attachmentId === attId) {
        previewUrl.value = URL.createObjectURL(blob)
      }
    } catch {
      previewFailed.value = true
    } finally {
      previewLoading.value = false
    }
  },
  { immediate: true },
)

onBeforeUnmount(revokePreview)

function attachmentSubtitle(att: VoucherBookVoucherAttachment): string {
  const meta = (att.parsedMeta || {}) as Record<string, unknown>
  const parts = [att.fileName]
  if (meta.receiptNo) parts.push(`回单号 ${String(meta.receiptNo)}`)
  if (meta.txDate) parts.push(String(meta.txDate))
  if (meta.amount) parts.push(`金额 ¥${String(meta.amount)}`)
  if (meta.summary) parts.push(String(meta.summary))
  return parts.filter(Boolean).join(' · ')
}

const meta = computed(() => props.payload.meta)
const bookNoText = computed(() => meta.value.bookNo || '预览件')

function dateCn(iso: string | undefined): string {
  const parts = String(iso || '').split('-')
  if (parts.length !== 3) return iso || '—'
  return `${parts[0]} 年 ${Number(parts[1])} 月 ${Number(parts[2])} 日`
}

const voucherRange = computed(() => {
  if (meta.value.voucherRange) return meta.value.voucherRange
  // 旧签发 payload 无 voucherRange：按凭证序号兜底（已签发册不回写，A2）
  const seqs = (props.payload.vouchers || []).map((item) => item.voucherSeq).filter(Boolean)
  return { first: seqs[0] || '—', last: seqs[seqs.length - 1] || '—' }
})
const voucherEntry = computed(() =>
  props.page.kind === 'voucher' ? voucherSheetEntry(props.page.voucher) : {},
)

function money(value: string | number | undefined): string {
  return Number(value || 0).toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}
</script>

<template>
  <div class="book-sheet-content">
    <!-- 封面 -->
    <section v-if="page.kind === 'cover'" class="cover-page">
      <div class="cover-company">{{ meta.companyName }}</div>
      <h1 class="cover-title">凭　证　封　面</h1>
      <div class="cover-sub">{{ meta.month }} 月度凭证册</div>
      <table class="cover-table">
        <tbody>
          <tr><th>立 档 单 位</th><td>{{ meta.companyName }}</td></tr>
          <tr><th>凭 证 类 别</th><td>记账凭证</td></tr>
          <tr><th>起 止 日 期</th><td>自 {{ dateCn(meta.periodStart) }} 至 {{ dateCn(meta.periodEnd) }}</td></tr>
          <tr><th>凭 证 册 数</th><td>本月共 1 册　本册是第 1 册</td></tr>
          <tr>
            <th>凭 证 号 数</th>
            <td>本册共 {{ meta.voucherCount }} 号　本册自第 {{ voucherRange.first }} 号至第 {{ voucherRange.last }} 号</td>
          </tr>
          <tr>
            <th>附　　　注</th>
            <td>原件 {{ meta.attachmentTotal }} 份（其中 XML 原件 {{ meta.xmlOriginalTotal }} 份），目录见册内</td>
          </tr>
          <tr><th>档案编号</th><td>全宗号 ＿＿＿ / 目录号 ＿＿＿ / 案卷号 ＿＿＿ / 保管号 ＿＿＿</td></tr>
        </tbody>
      </table>
      <div class="cover-sign">
        <span>财务主管：＿＿＿＿＿</span>
        <span>会计：＿＿＿＿＿</span>
        <span>装订人：＿＿＿＿＿</span>
        <span>保管年限：30 年</span>
      </div>
      <div class="cover-note">
        保管依据：财政部 国家档案局令第 79 号（会计凭证 30 年）· 校验码 {{ (meta.contentHash || '').slice(0, 16) }} · 册号
        {{ bookNoText }}
      </div>
      <div v-if="signed" class="archive-stamp" aria-hidden="true">
        <span>电子归档</span>
        <strong>已签发</strong>
        <small>{{ bookNoText }}</small>
      </div>
    </section>

    <!-- 记账凭证页 -->
    <section v-else-if="page.kind === 'voucher'" class="voucher-page">
      <VoucherSheet
        :entry="voucherEntry"
        :company-name="meta.companyName"
        :attachment-count="page.voucher.attachmentCount"
        :book-mode="true"
      />
    </section>

    <!-- 发票复现页 -->
    <section v-else-if="page.kind === 'invoice'" class="invoice-page">
      <InvoiceReproSheet :attachment="page.attachment" />
    </section>

    <!-- 原始凭证图片页（银行回单 / 支付截图 / 报销单 / 工资表等） -->
    <section v-else-if="page.kind === 'attachment'" class="attachment-page">
      <div class="attachment-head">
        <div class="attachment-title">
          {{ voucherOriginalTypeLabel(page.attachment.documentType) }} · 关联凭证 {{ page.voucher.voucherNo || '—' }}
        </div>
        <div class="attachment-sub">{{ attachmentSubtitle(page.attachment) }}</div>
      </div>
      <div class="attachment-stage">
        <img v-if="previewUrl" :src="previewUrl" :alt="page.attachment.fileName" class="attachment-img" />
        <div v-else class="attachment-placeholder">
          {{ previewLoading ? '正在加载原件图像…' : previewFailed ? '原件图像加载失败，请在凭证详情或原件目录下载查看' : '原件图像仅在系统登录后在线加载（导出 PDF 自动嵌入原图）' }}
        </div>
      </div>
      <div class="attachment-note">
        原始凭证归档件 · 原件 sha256 {{ (page.attachment.contentSha256 || '').slice(0, 16) }} · 文件 {{ page.attachment.fileName }}
      </div>
    </section>

    <!-- 原件归档目录 -->
    <section v-else-if="page.kind === 'originals'" class="originals-page">
      <h2 class="sheet-title">原件归档目录</h2>
      <div class="sheet-sub">
        {{ meta.month }} · 本册原件 {{ meta.attachmentTotal }} 份（XML 原件 {{ meta.xmlOriginalTotal }} 份）· 可在系统内逐件校验下载
      </div>
      <table class="originals-table">
        <thead>
          <tr><th>序号</th><th>凭证号</th><th>发票号</th><th>原件文件名</th><th>类型</th><th>sha256 前8</th><th>大小</th></tr>
        </thead>
        <tbody>
          <tr v-for="(item, index) in page.rows" :key="item.attachmentId">
            <td>{{ page.startIndex + index + 1 }}</td>
            <td>{{ item.voucherNo || '—' }}</td>
            <td class="mono">{{ item.invoiceNumber || '—' }}</td>
            <td class="file-cell">{{ item.fileName }}</td>
            <td>{{ voucherOriginalTypeLabel(item.documentType) }}</td>
            <td class="mono">{{ item.contentSha256.slice(0, 8) }}</td>
            <td>{{ (item.sizeBytes / 1024).toFixed(1) }}K</td>
          </tr>
          <tr v-if="page.rows.length === 0">
            <td colspan="7" class="empty-row">本册没有已归档原件（补传原件后重新签发即可更新目录）</td>
          </tr>
        </tbody>
      </table>
      <div v-if="payload.originalsMissing.length > 0" class="missing-note">
        待补原件凭证：{{ payload.originalsMissing.map((item) => item.voucherNo || item.title).join('、') }}
      </div>
    </section>

    <!-- 装订签章 -->
    <section v-else class="signoff-page">
      <h2 class="sheet-title">装 订 签 章</h2>
      <p>本册装订 {{ meta.voucherCount }} 张已过账记账凭证（{{ meta.month }}，含红冲凭证），借贷合计 {{ money(meta.debitTotal) }} 元。</p>
      <p>
        随册归档原件 {{ meta.attachmentTotal }} 份（XML 原件 {{ meta.xmlOriginalTotal }} 份），清单见「原件归档目录」；原件为含税务机关数字签名的电子文件，
        按财会〔2020〕6号可仅以电子形式归档，保管期限 30 年；系统内可逐件下载并经 sha256 校验。
      </p>
      <p v-if="payload.originalsMissing.length > 0" class="warn">
        ⚠ 本册有 {{ payload.originalsMissing.length }} 张凭证暂无已归档原件（见下方清单），补传原件后重新签发即可更新目录。
      </p>
      <table v-if="payload.originalsMissing.length > 0" class="missing-table">
        <thead><tr><th>凭证号</th><th>日期</th><th>摘要</th></tr></thead>
        <tbody>
          <tr v-for="item in payload.originalsMissing.slice(0, 18)" :key="item.voucherNo + item.date">
            <td>{{ item.voucherNo || '—' }}</td><td>{{ item.date }}</td><td>{{ item.title }}</td>
          </tr>
        </tbody>
      </table>
      <div class="sign-lines">
        <span>装订人：＿＿＿＿＿＿</span><span>财务复核：＿＿＿＿＿＿</span><span>归档日期：＿＿＿＿＿＿</span>
      </div>
      <p class="hash-line">校验码（SHA-256）：{{ meta.contentHash }}</p>
      <p class="hash-line">编制：{{ meta.generatedBy || '—' }}　编制时间：{{ (meta.generatedAt || '').slice(0, 19).replace('T', ' ') || '—' }}</p>
      <div v-if="signed" class="archive-stamp" aria-hidden="true">
        <span>电子归档</span>
        <strong>已签发</strong>
        <small>{{ bookNoText }}</small>
      </div>
    </section>

    <footer class="sheet-footer">
      <span>{{ bookNoText }}</span>
      <span>第 {{ page.pageNo }} 页 共 {{ totalPages }} 页</span>
    </footer>
  </div>
</template>

<style scoped>
.book-sheet-content {
  height: 100%;
  display: flex;
  flex-direction: column;
  color: var(--solo-text-primary);
  font-family: 'Songti SC', 'SimSun', 'Noto Serif SC', serif;
  font-size: 14px;
}
.cover-page {
  position: relative;
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  margin: 2px 4px 8px;
  padding: 20px 30px 16px;
  border: 2px solid rgba(63, 52, 42, 0.56);
  outline: 1px solid rgba(63, 52, 42, 0.32);
  outline-offset: -6px;
}
.cover-company { font-size: 18px; text-align: center; letter-spacing: 0.6px; }
.cover-title {
  margin: 20px 0 0;
  text-align: center;
  font-family: 'KaiTi', 'STKaiti', 'Kaiti SC', serif;
  font-size: 38px;
  font-weight: 600;
  letter-spacing: 2px;
  border-bottom: 4px double rgba(63, 52, 42, 0.72);
  align-self: center;
  padding: 0 22px 8px;
}
.cover-sub { text-align: center; margin: 10px 0 20px; font-size: 16px; }
.cover-table {
  width: 90%;
  margin: 0 auto;
  border-collapse: collapse;
  font-size: 13.5px;
  border: 1.5px solid rgba(63, 52, 42, 0.58);
}
.cover-table th,
.cover-table td { border: 1px solid rgba(63, 52, 42, 0.45); padding: 7px 10px; text-align: left; }
.cover-table th {
  width: 21%;
  font-weight: 600;
  white-space: nowrap;
  background: rgba(63, 52, 42, 0.055);
}
.cover-sign {
  width: 90%;
  margin: 18px auto 0;
  display: flex;
  justify-content: space-between;
  font-size: 13.5px;
}
.cover-note { width: 90%; margin: auto auto 0; padding-top: 10px; color: var(--solo-text-secondary); font-size: 11px; }
.archive-stamp {
  position: absolute;
  right: 28px;
  bottom: 24px;
  width: 84px;
  height: 84px;
  border-radius: 50%;
  border: 2px solid var(--solo-doc-stamp);
  outline: 1px solid var(--solo-doc-stamp);
  outline-offset: -5px;
  color: var(--solo-doc-stamp);
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 1px;
  transform: rotate(-8deg);
  opacity: 0.72;
  pointer-events: none;
  user-select: none;
  font-family: 'KaiTi', 'STKaiti', 'Kaiti SC', serif;
}
.archive-stamp span { font-size: 10px; letter-spacing: 1px; }
.archive-stamp strong { font-size: 14px; font-weight: 700; letter-spacing: 1.5px; line-height: 1.1; }
.archive-stamp small { font-size: 8.5px; font-family: var(--solo-font-mono); letter-spacing: 0.2px; }
.voucher-page { width: 100%; flex: 1; min-height: 0; display: flex; align-items: flex-start; }
.voucher-page :deep(.voucher-company) { font-size: 15px; }
.voucher-page :deep(.voucher-title) { font-size: 24px; letter-spacing: 8px; }
.voucher-page :deep(.voucher-table) { font-size: 13.5px; }
.invoice-page { flex: 1; min-height: 0; display: flex; }
.invoice-page :deep(.invoice-repro) { width: 100%; }
.attachment-page {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 1.5% 3%;
  gap: 10px;
}
.attachment-head { display: flex; flex-direction: column; gap: 3px; }
.attachment-title { font-size: 16px; font-weight: 700; color: var(--solo-doc-ink); }
.attachment-sub { font-size: 12px; color: var(--solo-text-muted); }
.attachment-stage {
  flex: 1;
  min-height: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px dashed var(--solo-border);
  background: var(--solo-surface-soft);
  padding: 10px;
  overflow: hidden;
}
.attachment-img {
  max-width: 100%;
  max-height: 100%;
  object-fit: contain;
  display: block;
}
.attachment-placeholder { font-size: 12.5px; color: var(--solo-text-muted); text-align: center; }
.attachment-note { font-size: 11px; color: var(--solo-text-muted); }
.originals-page, .signoff-page { position: relative; flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 2% 3%; }
.sheet-title {
  margin: 0 0 8px;
  align-self: center;
  text-align: center;
  font-family: 'KaiTi', 'STKaiti', 'Kaiti SC', serif;
  font-size: 26px;
  font-weight: 600;
  letter-spacing: 3px;
  color: var(--solo-doc-ink);
  padding: 0 18px 6px;
  border-bottom: 3px double rgba(44, 62, 85, 0.55);
}
.sheet-sub { text-align: center; color: var(--solo-text-muted); font-size: 12px; margin-bottom: 14px; }
.originals-table { width: 100%; border-collapse: collapse; font-size: 12.5px; border: 1.5px solid var(--solo-doc-ink); }
.originals-table th,
.originals-table td { border: 1px solid rgba(44, 62, 85, 0.42); padding: 6px 8px; text-align: left; }
.originals-table th { background: rgba(44, 62, 85, 0.045); color: var(--solo-doc-ink); font-weight: 600; }
.mono { font-family: 'Courier New', Courier, monospace; }
.file-cell { max-width: 300px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.empty-row { text-align: center; color: var(--solo-text-muted); }
.missing-note { margin-top: 12px; font-size: 12px; color: var(--solo-warning); }
.signoff-page p { margin: 0 0 10px; line-height: 1.8; font-size: 14px; }
.signoff-page .warn { color: var(--solo-warning); }
.missing-table { width: 100%; border-collapse: collapse; font-size: 12.5px; margin: 6px 0 16px; border: 1.5px solid var(--solo-doc-ink); }
.missing-table th,
.missing-table td { border: 1px solid rgba(44, 62, 85, 0.42); padding: 5px 8px; text-align: left; }
.missing-table th { background: rgba(44, 62, 85, 0.045); color: var(--solo-doc-ink); font-weight: 600; }
.sign-lines { display: flex; justify-content: space-between; margin-top: 26px; font-size: 14px; color: var(--solo-doc-ink); }
.hash-line { color: var(--solo-text-muted); font-size: 11px; margin: 6px 0 0 !important; word-break: break-all; }
.sheet-footer {
  margin-top: auto;
  padding-top: 8px;
  padding-right: 40px;
  border-top: 1px solid rgba(44, 62, 85, 0.22);
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: var(--solo-text-muted);
}
</style>
