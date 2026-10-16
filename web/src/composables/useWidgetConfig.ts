/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { ref, watch } from 'vue'

export interface WidgetItem {
  id: string
  title: string
  visible: boolean
}

const STORAGE_KEY = 'solo_widget_config'

const defaultWidgets: WidgetItem[] = [
  { id: 'stats', title: '收支概览', visible: true },
  { id: 'quick-actions', title: '快捷操作', visible: true },
  { id: 'todos', title: '待办事项', visible: true },
  { id: 'recent-entries', title: '最近记录', visible: true },
]

function loadConfig(): WidgetItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (raw) {
      const saved = JSON.parse(raw) as WidgetItem[]
      // Merge with defaults to handle new widgets
      return defaultWidgets.map(d => {
        const found = saved.find(s => s.id === d.id)
        return found ? { ...d, visible: found.visible } : d
      })
    }
  } catch { /* ignore */ }
  return [...defaultWidgets]
}

function saveConfig(widgets: WidgetItem[]) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(widgets.map(w => ({ id: w.id, visible: w.visible }))))
  } catch { /* ignore */ }
}

export function useWidgetConfig() {
  const widgets = ref<WidgetItem[]>(loadConfig())

  const visibleWidgets = ref(widgets.value.filter(w => w.visible))

  watch(widgets, (val) => {
    visibleWidgets.value = val.filter(w => w.visible)
    saveConfig(val)
  }, { deep: true })

  function toggle(id: string) {
    const w = widgets.value.find(w => w.id === id)
    if (w) w.visible = !w.visible
  }

  function showAll() {
    widgets.value.forEach(w => { w.visible = true })
  }

  function hideAll() {
    widgets.value.forEach(w => { w.visible = false })
  }

  return { widgets, visibleWidgets, toggle, showAll, hideAll }
}
