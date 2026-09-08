import { beforeEach, describe, expect, it, vi } from 'vitest'

const { warning } = vi.hoisted(() => ({ warning: vi.fn() }))

vi.mock('naive-ui', () => ({
  useDialog: () => ({ warning }),
}))

import { useDeleteConfirm } from '@/composables/useDeleteConfirm'

describe('useDeleteConfirm', () => {
  beforeEach(() => warning.mockReset())

  it('requires an explicit destructive confirmation before running delete', async () => {
    const onConfirm = vi.fn().mockResolvedValue(undefined)
    const { confirmDelete } = useDeleteConfirm()

    confirmDelete({ content: '确定删除测试记录吗？', onConfirm })

    expect(onConfirm).not.toHaveBeenCalled()
    expect(warning).toHaveBeenCalledOnce()
    const options = warning.mock.calls[0][0]
    expect(options).toMatchObject({
      title: '确认删除',
      content: '确定删除测试记录吗？',
      positiveText: '确认删除',
      negativeText: '取消',
      maskClosable: false,
    })

    await options.onPositiveClick()
    expect(onConfirm).toHaveBeenCalledOnce()
  })
})
