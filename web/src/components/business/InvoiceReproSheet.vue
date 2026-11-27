<!-- Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司. Licensed under the SoloBooks License v2.0. -->
<script setup lang="ts">
/**
 * 数电发票复现页（仿真册用）：版式对齐电子税务局官方 PDF（#800000 双框、
 * 楷体标题、购销竖排标签、无网格明细表、⊗ 价税合计、备注、开票人）。
 * 字段来自 XML 解析结果；底部强制复现声明，不伪造税局签章与二维码。
 */
import { computed } from 'vue'
import type { VoucherBookVoucherAttachment } from '@/api/voucherBook'

const props = defineProps<{
  attachment: VoucherBookVoucherAttachment
}>()

const parsed = computed(() => props.attachment.parsed!)
const issueCn = computed(() => {
  const parts = String(parsed.value.issueTime || '').split('-')
  if (parts.length !== 3) return parsed.value.issueTime || '—'
  return `${parts[0]}年${Number(parts[1])}月${Number(parts[2])}日`
})
const agentName = computed(() => {
  const agent = parsed.value.specific?.AgentIssuingInvoice?.fields
  return agent?.AgentIssuingName || ''
})
const agentCode = computed(() => {
  const agent = parsed.value.specific?.AgentIssuingInvoice?.fields
  return agent?.AgentIssuingIdCode || ''
})
const allTaxMasked = computed(
  () => parsed.value.items.length > 0 && parsed.value.items.every((item) => !String(item.taxAmount || '').trim()),
)
const items = computed(() => parsed.value.items.slice(0, 12))
const moreItems = computed(() => Math.max(0, parsed.value.items.length - 12))
</script>

<template>
  <div class="invoice-repro">
    <div class="invoice-frame">
      <div class="invoice-head">
        <div class="head-left">
          <div class="qr-box">
            <span>二维码<br />见原件</span>
          </div>
          <span v-if="agentName" class="agent-label">税务代开</span>
          <span v-if="!parsed.isBlue" class="red-label">红字发票</span>
        </div>
        <div class="head-center">
          <div class="invoice-title">电子发票（{{ parsed.generalOrSpecial || '普通发票' }}）</div>
          <div class="title-rule"></div>
        </div>
        <div class="head-right">
          <div><span class="lbl">发票号码：</span>{{ parsed.invoiceNumber || '—' }}</div>
          <div><span class="lbl">开票日期：</span>{{ issueCn }}</div>
          <div v-if="parsed.hasTaxBureauSignature" class="sig-note">原件含税务机关数字签名</div>
        </div>
      </div>

      <div class="invoice-parties">
        <div class="party">
          <div class="vlabel"><span>购买方信息</span></div>
          <div class="party-body">
            <div><span class="lbl">名称：</span>{{ parsed.buyer.name || '—' }}</div>
            <div class="tax-line">
              <span class="lbl">统一社会信用代码/纳税人识别号：</span><span class="mono">{{ parsed.buyer.taxId }}</span>
            </div>
          </div>
        </div>
        <div class="party">
          <div class="vlabel"><span>销售方信息</span></div>
          <div class="party-body">
            <div><span class="lbl">名称：</span>{{ parsed.seller.name || '—' }}</div>
            <div class="tax-line">
              <span class="lbl">统一社会信用代码/纳税人识别号：</span><span class="mono">{{ parsed.seller.taxId }}</span>
            </div>
          </div>
        </div>
      </div>

      <div class="invoice-detail">
        <div class="detail-row detail-head">
          <span>项目名称</span><span>规格型号</span><span>单 位</span><span>数 量</span>
          <span>单 价</span><span class="num">金 额</span><span>税率/征收率</span><span class="num">税 额</span>
        </div>
        <div v-for="(item, index) in items" :key="index" class="detail-row">
          <span class="item-name">{{ item.itemName }}</span>
          <span>{{ item.spec }}</span>
          <span class="c">{{ item.unit }}</span>
          <span class="c">{{ item.quantity }}</span>
          <span class="c">{{ item.unitPrice }}</span>
          <span class="num">{{ item.amount }}</span>
          <span class="c">{{ item.taxRate }}</span>
          <span class="num">{{ item.taxAmount || '***' }}</span>
        </div>
        <div v-if="moreItems > 0" class="detail-more">另有 {{ moreItems }} 行完整明细见原件</div>
        <div class="detail-row detail-total">
          <span class="c">合　计</span><span></span><span></span><span></span><span></span>
          <span class="num">{{ parsed.amounts.excl }}</span><span></span>
          <span class="num">{{ allTaxMasked ? '***' : parsed.amounts.tax }}</span>
        </div>
      </div>

      <div class="invoice-grand-total">
        <span class="gt-label">价税合计（大写）</span>
        <span class="gt-mark">⊗</span>
        <span class="gt-chinese">{{ parsed.amounts.inclChinese }}</span>
        <span class="gt-small">（小写）<span class="mono">¥{{ parsed.amounts.incl }}</span></span>
      </div>

      <div class="invoice-remark">
        <div class="vlabel"><span>备　注</span></div>
        <div class="remark-body">
          <div v-if="agentName">代开发票税务机构代码:{{ agentCode }};　　代开发票税务机构名称:{{ agentName }}</div>
          <div v-if="parsed.remark">{{ parsed.remark }}</div>
        </div>
      </div>

      <div class="invoice-drawer">开票人：{{ parsed.drawer || '—' }}</div>
    </div>
    <div class="invoice-disclaimer">
      本页由 XML 数据原件渲染，非原始凭证 · 原件 sha256 {{ attachment.contentSha256.slice(0, 16) }} · 可在系统查验下载 · 文件
      {{ attachment.fileName }}
    </div>
  </div>
