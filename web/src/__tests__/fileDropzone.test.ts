/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { describe, expect, it, vi, afterEach } from 'vitest'
import { enableAutoUnmount, mount } from '@vue/test-utils'

import FileDropzone from '@/components/business/FileDropzone.vue'

enableAutoUnmount(afterEach)

function makeFile(name: string, bytes = 8): File {
  return new File([new Uint8Array(bytes)], name)
}

function mountDropzone(props: Record<string, unknown> = {}) {
  return mount(FileDropzone, {
    props: { accept: '.xlsx,.xls,.csv', title: '上传文件', ...props },
  })
}

describe('FileDropzone', () => {
  it('emits file when a supported file is dropped', async () => {
    const wrapper = mountDropzone()
    const file = makeFile('发票.xlsx')

    await wrapper.find('.file-dropzone').trigger('drop', { dataTransfer: { files: [file] } })

    const emitted = wrapper.emitted('file')
    expect(emitted).toHaveLength(1)
    expect(emitted?.[0][0]).toBe(file)
  })

  it('rejects unsupported extensions', async () => {
    const wrapper = mountDropzone()
    await wrapper.find('.file-dropzone').trigger('drop', { dataTransfer: { files: [makeFile('账单.txt')] } })

    expect(wrapper.emitted('file')).toBeUndefined()
    expect(String(wrapper.emitted('error')?.[0][0])).toContain('仅支持')
  })

  it('rejects files over the size limit', async () => {
    const wrapper = mountDropzone({ maxSizeMb: 0.001 })
    await wrapper.find('.file-dropzone').trigger('drop', { dataTransfer: { files: [makeFile('大文件.csv', 4096)] } })

    expect(wrapper.emitted('file')).toBeUndefined()
    expect(String(wrapper.emitted('error')?.[0][0])).toContain('MB')
  })

  it('toggles the active class while dragging', async () => {
    const wrapper = mountDropzone()
    const zone = wrapper.find('.file-dropzone')

    await zone.trigger('dragenter')
    expect(wrapper.find('.uploader-zone').classes()).toContain('active')

    await zone.trigger('dragleave')
    expect(wrapper.find('.uploader-zone').classes()).not.toContain('active')
  })

  it('ignores drops while loading', async () => {
    const wrapper = mountDropzone({ loading: true })
    await wrapper.find('.file-dropzone').trigger('drop', { dataTransfer: { files: [makeFile('a.csv')] } })
    expect(wrapper.emitted('file')).toBeUndefined()
  })

  it('opens the hidden input when clicked', async () => {
    const wrapper = mountDropzone()
    const input = wrapper.find('input[type="file"]')
    const spy = vi.spyOn(input.element as HTMLInputElement, 'click')
    await wrapper.find('.uploader-zone').trigger('click')
    expect(spy).toHaveBeenCalled()
  })
})
