import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../server/static",
    emptyOutDir: true,
    cssTarget: ["chrome125", "firefox128", "safari17.5"],
  },
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8090",
      "/v1": "http://127.0.0.1:8090",
      "/health": "http://127.0.0.1:8090",
    },
  },
})
