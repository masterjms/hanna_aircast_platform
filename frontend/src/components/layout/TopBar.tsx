/**
 * 상단바.
 *
 * 왼쪽은 현재 화면 이름, 오른쪽은 담당 범위 알약이다.
 * 계정·로그아웃은 사이드바 하단으로 내렸다(시안 기준).
 */

import { useAuth } from '../../auth/AuthContext';

interface TopBarProps {
  title: string;
  subtitle?: string;
}

export function TopBar({ title, subtitle }: TopBarProps) {
  const { user } = useAuth();
  if (!user) return null;

  const scopeText = user.all_villages
    ? `전체 ${user.villages.length}개 마을`
    : user.villages.length === 0
      ? '담당 마을 없음'
      : user.villages.map((v) => v.name).join(', ');

  return (
    <header className="topbar">
      <div className="topbar__title">
        <strong>{title}</strong>
        {subtitle && <span>{subtitle}</span>}
      </div>

      <div className="status-pill">
        <span className="status-pill__dot" aria-hidden="true" />
        담당 범위 {scopeText} · {user.device_count}대
      </div>
    </header>
  );
}
