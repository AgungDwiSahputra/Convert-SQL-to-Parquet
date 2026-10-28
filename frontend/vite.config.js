import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Mode dev (npm run dev): Vite di :5173, panggilan /api diteruskan ke FastAPI di :8000.
// Produksi: `npm run build` -> dist/, disajikan langsung oleh FastAPI (webapp/main.py).
// base relatif: URL asset mengikuti <base href> yang diisi server dari BASE_PATH, jadi satu hasil build
// jalan di root maupun di sub-path gateway tanpa build ulang.
export default defineConfig({
  base: './',
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
