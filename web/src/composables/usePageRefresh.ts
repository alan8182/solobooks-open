/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { onActivated } from 'vue'

/**
 * 页内多标签的「静默刷新」。
 *
 * 标签用 <KeepAlive> 保活，切回来草稿、滚动、展开状态都还在；但会计系统的
 * 数字会被别处改掉（另一张标签里记了账、同事过了账），保活太久就会对着旧
 * 余额干活。所以每次重新激活都静默拉一次数据——不动筛选、不闪骨架、不弹提示。
 *
 * 只在被 KeepAlive 缓存的页面上生效；未被缓存的页面 onActivated 不会触发，
 * 页面自身的 onMounted 已经负责首次加载，所以这里跳过第一次激活，避免首屏双请求。
 */
export function usePageRefresh(refresh: () => unknown): void {
  let primed = false
  onActivated(() => {
    if (!primed) {
      primed = true
      return
    }
    void refresh()
  })
}
