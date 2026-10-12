/**
 * 상단바.
 *
 * 왼쪽은 현재 화면 이름, 오른쪽은 담당 범위 알약이다.
 * 계정·로그아웃은 사이드바 하단으로 내렸다(시안 기준).
 */

import { useEffect, useState } from 'react';

import { useAuth } from '../../auth/AuthContext';
import { applyTheme, readTheme, type Theme } from '../../lib/theme';

/** 오른쪽 위 시계 — 「2026. 10. 9. 13:30:54」(문제점 79번). 1초마다 다시 그린다. */
function Clock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 1000);
    return () => window.clearInterval(id);
  }, []);
  return (
    <time className="topbar__clock" dateTime={now.toISOString()}>
      {now.toLocaleString('ko-KR', { dateStyle: 'medium', timeStyle: 'medium', hour12: false })}
    </time>
  );
}

interface TopBarProps {
  title: string;
  subtitle?: string;
}

/** 밝게·어둡게(문제점 73번). 지금의 반대를 보여 준다. */
function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  const next: Theme = theme === 'dark' ? 'light' : 'dark';
  return (
    <button
      type="button"
      className="btn btn--sm btn--ghost theme-toggle"
      title={next === 'light' ? '밝은 화면으로' : '어두운 화면으로'}
      onClick={() => {
        applyTheme(next);
        setTheme(next);
      }}
    >
      {next === 'light' ? '☀ 밝게' : '☾ 어둡게'}
    </button>
  );
}

export function TopBar({ title, subtitle }: TopBarProps) {
  const { user } = useAuth();
  if (!user) return null;

  const scopeText = user.all_villages
    ? `전체 ${user.villages.length}개 지역`
    : user.villages.length === 0
      ? '담당 지역 없음'
      : user.villages.map((v) => v.name).join(', ');

  return (
    <header className="topbar">
      <div className="topbar__title">
        <strong>{title}</strong>
        {subtitle && <span>{subtitle}</span>}
      </div>

      <div className="topbar__right">
        <Clock />
        <ThemeToggle />
        <button
          type="button"
          className="btn btn--sm btn--ghost"
          title="화면을 새로 불러옵니다"
          aria-label="새로고침"
          onClick={() => window.location.reload()}
        >
          ↻ 새로고침
        </button>
        <div className="status-pill">
          <span className="status-pill__dot" aria-hidden="true" />
          담당 범위 {scopeText} · {user.device_count}대
        </div>
      </div>
    </header>
  );
}
