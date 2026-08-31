import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  root: 'renderer-react',
  // Electron loads the production renderer through file://, so assets must be
  // relative to renderer-dist/index.html instead of resolving from C:\assets.
  base: './',
  plugins: [react(), tailwindcss()],
  build: { outDir: '../renderer-dist', emptyOutDir: true },
})
