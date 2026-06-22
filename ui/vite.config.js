import { resolve } from 'path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  root: 'renderer',
  plugins: [react()],
  build: {
    outDir: '../dist/renderer',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'renderer/index.html'),
        canvas: resolve(__dirname, 'renderer/canvas.html'),
      },
    },
  },
})
