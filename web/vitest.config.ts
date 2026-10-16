import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'

// vitest 在 Windows 上会把 SFC 模板里的 public 资源 import（/solo-logo.png）
// 解析成无盘符的 file:///xxx，模块求值时 createRequire 直接抛 TypeError，
// 导致引用该组件的测试文件无法收集。alias 到 public 目录绝对路径绕开；
// dev/build 走 vite.config.ts（vite 自己处理 public 文件），不受此影响。
const publicDir = fileURLToPath(new URL('./public', import.meta.url))

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: [
      { find: '@', replacement: fileURLToPath(new URL('./src', import.meta.url)) },
      {
        find: /^(?=\/[^/]+\.(png|jpe?g|gif|webp|svg|ico)$)/,
        replacement: publicDir,
      },
    ],
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/__tests__/**/*.test.ts'],
  },
})
