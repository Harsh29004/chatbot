import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API is proxied under the same origin in development so the session
// cookie (SameSite=Lax) behaves exactly as it will in production behind a
// reverse proxy. Avoids a class of "works locally, breaks deployed" bugs.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: process.env.API_ORIGIN ?? "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    // three.js is large; splitting it keeps the initial page payload small.
    rollupOptions: {
      output: {
        manualChunks: {
          three: ["three", "@react-three/fiber", "@react-three/drei"],
        },
      },
    },
  },
});
