/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

// 邮件收件箱卡片单测。
//
// 覆盖的是最容易出数据事故的三处：
//   ① 「导入」只把附件交给现有解析向导（emit），组件本身不做任何入账动作——
//      确认权属于用户（PRODUCT 原则），这条边界破了就是绕过确认直接改账。
//   ② unknown 类型必须先让用户选，猜错类型会把银行流水喂给发票解析器。
//   ③ 导入后标记 consumed 且不重复标记——重复防线在业务层，组件层不再加戏。

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import MailInboxCard from '@/components/business/MailInboxCard.vue'
import type { InboxDocumentItem } from '@/api/mailbox'

const apiMocks = vi.hoisted(() => ({
  fetchMailbox: vi.fn(),
  resetMailboxAddress: vi.fn(),
  fetchInboxDocuments: vi.fn(),
  fetchInboxDocumentFile: vi.fn(),
  markInboxDocumentConsumed: vi.fn(),
  deleteInboxDocument: vi.fn(),
}))

vi.mock('@/api/mailbox', () => apiMocks)

function doc(overrides: Partial<InboxDocumentItem> = {}): InboxDocumentItem {
  return {
    id: 'doc-1',
    filename: '销项发票.xlsx',
    fileExt: 'xlsx',
    sizeBytes: 2048,
    importKind: 'invoice',
    status: 'pending',
    note: '',
    source: 'email',
    emailFrom: 'noreply@chinatax.gov.cn',
    emailSubject: '发票清单',
    receivedAt: '2026-11-06T10:00:00+08:00',
    consumedAt: null,
    ...overrides,
  }
}

