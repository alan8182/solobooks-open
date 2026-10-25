/**
 * Copyright (c) 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

/**
 * 术语表：一处定义，三处消费（SoloFieldHelp 的 tooltip、/guide 术语表、AI 提示词占位）。
 *
 * 文案原则：「老板话」优先于会计教科书。
 * - `plain` 是一句话解释，必须短到能塞进 tooltip（≤50 字）；
 * - `detail` 是 guide 页展开的第二句，tooltip 不用它；
 * - `see` 是可选深链，guide 页渲染成「去看看 →」。
 */
export interface GlossaryEntry {
  term: string
  plain: string
  detail?: string
  see?: string
}

export const GLOSSARY: GlossaryEntry[] = [
  {
    term: '期初余额',
    plain: '开始记账那天，公司手里已有的钱和欠的款，决定报表从第一天起准不准。',
    detail: '从旧账套导过来的，填一次就不用管。',
    see: '/mine/opening',
  },
  {
    term: '末级科目',
    plain: '科目树最末端的那个，只有它能记账，选了有下级的系统会拦住。',
    detail: '带下级的叫容器科目，比如「应付职工薪酬」下面还有工资、社保。',
    see: '/mine/accounts',
  },
  {
    term: '计提',
    plain: '钱还没付/没收，但这个月已经发生的成本和费用，先记进账里。',
    detail: '折旧、无形资产摊销都是系统自动计提的，不用手工算。',
  },
  {
    term: '暂估入账',
    plain: '货到了发票没到，先按合同价记入账，票到了再改成实际数。',
    detail: '次月初系统会自动提醒红冲。',
    see: '/vouchers',
  },
  {
    term: '销项',
    plain: '你开出去的发票，代表你要交的增值税。',
    see: '/invoices',
  },
  {
    term: '进项',
    plain: '你收进来的发票，代表你可以抵扣的增值税。',
    see: '/invoices',
  },
  {
    term: '纳税义务时间',
    plain: '这笔收入「什么时候算该交税」，不等于你什么时候收到钱。',
    detail: '一般看合同约定或发货开票节点，结账向导会按这个口径提示。',
  },
  {
    term: '未开票收入',
    plain: '钱已经收到了、但还没开发票的收入，也要先报税。',
    detail: '开了票之后系统会自动对冲，不会重复交。',
  },
  {
    term: '往来单位',
    plain: '跟你有资金往来的客户和供应商，一个台账两头管。',
    see: '/mine/contacts',
  },
  {
    term: '冲销',
    plain: '把记错或临时记的账用一笔相反的分录抵消掉，账本不留涂改。',
    detail: '已过账的凭证不能直接改，只能冲销后重做，这是会计底线。',
    see: '/vouchers',
  },
]

export function lookupTerm(term: string): GlossaryEntry | undefined {
  const key = term.trim()
  if (!key) return undefined
  return GLOSSARY.find((entry) => entry.term === key)
}
