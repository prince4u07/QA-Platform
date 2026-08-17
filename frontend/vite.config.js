import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  test: {
    // Tests render real components into a real DOM, so they exercise what a
    // user actually sees rather than asserting on implementation details.
    environment: 'jsdom',
    globals: true,
    setupFiles: './src/test/setup.js',
    // Only our own tests. Without this, vitest walks node_modules.
    include: ['src/**/*.test.{js,jsx}'],
    css: false,
  },
})
