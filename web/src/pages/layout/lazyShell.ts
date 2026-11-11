/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { defineAsyncComponent, h } from 'vue'
import type { Component } from 'vue'

/**
 * 路由壳：Vue Router 在导航时会解析整条匹配链上的懒加载组件，
 * 所以 `component: () => import(...)` 一旦写在匹配链上就一定会被下载 ——
 * 哪怕父组件最终渲染的是别的分支（匿名访客访问 `/` 时 RootSwitch 渲染的是官网
 * Landing，但它的子记录 `path: ''` 仍会被解析，工作台布局与首页业务模块因此
 * 一起进入首屏，实测 65KB gzip / 16 个额外请求）。
 *
 * 这个壳本身是同步的极小对象，真正的大组件由 defineAsyncComponent 在壳被渲染时
 * 才加载。挂在「总是匹配」的记录上，官网首屏就不再下载工作台代码。
 */
export function lazyShell(loader: () => Promise<Component>): Component {
  const resolved = defineAsyncComponent(loader)
  return { name: 'LazyRouteShell', render: () => h(resolved) }
}
