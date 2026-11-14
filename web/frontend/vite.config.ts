import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileViewerRenderers } from "@file-viewer/vite-plugin";

export default defineConfig({
  plugins: [
    react(),
    fileViewerRenderers({
      formats: ["pdf", "doc", "docx", "docm", "ppt", "pptx", "pptm"],
      inject: false,
      copyAssets: { baseDir: "assets/file-viewer" },
    }),
  ],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
