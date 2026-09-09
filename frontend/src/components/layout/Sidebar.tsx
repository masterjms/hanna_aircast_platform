/**
 * 좌측 메뉴.
 *
 * 계층에 못 미치는 메뉴는 아예 렌더링하지 않는다(설계 2026-09-08 §9).
 * (비활성화해서 보여주면 "왜 안 눌리냐"는 질문만 늘어난다.)
 *
 * 접근 제어의 실체는 백엔드다. 여기 숨기는 건 UI 편의일 뿐이다.
 */

import { NavLink } from 'react-router-dom';

import type { Role } from '../../api/types';
import { useAuth } from '../../auth/AuthContext';
import { ROLE_LABEL } from '../../lib/roles';
import { Logo } from '../Logo';

interface MenuItem {
  to: string;
  label: string;
  /** 이 계층 이상만 본다. 없으면 전원. */
  minRole?: Role;
  /** 아직 구현 전인 화면. 라우트는 잡아두고 준비 중으로 표시한다. */
  pending?: boolean;
}

const OPERATION: MenuItem[] = [
  { to: '/', label: '대시보드' },
  { to: '/devices', label: '단말 관리' },
  { to: '/broadcast', label: '방송 제어' },
  { to: '/files', label: '파일함' },
  { to: '/events', label: '이력' },
  { to: '/schedules', label: '스케줄' },
];

const ADMIN: MenuItem[] = [
  { to: '/costs', label: '비용', pending: true },
  { to: '/ota', label: 'OTA 관리', minRole: 'super_admin', pending: true },
  { to: '/settings', label: '설정', minRole: 'super_admin' },
  { to: '/organizations', label: '기관 관리', minRole: 'super_admin' },
  { to: '/villages', label: '마을 관리', minRole: 'sigungu_admin' },
  { to: '/users', label: '계정 관리', minRole: 'sigungu_admin' },
];

function MenuLinks({ items, atLeast }: { items: MenuItem[]; atLeast: (r: Role) => boolean }) {
  return (
    <>
      {items
        .filter((item) => !item.minRole || atLeast(item.minRole))
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
  const { user, atLeast, logout } = useAuth();

  // 시·도/시·군 관리자는 기관 이름이 범위를 가장 잘 설명한다. 마을 수십 개를 나열하면
  // 카드가 넘친다.
  const scopeLabel = user?.all_villages
    ? `전체 ${user.villages.length}개 마을`
    : user?.organization_name
      ? `${user.organization_name} · ${user.villages.length}개 마을`
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
        <MenuLinks items={OPERATION} atLeast={atLeast} />
        <div className="sidebar__section">관리</div>
        <MenuLinks items={ADMIN} atLeast={atLeast} />
      </nav>

      <div className="sidebar__user">
        <div className="avatar" aria-hidden="true">
          {user?.username.slice(0, 1).toUpperCase() ?? '?'}
        </div>
        <div className="sidebar__user-meta">
          <div className="sidebar__user-name">{user?.username}</div>
          <div className="sidebar__user-role">{user ? ROLE_LABEL[user.role] : ''}</div>
        </div>
        <button type="button" className="btn btn--ghost" onClick={() => void logout()}>
          로그아웃
        </button>
      </div>
    </aside>
  );
}
