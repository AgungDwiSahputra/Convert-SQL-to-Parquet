import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// Mode dev (npm run dev): Vite di :5173, panggilan /api diteruskan ke FastAPI di :8000.
// Produksi: `npm run build` -> dist/, disajikan langsung oleh FastAPI (webapp/main.py).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
