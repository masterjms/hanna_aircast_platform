/**
 * 라우팅 · 접근 가드.
 *
 * 로그인 여부에 따라 라우트 자체를 갈아끼운다. 로그인 전에는 보호 라우트가
 * 존재하지 않으므로 URL 을 직접 쳐도 들어올 수 없다.
 *
 * 계층이 필요한 화면은 RequireRole 로 한 번 더 막는다(설계 2026-09-08 §9).
 * 다만 실제 방어선은 백엔드다 — 프론트 가드는 UX 를 위한 것이다.
 */

import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';

import type { Role } from './api/types';

import { MobileNav, Sidebar } from './components/layout/Sidebar';
import { TopBar } from './components/layout/TopBar';
import { useAuth } from './auth/AuthContext';
import { DashboardPage } from './pages/DashboardPage';
import { EventsPage } from './pages/EventsPage';
import { BroadcastPage } from './pages/BroadcastPage';
import { DevicesPage } from './pages/DevicesPage';
import { FilesPage } from './pages/FilesPage';
import { ChangePasswordPage } from './pages/ChangePasswordPage';
import { LoginPage } from './pages/LoginPage';
import { PrivacyPage } from './pages/legal/PrivacyPage';
import { TermsPage } from './pages/legal/TermsPage';
import { RegionsPage } from './pages/RegionsPage';
import { SchedulesPage } from './pages/SchedulesPage';
import { SettingsPage } from './pages/SettingsPage';
import { UsersPage } from './pages/UsersPage';

/** 상단바에 띄울 화면 이름. 경로가 유일한 출처라 페이지가 따로 알릴 필요가 없다. */
const PAGE_TITLES: Record<string, [string, string]> = {
  '/': ['마을 현황', '단말 상태와 지도'],
  '/devices': ['단말 관리', '등록 · 배정 · 상태'],
  '/broadcast': ['방송하기', '말로 · 글로 · 저장된 소리로, 지금 또는 예약'],
  '/files': ['방송 자료', '저장된 소리 · 글로 만든 음성'],
  '/events': ['방송 기록', '지난 방송과 단말 응답'],
  '/schedules': ['예약 방송', '예약 목록 · 예정표'],
  '/costs': ['비용', '마을별 사용량'],
  '/ota': ['OTA 관리', '펌웨어 배포'],
  '/settings': ['설정', '전 단말 공통 CONFIG'],
  '/regions': ['지역 관리', '기관 트리 · 마을 · 구역 · 단말'],
  '/users': ['계정 관리', '관리자 계정과 범위'],
};

function AppShell({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  const [title, subtitle] = PAGE_TITLES[pathname] ?? ['HANNA AirCast', ''];

  return (
    <div className="app">
      <Sidebar />
      <div className="shell">
        <TopBar title={title} subtitle={subtitle} />
        <main className="main">{children}</main>
        <MobileNav />
      </div>
    </div>
  );
}

function RequireRole({ role, children }: { role: Role; children: ReactNode }) {
  const { atLeast } = useAuth();
  return atLeast(role) ? <>{children}</> : <Navigate to="/" replace />;
}

/** 아직 구현하지 않은 화면. 라우트를 비워두면 404 처럼 보여서 혼선이 생긴다. */
function ComingSoon({ title, phase }: { title: string; phase: string }) {
  return (
    <>
      <div className="page-head">
        <h1>{title}</h1>
        <p>{phase} 에서 구현 예정입니다.</p>
      </div>
      <div className="placeholder">다음 단계에서 구현 예정입니다.</div>
    </>
  );
}

export function App() {
  const { user, loading } = useAuth();
  const { pathname } = useLocation();

  // 약관·개인정보 처리방침은 로그인 여부와 상관없이 열린다(처리방침은 첫 화면에서 갈 수 있어야 한다).
  if (pathname === '/terms') return <TermsPage />;
  if (pathname === '/privacy') return <PrivacyPage />;

  // 저장된 토큰으로 세션을 복구하는 동안 로그인 화면을 깜빡이지 않게 한다.
  if (loading) {
    return <div className="login" />;
  }

  if (!user) {
    return (
      <Routes>
        <Route path="*" element={<LoginPage />} />
      </Routes>
    );
  }

  // 임시 비밀번호 계정은 새 비밀번호를 정하기 전까지 다른 화면에 못 간다(향후검토 10번).
  if (user.must_change_password) {
    return <ChangePasswordPage />;
  }

  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/devices" element={<DevicesPage />} />
        <Route path="/broadcast" element={<BroadcastPage />} />
        <Route path="/files" element={<FilesPage />} />
        <Route path="/events" element={<EventsPage />} />
        <Route path="/schedules" element={<SchedulesPage />} />
        <Route path="/costs" element={<ComingSoon title="비용" phase="Phase 8" />} />

        <Route
          path="/settings"
          element={
            <RequireRole role="super_admin">
              <SettingsPage />
            </RequireRole>
          }
        />
        <Route
          path="/ota"
          element={
            <RequireRole role="super_admin">
              <ComingSoon title="OTA 관리" phase="Phase 7" />
            </RequireRole>
          }
        />
        <Route
          path="/regions"
          element={
            <RequireRole role="org_admin">
              <RegionsPage />
            </RequireRole>
          }
        />
        {/* 옛 경로 — 북마크가 남아 있을 수 있다. 두 화면이 지역 관리 하나로 합쳐졌다. */}
        <Route path="/organizations" element={<Navigate to="/regions" replace />} />
        <Route path="/villages" element={<Navigate to="/regions" replace />} />
        <Route
          path="/users"
          element={
            <RequireRole role="org_admin">
              <UsersPage />
            </RequireRole>
          }
        />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
