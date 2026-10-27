import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 前端开发服务器把 /api 和 /health 代理到后端（默认 8000）
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
