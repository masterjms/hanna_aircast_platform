/**
 * 좌측 메뉴.
 *
 * super_admin 전용 메뉴는 village_admin 에게 아예 렌더링하지 않는다.
 * (비활성화해서 보여주면 "왜 안 눌리냐"는 질문만 늘어난다.)
 *
 * 접근 제어의 실체는 백엔드다. 여기 숨기는 건 UI 편의일 뿐이다.
 */

import { NavLink } from 'react-router-dom';

import { useAuth } from '../../auth/AuthContext';
import { Logo } from '../Logo';

interface MenuItem {
  to: string;
  label: string;
  superAdminOnly?: boolean;
  /** 아직 구현 전인 화면. 라우트는 잡아두고 준비 중으로 표시한다. */
  pending?: boolean;
}

const OPERATION: MenuItem[] = [
  { to: '/', label: '대시보드' },
  { to: '/devices', label: '단말 관리' },
  { to: '/broadcast', label: '방송 제어' },
  { to: '/files', label: '파일함' },
  { to: '/events', label: '이력' },
  { to: '/schedules', label: '스케줄', pending: true },
];

const ADMIN: MenuItem[] = [
  { to: '/costs', label: '비용', pending: true },
  { to: '/ota', label: 'OTA 관리', superAdminOnly: true, pending: true },
  { to: '/settings', label: '설정', superAdminOnly: true },
  { to: '/villages', label: '마을 관리', superAdminOnly: true },
  { to: '/users', label: '계정 관리', superAdminOnly: true },
];

function MenuLinks({ items, isSuperAdmin }: { items: MenuItem[]; isSuperAdmin: boolean }) {
  return (
    <>
      {items
        .filter((item) => !item.superAdminOnly || isSuperAdmin)
        .map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) => `navlink${isActive ? ' active' : ''}`}
          >
            <span>{item.label}</span>
            {item.pending && <span className="navlink__tag">준비</span>}
          </NavLink>
        ))}
    </>
  );
}

export function Sidebar() {
  const { user, isSuperAdmin, logout } = useAuth();

  const scopeLabel = user?.all_villages
    ? `전체 ${user.villages.length}개 마을`
    : user && user.villages.length > 0
      ? user.villages.map((v) => v.name).join(', ')
      : '담당 마을 없음';

  return (
    <aside className="sidebar">
      <div className="sidebar__brand">
        <Logo size={26} />
      </div>

      <div className="scope-card">
        <div className="scope-card__label">담당 범위</div>
        <div className="scope-card__value">{scopeLabel}</div>
        <div className="scope-card__meta">단말 {user?.device_count ?? 0}대</div>
      </div>

      <nav className="sidebar__nav">
        <div className="sidebar__section">운영</div>
        <MenuLinks items={OPERATION} isSuperAdmin={isSuperAdmin} />
        <div className="sidebar__section">관리</div>
        <MenuLinks items={ADMIN} isSuperAdmin={isSuperAdmin} />
      </nav>

      <div className="sidebar__user">
        <div className="avatar" aria-hidden="true">
          {user?.username.slice(0, 1).toUpperCase() ?? '?'}
        </div>
        <div className="sidebar__user-meta">
          <div className="sidebar__user-name">{user?.username}</div>
          <div className="sidebar__user-role">
            {isSuperAdmin ? '최고 관리자' : '마을 관리자'}
          </div>
        </div>
        <button type="button" className="btn btn--ghost" onClick={() => void logout()}>
          로그아웃
        </button>
      </div>
    </aside>
  );
}