</template>

<style scoped>
.invoice-repro {
  display: flex;
  flex-direction: column;
  height: 100%;
  color: var(--solo-text-primary);
  font-family: 'Songti SC', 'SimSun', 'Noto Serif SC', serif;
}
.invoice-frame {
  flex: 1;
  display: flex;
  flex-direction: column;
  border: 2px solid var(--solo-doc-red);
  outline: 1px solid var(--solo-doc-red);
  outline-offset: -5px;
  padding: 3.2% 3.6%;
  min-height: 0;
}
.invoice-head {
  display: grid;
  grid-template-columns: 13% 1fr 30%;
  align-items: start;
  gap: 8px;
}
.head-left { display: flex; flex-direction: column; gap: 6px; align-items: center; }
.qr-box {
  width: 88px;
  height: 88px;
  border: 1px dashed var(--solo-text-muted);
  display: grid;
  place-items: center;
  text-align: center;
  font-size: 11px;
  line-height: 1.4;
  color: var(--solo-text-muted);
}
.agent-label { font-size: 12px; }
.red-label { color: var(--solo-doc-red); font-weight: 600; font-size: 12px; }
.head-center { text-align: center; padding-top: 4px; }
.invoice-title {
  font-family: 'KaiTi', 'STKaiti', 'Kaiti SC', serif;
  font-size: 30px;
  color: var(--solo-doc-red);
  letter-spacing: 1px;
  white-space: nowrap;
}
.title-rule {
  width: 62%;
  margin: 6px auto 0;
  border-top: 3px double var(--solo-doc-red);
}
.head-right { text-align: right; font-size: 12.5px; line-height: 1.9; color: var(--solo-doc-red); }
.head-right .lbl { color: var(--solo-doc-red); }
.sig-note { font-size: 10.5px; color: var(--solo-income); line-height: 1.5; }
.invoice-parties {
  display: grid;
  grid-template-columns: 1fr 1fr;
  margin-top: 10px;
  border-top: 1px solid var(--solo-doc-red);
  border-bottom: 1px solid var(--solo-doc-red);
  font-size: 12.5px;
}
.party { display: flex; min-width: 0; }
.party + .party { border-left: 1px solid var(--solo-doc-red); }
.vlabel {
  width: 26px;
  flex: 0 0 26px;
  border-right: 1px solid var(--solo-doc-red);
  display: grid;
  place-items: center;
  color: var(--solo-doc-red);
  font-size: 12px;
}
.vlabel span { writing-mode: vertical-rl; text-orientation: upright; letter-spacing: 3px; }
.party-body { padding: 8px 10px; display: flex; flex-direction: column; gap: 12px; min-width: 0; }
.party-body .lbl, .invoice-head .lbl { color: var(--solo-doc-red); }
.tax-line { display: flex; gap: 0; align-items: baseline; min-width: 0; }
.mono { font-family: 'Courier New', Courier, monospace; font-size: 13.5px; letter-spacing: 0.3px; }
.invoice-detail { padding: 8px 0 0; font-size: 12.5px; }
.detail-row {
  display: grid;
  grid-template-columns: 26% 10% 7% 7% 10% 12% 10% 10%;
  align-items: baseline;
  padding: 3px 0;
}
.detail-head { color: var(--solo-doc-red); }
.detail-row .c { text-align: center; }
.detail-row .num { text-align: right; }
.item-name { overflow-wrap: anywhere; }
.detail-more { color: var(--solo-text-muted); font-size: 11.5px; padding: 2px 0; }
.detail-total { font-weight: 600; }
.invoice-grand-total {
  display: grid;
  grid-template-columns: 17% 5% 1fr auto;
  align-items: center;
  border-top: 1px solid var(--solo-doc-red);
  border-bottom: 1px solid var(--solo-doc-red);
  padding: 6px 0;
  font-size: 12.5px;
  gap: 4px;
}
.gt-label { color: var(--solo-doc-red); }
.gt-mark { text-align: center; font-size: 15px; }
.gt-chinese { padding-left: 6px; }
.gt-small { text-align: right; padding-right: 4px; }
.invoice-remark {
  flex: 1;
  display: flex;
  min-height: 64px;
  border-bottom: 1px solid var(--solo-doc-red);
  font-size: 12px;
}
.remark-body { padding: 8px 10px; display: flex; flex-direction: column; gap: 6px; line-height: 1.5; min-width: 0; }
.invoice-drawer { padding: 10px 2px 2px; font-size: 12.5px; }
.invoice-drawer::first-letter { color: var(--solo-doc-red); }
.invoice-disclaimer {
  margin-top: 6px;
  font-size: 10px;
  color: var(--solo-text-muted);
  line-height: 1.5;
}
</style>
