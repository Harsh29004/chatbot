import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The API is proxied under the same origin in development so the session
// cookie (SameSite=Lax) behaves exactly as it will in production behind a
// reverse proxy. Avoids a class of "works locally, breaks deployed" bugs.
export default defineConfig({
  plugins: [react()],
  // One .env for the whole project, at the repo root, instead of a second one
  // here. Without this the VITE_FIREBASE_* values sit in a file Vite never
  // reads, and sign-in fails in the browser with an empty API key. Only
  // VITE_-prefixed values are exposed to the bundle; the server's secrets in
  // the same file are not.
  envDir: "..",
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
          // Firebase is ~200 kB of SDK that changes on its own release
          // schedule, not ours. In its own chunk it stays cached across
          // every deploy that only touches application code — and the
          // landing page, which needs none of it until someone signs in,
          // no longer pays for it in the initial payload.
          firebase: [
            "firebase/app",
            "firebase/auth",
            "firebase/analytics",
            "firebase/performance",
          ],
        },
      },
    },
  },
});
