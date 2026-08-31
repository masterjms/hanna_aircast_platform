/**
 * 라우팅 · 접근 가드.
 *
 * 로그인 여부에 따라 라우트 자체를 갈아끼운다. 로그인 전에는 보호 라우트가
 * 존재하지 않으므로 URL 을 직접 쳐도 들어올 수 없다.
 *
 * super_admin 전용 화면은 RequireSuperAdmin 으로 한 번 더 막는다.
 * 다만 실제 방어선은 백엔드다 — 프론트 가드는 UX 를 위한 것이다.
 */

import { Navigate, Route, Routes, useLocation } from 'react-router-dom';
import type { ReactNode } from 'react';

import { Sidebar } from './components/layout/Sidebar';
import { TopBar } from './components/layout/TopBar';
import { useAuth } from './auth/AuthContext';
import { DashboardPage } from './pages/DashboardPage';
import { MapPage } from './pages/MapPage';
import { BroadcastPage } from './pages/BroadcastPage';
import { DevicesPage } from './pages/DevicesPage';
import { FilesPage } from './pages/FilesPage';
import { LoginPage } from './pages/LoginPage';
import { SettingsPage } from './pages/SettingsPage';
import { UsersPage } from './pages/UsersPage';
import { VillagesPage } from './pages/VillagesPage';

/** 상단바에 띄울 화면 이름. 경로가 유일한 출처라 페이지가 따로 알릴 필요가 없다. */
const PAGE_TITLES: Record<string, [string, string]> = {
  '/': ['전체 개요', '단말 상태와 진행 중인 방송'],
  '/devices': ['단말 관리', '등록 · 배정 · 상태'],
  '/broadcast': ['방송 제어', '실시간 · 파일 송출'],
  '/files': ['파일함', '업로드 · TTS'],
  '/events': ['이력', '방송 명령과 단말 응답'],
  '/schedules': ['스케줄', '자동방송 규칙'],
  '/costs': ['비용', '마을별 사용량'],
  '/ota': ['OTA 관리', '펌웨어 배포'],
  '/settings': ['설정', '전 단말 공통 CONFIG'],
  '/villages': ['마을 관리', '마을 · 구역'],
  '/users': ['계정 관리', '관리자 계정과 담당 마을'],
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
      </div>
    </div>
  );
}

function RequireSuperAdmin({ children }: { children: ReactNode }) {
  const { isSuperAdmin } = useAuth();
  return isSuperAdmin ? <>{children}</> : <Navigate to="/" replace />;
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

  return (
    <AppShell>
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/map" element={<MapPage />} />
        <Route path="/devices" element={<DevicesPage />} />
        <Route path="/broadcast" element={<BroadcastPage />} />
        <Route path="/files" element={<FilesPage />} />
        <Route path="/events" element={<ComingSoon title="이력" phase="Phase 6" />} />
        <Route path="/schedules" element={<ComingSoon title="스케줄" phase="Phase 6" />} />
        <Route path="/costs" element={<ComingSoon title="비용" phase="Phase 8" />} />

        <Route
          path="/settings"
          element={
            <RequireSuperAdmin>
              <SettingsPage />
            </RequireSuperAdmin>
          }
        />
        <Route
          path="/ota"
          element={
            <RequireSuperAdmin>
              <ComingSoon title="OTA 관리" phase="Phase 7" />
            </RequireSuperAdmin>
          }
        />
        <Route
          path="/villages"
          element={
            <RequireSuperAdmin>
              <VillagesPage />
            </RequireSuperAdmin>
          }
        />
        <Route
          path="/users"
          element={
            <RequireSuperAdmin>
              <UsersPage />
            </RequireSuperAdmin>
          }
        />

        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AppShell>
  );
}
