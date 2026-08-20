import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 개발 중 /api 를 백엔드로 넘겨 CORS 를 우회한다.
    // 운영에서는 nginx 가 같은 오리진으로 서빙하므로 프록시가 필요 없다.
    // target 을 127.0.0.1 로 못 박는다. 'localhost' 를 쓰면 Node 가 ::1(IPv6) 로
    // 먼저 풀어서, 127.0.0.1 에만 바인딩된 백엔드에 연결이 안 된다.
    proxy: {
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      '/health': { target: 'http://127.0.0.1:8000', changeOrigin: true },
      // Phase 4 — 브라우저 마이크 업링크
      '/ingest': { target: 'ws://127.0.0.1:8000', ws: true },
    },
  },
});
