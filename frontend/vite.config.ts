import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // Generated evidence is refreshed explicitly, not watched file by file.
    // Avoid Windows watcher locks during atomic pipeline publication.
    watch: { ignored: ['**/public/project-data/**', '**/public/audit-data/**', '**/public/analysis-data/**'] },
    proxy: {
      '/api': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
    },
  },
})
