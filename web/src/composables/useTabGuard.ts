/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { onActivated, onBeforeUnmount, onMounted, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useTabsStore } from '@/stores/tabs'
import { resolveActiveTab } from '@/utils/tabPages'

/**
 * 登记「离开这个标签前必须先确认」。
 *
 * 记账最怕的是填了一半被关掉：标签栏给了用户随手关页面的新入口，就得配一个
 * 拦截。页面只需说明「我现在算不算有未保存内容」，关标签的弹窗由标签栏统一出；
 * 能顺手存成草稿的，再给一个 save，弹窗里就多一个「存草稿并关闭」。
 *
 * 守卫挂在**最近的父级标签**上：在 /vouchers/123 详情里改辅助核算，关的
 * 是「记账凭证」这个标签，所以拦截要记在它头上。
 */
export function useTabGuard(
  isDirty: () => boolean,
  options: { save?: () => void | Promise<void> } = {},
): void {
  const route = useRoute()
  const tabsStore = useTabsStore()
  let unregister: (() => void) | null = null

  function bind() {
    unregister?.()
    unregister = null
    const key = resolveActiveTab(route.path)
    if (!key) return
    unregister = tabsStore.registerGuard(key, { isDirty, save: options.save })
  }

  onMounted(bind)
  // 被 KeepAlive 缓存过的页面再回来时不会重新 mount，只走 activated；
  // 不在这里补一次登记，从详情页返回列表后标签栏就找不到守卫了。
  onActivated(bind)
  watch(() => route.path, bind)
  onBeforeUnmount(() => {
    unregister?.()
    unregister = null
  })
}
