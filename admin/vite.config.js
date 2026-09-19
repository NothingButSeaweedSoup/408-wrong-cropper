import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// 后台最终由 FastAPI 挂在 /admin 下，所以 base 固定 /admin/；
// 开发态把 /api 代理到后端（端口 18100，见 backend/config.py 的 SERVER_PORT），避免跨域配置。
export default defineConfig({
  plugins: [vue()],
  base: "/admin/",
  build: { outDir: "dist", emptyOutDir: true },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:18100", changeOrigin: true },
    },
  },
});
