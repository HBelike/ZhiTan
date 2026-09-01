import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'

const uiRoot = fileURLToPath(new URL('.', import.meta.url))

export default defineConfig(({ mode }) => {
  // 始终从 web-ui 目录读取环境变量，避免从项目根目录执行 npm 时误代理到旧后端。
  const env = loadEnv(mode, uiRoot, '')
  // 命令行临时指定的代理地址优先，便于与既有开发服务并行验证。
  // 与 scripts/start_dev_backend.ps1 的固定默认端口保持一致。此前这里仍指向
  // 已弃用的 8000，导致前端出现 “Failed to fetch” 或误连旧后端。
  const apiProxyTarget = process.env.VITE_API_PROXY_TARGET || env.VITE_API_PROXY_TARGET || 'http://127.0.0.1:18080'

  return {
    plugins: [vue()],
    server: {
      proxy: {
        '/api': {
          target: apiProxyTarget,
          changeOrigin: true,
          ws: true
        }
      }
    }
  }
})
