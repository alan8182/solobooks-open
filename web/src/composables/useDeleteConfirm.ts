/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { useDialog } from 'naive-ui'

interface DeleteConfirmOptions {
  content: string
  onConfirm: () => void | Promise<void>
  title?: string
  positiveText?: string
}

export function useDeleteConfirm() {
  const dialog = useDialog()

  function confirmDelete(options: DeleteConfirmOptions) {
    dialog.warning({
      title: options.title || '确认删除',
      content: options.content,
      positiveText: options.positiveText || '确认删除',
      negativeText: '取消',
      positiveButtonProps: { type: 'error' },
      maskClosable: false,
      onPositiveClick: () => options.onConfirm(),
    })
  }

  return { confirmDelete }
}