function mountCard() {
  return mount(MailInboxCard, {
    global: {
      // naive-ui 组件未引，卡片本身只用原生元素，无需插件
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.fetchMailbox.mockResolvedValue({
    enabled: true,
    addressCode: 'abc234def56',
    domain: 'in.solobooks.cn',
    address: 'abc234def56@in.solobooks.cn',
  })
  apiMocks.fetchInboxDocumentFile.mockResolvedValue(new Blob(['PK\x03\x04fake'], { type: 'application/octet-stream' }))
  apiMocks.markInboxDocumentConsumed.mockImplementation(async (id: string) => ({ id, status: 'consumed' }))
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('MailInboxCard', () => {
  it('后端不可用时给出可重试提示，不再整卡消失', async () => {
    apiMocks.fetchMailbox.mockRejectedValueOnce(new Error('500'))
    apiMocks.fetchInboxDocuments.mockRejectedValueOnce(new Error('500'))
    const wrapper = mountCard()
    await flushPromises()
    expect(wrapper.find('.mail-inbox-card').exists()).toBe(true)
    expect(wrapper.text()).toContain('邮箱收单服务暂时不可用')
    expect(wrapper.text()).toContain('服务不可用')

    // 重试：服务恢复后展示地址与空态提示，错误提示消失
    apiMocks.fetchInboxDocuments.mockResolvedValue({ items: [] })
    await wrapper.find('.inbox-error .inbox-link-btn').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('abc234def56@in.solobooks.cn')
    expect(wrapper.text()).toContain('附件会出现在这里')
    expect(wrapper.find('.inbox-error').exists()).toBe(false)
  })

  it('地址可用但附件列表失败时仍展示地址，并提示可重试', async () => {
    apiMocks.fetchInboxDocuments.mockRejectedValueOnce(new Error('500'))
    const wrapper = mountCard()
    await flushPromises()
    expect(wrapper.text()).toContain('abc234def56@in.solobooks.cn')
    expect(wrapper.text()).toContain('邮箱收单服务暂时不可用')
    expect(wrapper.text()).not.toContain('附件会出现在这里')
  })

  it('展示专属地址与收件列表', async () => {
    apiMocks.fetchInboxDocuments.mockResolvedValue({ items: [doc()] })
    const wrapper = mountCard()
    await flushPromises()
    expect(wrapper.text()).toContain('abc234def56@in.solobooks.cn')
    expect(wrapper.text()).toContain('销项发票.xlsx')
    expect(wrapper.text()).toContain('noreply@chinatax.gov.cn')
  })

  it('点击导入：构造原始文件名的 File 并 emit，pending 状态随后标记 consumed', async () => {
    apiMocks.fetchInboxDocuments.mockResolvedValue({ items: [doc()] })
    const wrapper = mountCard()
    await flushPromises()

    await wrapper.find('.inbox-link-btn.is-primary').trigger('click')
    await flushPromises()

    const events = wrapper.emitted('import')
    expect(events).toHaveLength(1)
    const payload = events![0][0] as { kind: string; file: File }
    expect(payload.kind).toBe('invoice')
    expect(payload.file.name).toBe('销项发票.xlsx')
    expect(apiMocks.fetchInboxDocumentFile).toHaveBeenCalledWith('doc-1')
    expect(apiMocks.markInboxDocumentConsumed).toHaveBeenCalledWith('doc-1')
    expect(wrapper.text()).toContain('已处理')
  })

  it('再次点击导入不重复标记 consumed（重复防线交给业务层去重）', async () => {
    apiMocks.fetchInboxDocuments.mockResolvedValue({ items: [doc()] })
    const wrapper = mountCard()
    await flushPromises()

    const btn = wrapper.find('.inbox-link-btn.is-primary')
    await btn.trigger('click')
    await flushPromises()
    await btn.trigger('click')
    await flushPromises()

    expect(wrapper.emitted('import')).toHaveLength(2)
    expect(apiMocks.markInboxDocumentConsumed).toHaveBeenCalledTimes(1)
  })

  it('unknown 类型必须先选单据类型，选错入口会喂错解析器', async () => {
    apiMocks.fetchInboxDocuments.mockResolvedValue({ items: [doc({ id: 'doc-2', filename: '随手导出.csv', importKind: 'unknown' })] })
    const wrapper = mountCard()
    await flushPromises()

    await wrapper.find('.inbox-link-btn.is-primary').trigger('click')
    await flushPromises()
    // 未选择类型前不得 emit
    expect(wrapper.emitted('import')).toBeUndefined()

    const buttons = wrapper.findAll('.inbox-item-actions .inbox-link-btn')
    const asBank = buttons.find((b) => b.text() === '作为银行流水')
    expect(asBank).toBeTruthy()
    await asBank!.trigger('click')
    await flushPromises()

    const events = wrapper.emitted('import')
    expect(events).toHaveLength(1)
    expect((events![0][0] as { kind: string }).kind).toBe('bank')
  })

  it('unsupported（如加密压缩包）不提供导入入口，note 引导手动处理', async () => {
    apiMocks.fetchInboxDocuments.mockResolvedValue({
      items: [doc({ id: 'doc-3', filename: '银行流水.zip', fileExt: 'zip', status: 'unsupported', note: '压缩包暂不支持自动导入，请下载后手动上传' })],
    })
    const wrapper = mountCard()
    await flushPromises()

    expect(wrapper.text()).toContain('压缩包暂不支持自动导入')
    const importBtn = wrapper.find('.inbox-link-btn.is-primary')
    expect(importBtn.exists()).toBe(false)
    expect(wrapper.text()).toContain('下载')
  })

  it('删除附件走确认弹窗并从列表移除', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true)
    apiMocks.fetchInboxDocuments.mockResolvedValue({ items: [doc()] })
    apiMocks.deleteInboxDocument.mockResolvedValue({ deleted: true })
    const wrapper = mountCard()
    await flushPromises()

    const del = wrapper.findAll('.inbox-link-btn').find((b) => b.text() === '删除')
    await del!.trigger('click')
    await flushPromises()

    expect(confirmSpy).toHaveBeenCalled()
    expect(apiMocks.deleteInboxDocument).toHaveBeenCalledWith('doc-1')
    expect(wrapper.text()).not.toContain('销项发票.xlsx')
  })

  it('重置地址前必须确认，旧地址失效语义要讲清楚', async () => {
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false)
    apiMocks.fetchInboxDocuments.mockResolvedValue({ items: [] })
    const wrapper = mountCard()
    await flushPromises()

    const reset = wrapper.findAll('.inbox-link-btn').find((b) => b.text() === '重置')
    await reset!.trigger('click')
    expect(confirmSpy).toHaveBeenCalled()
    expect(apiMocks.resetMailboxAddress).not.toHaveBeenCalled()

    confirmSpy.mockReturnValue(true)
    apiMocks.resetMailboxAddress.mockResolvedValue({
      enabled: true,
      addressCode: 'newcode9999',
      domain: 'in.solobooks.cn',
      address: 'newcode9999@in.solobooks.cn',
    })
    await reset!.trigger('click')
    await flushPromises()
    expect(apiMocks.resetMailboxAddress).toHaveBeenCalled()
    expect(wrapper.text()).toContain('newcode9999@in.solobooks.cn')
  })

  it('kinds 过滤：限定入口只显示该类型与 unknown，非本类型附件不出现', async () => {
    apiMocks.fetchInboxDocuments.mockResolvedValue({
      items: [
        doc({ id: 'doc-bank', filename: '对账单.csv', fileExt: 'csv', importKind: 'bank' }),
        doc({ id: 'doc-inv', filename: '销项发票.xlsx', importKind: 'invoice' }),
        doc({ id: 'doc-unknown', filename: '随手导出.csv', fileExt: 'csv', importKind: 'unknown' }),
      ],
    })
    const wrapper = mount(MailInboxCard, { props: { kinds: ['bank'] } })
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('对账单.csv')
    expect(text).toContain('随手导出.csv')
    expect(text).not.toContain('销项发票.xlsx')
  })

  it('限定入口下 unknown 只给本入口的类型选项（银行弹窗不会把文件喂给发票解析器）', async () => {
    apiMocks.fetchInboxDocuments.mockResolvedValue({
      items: [doc({ id: 'doc-u', filename: '随手导出.csv', fileExt: 'csv', importKind: 'unknown' })],
    })
    const wrapper = mount(MailInboxCard, { props: { kinds: ['bank'] } })
    await flushPromises()

    await wrapper.find('.inbox-link-btn.is-primary').trigger('click')
    await flushPromises()

    const labels = wrapper.findAll('.inbox-item-actions .inbox-link-btn').map((b) => b.text())
    expect(labels).toContain('作为银行流水')
    expect(labels).not.toContain('作为发票')
    expect(labels).not.toContain('作为微信账单')

    const asBank = wrapper.findAll('.inbox-item-actions .inbox-link-btn').find((b) => b.text() === '作为银行流水')
    await asBank!.trigger('click')
    await flushPromises()
    const events = wrapper.emitted('import')
    expect((events![0][0] as { kind: string }).kind).toBe('bank')
  })

  it('collapsible 模式：无论本地是否配置 IMAP，空收件箱时始终保留轻量胶囊入口，点击展开并支持收起', async () => {
    apiMocks.fetchMailbox.mockResolvedValueOnce({
      enabled: false,
      addressCode: '13800000000-2',
      domain: 'solobooks.cn',
      address: '13800000000-2@solobooks.cn',
    })
    apiMocks.fetchInboxDocuments.mockResolvedValueOnce({ items: [] })
    const wrapper = mount(MailInboxCard, { props: { collapsible: true } })
    await flushPromises()

    expect(wrapper.find('.inbox-collapsed-trigger').exists()).toBe(true)
    expect(wrapper.text()).toContain('邮箱免传收单')
    expect(wrapper.text()).not.toContain('13800000000-2@solobooks.cn')

    await wrapper.find('.inbox-collapsed-trigger').trigger('click')
    expect(wrapper.text()).toContain('13800000000-2@solobooks.cn')
    expect(wrapper.find('.inbox-collapse-btn').exists()).toBe(true)

    await wrapper.find('.inbox-collapse-btn').trigger('click')
    expect(wrapper.find('.inbox-collapsed-trigger').exists()).toBe(true)
  })

  it('collapsible 模式：有收件附件时自动展开列表', async () => {
    apiMocks.fetchInboxDocuments.mockResolvedValueOnce({ items: [doc()] })
    const wrapper = mount(MailInboxCard, { props: { collapsible: true } })
    await flushPromises()

    expect(wrapper.find('.inbox-collapsed-trigger').exists()).toBe(false)
    expect(wrapper.text()).toContain('销项发票.xlsx')
    expect(wrapper.text()).toContain('收件箱 1 份')
  })
})
