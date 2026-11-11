/**
 * Copyright (c) 2026 芥子星辰（杭州）智能科技有限公司
 * Licensed under the SoloBooks License v2.0.
 */

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import piniaPluginPersistedstate from 'pinia-plugin-persistedstate'
import App from './App.vue'
import router from './router'
import './style.css'

const app = createApp(App)

const pinia = createPinia()
pinia.use(piniaPluginPersistedstate)

app.use(pinia)
app.use(router)
// 不要 app.use(naive)：全局注册会把整个 naive-ui（约 1MB 原始 / 278KB gzip）
// 打进入口 chunk，官网首屏也得先下载整套组件库。全站用到 naive 的 116 个组件文件
// 都是显式 import 具体组件（无 <n-xxx> kebab 用法、无 resolveComponent、无
// globalProperties），按需引入即可，不需要全局注册。

app.mount('#app')
