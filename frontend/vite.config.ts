import { loadEnv, type ProxyOptions } from "vite";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { resolve } from "node:path";

export default defineConfig(({ mode }) => {
  const env = {
    ...loadEnv(mode, resolve(process.cwd(), ".."), ""),
    ...loadEnv(mode, process.cwd(), ""),
    ...process.env,
  };
  const backendTarget = env.KNOWVIA_API_BASE_URL || "http://127.0.0.1:8000";
  const bearerToken = env.API_BEARER_TOKEN;
  const apiProxy: Record<string, ProxyOptions> = {
    "/api": {
      target: backendTarget,
      changeOrigin: true,
      configure(proxy) {
        if (!bearerToken) {
          return;
        }
        proxy.on("proxyReq", (proxyRequest) => {
          proxyRequest.setHeader("Authorization", `Bearer ${bearerToken}`);
        });
      },
    },
  };

  return {
    plugins: [react()],
    server: { proxy: apiProxy },
    preview: { proxy: apiProxy },
    test: {
      environment: "jsdom",
      setupFiles: "./src/test/setup.ts",
      css: true,
    },
  };
});
