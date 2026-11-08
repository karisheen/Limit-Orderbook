import { defineConfig } from 'vite';

export default defineConfig({
  resolve: { alias: { '@': new URL('./src/orderflow', import.meta.url).pathname } },
  server: {
    host: '127.0.0.1',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', ws: true },
      '/health': { target: 'http://127.0.0.1:8000' },
    },
  },
  build: { target: 'es2022', sourcemap: false },
});
