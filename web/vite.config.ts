import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  plugins: [vue(), tailwindcss()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.VITE_DEV_API_TARGET || 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
      // MCP 端点与生产 nginx 行为一致：页面上的客户端配置示例（origin/mcp）开发环境同样可用。
      // 用正则锚定 /mcp 与 /mcp/*，不能用裸 '/mcp'——前缀匹配会把 /mcp-guide 这类
      // 前端路由一并代理到后端，导致 SPA 页面 404。
      '^/mcp(/|$)': {
        target: process.env.VITE_DEV_API_TARGET || 'http://127.0.0.1:8001',
        changeOrigin: true,
      },
    },
  },
})
