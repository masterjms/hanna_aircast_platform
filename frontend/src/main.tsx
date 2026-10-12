import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter } from 'react-router-dom';

import { App } from './App';
import { AuthProvider } from './auth/AuthContext';
import { applyTheme, readTheme } from './lib/theme';
import './styles/global.css';

// 첫 그림 전에 테마를 정한다 — 늦으면 밝은 테마 사용자에게 어두운 화면이 한 번 번쩍인다.
applyTheme(readTheme());

const container = document.getElementById('root');
if (!container) throw new Error('#root 엘리먼트를 찾을 수 없습니다.');

createRoot(container).render(
  <StrictMode>
    <BrowserRouter>
      <AuthProvider>
        <App />
      </AuthProvider>
    </BrowserRouter>
  </StrictMode>,
);
