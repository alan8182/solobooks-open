/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { ref } from 'vue'
import { getEmployees, type Employee } from '@/api/salary'

/**
 * 垫付人名单（付款方 = 员工垫付时的二级下拉）。
 *
 * 只列在职员工，且**排除 is_owner=1 的人**：老板在员工档案里也有一条记录，
 * 混进员工列表会让同一笔垫付落到「其他应付款-员工往来」而不是
 * 「其他应付款-股东」，两个子户记同一笔负债。老板有独立的「老板/股东个人」
 * 选项，不需要从员工里挑。
 *
 * 名单懒加载：一页上可能同时挂多张确认卡片，每张都请求一次员工接口是浪费。
 */
export function usePayeeEmployees() {
  const loading = ref(false)
  const loaded = ref(false)
  const list = ref<Employee[]>([])

  async function ensure() {
    if (loaded.value || loading.value) return
    loading.value = true
    try {
      // 响应拦截器在运行时已把 axios 的 response 解包成 data 本体，
      // 但 axios 的 TS 签名仍声明返回 AxiosResponse，这里需要显式断言。
      const rows = ((await getEmployees()) as unknown as Employee[] | undefined) || []
      list.value = rows.filter(
        (e) => Number(e.is_active ?? 1) === 1 && !Number(e.is_owner ?? 0),
      )
    } catch {
      // 员工档案读不到不该挡住记账：仍允许按「员工垫付」保存，只是没有可选名单
    } finally {
      loaded.value = true
      loading.value = false
    }
  }

  /** 下拉选项；草稿里的姓名若不在档案里，保留一项让用户自己确认，不静默丢人 */
  function options(currentName = '') {
    const items = list.value.map((e) => ({
      label: e.department ? `${e.name} · ${e.department}` : e.name,
      value: e.name,
    }))
    const current = String(currentName || '').trim()
    if (current && !items.some((item) => item.value === current)) {
      items.unshift({ label: `${current}（未在员工档案）`, value: current })
    }
    return items
  }

  return { loading, loaded, list, ensure, options }
}
