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
import { LegalLinks } from '../../pages/legal/LegalLayout';
import { Logo } from '../Logo';

interface MenuItem {
  to: string;
  label: string;
  /** 이 계층 이상만 본다. 없으면 전원. */
  minRole?: Role;
  /** 아직 구현 전인 화면. 라우트는 잡아두고 준비 중으로 표시한다. */
  pending?: boolean;
  /** 가장 많이 쓰는 메뉴 — 크게, 눈에 띄게. */
  primary?: boolean;
}

/**
 * 방송이 이 시스템의 일이라 「방송하기」를 맨 위에 둔다(2026-09-21 개편). 이름은 이장님이
 * 쓰는 말로 — 파일함 → 방송 자료, 스케줄 → 예약 방송, 이력 → 방송 기록.
 */
const OPERATION: MenuItem[] = [
  { to: '/broadcast', label: '방송하기', primary: true },
  { to: '/schedules', label: '예약 방송' },
  { to: '/', label: '마을 현황' },
  { to: '/files', label: '방송 자료' },
  { to: '/events', label: '방송 기록' },
  { to: '/devices', label: '단말 관리' },
];

const ADMIN: MenuItem[] = [
  { to: '/costs', label: '비용', pending: true },
  { to: '/ota', label: 'OTA 관리', minRole: 'super_admin', pending: true },
  { to: '/settings', label: '설정', minRole: 'super_admin' },
  { to: '/regions', label: '지역 관리', minRole: 'org_admin' },
  { to: '/users', label: '계정 관리', minRole: 'org_admin' },
];

function MenuLinks({ items, atLeast }: { items: MenuItem[]; atLeast: (r: Role) => boolean }) {
  return (
    <>
      {items
        .filter((item) => !item.minRole || atLeast(item.minRole))
        // 준비 중인 화면은 최고 관리자에게만 — 이장님 메뉴에 눌러도 안 되는 칸을 두지 않는다.
        .filter((item) => !item.pending || atLeast('super_admin'))
        .map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === '/'}
            className={({ isActive }) =>
              `navlink${item.primary ? ' navlink--primary' : ''}${isActive ? ' active' : ''}`
            }
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

  // 기관 관리자는 기관 이름이 범위를 가장 잘 설명한다. 마을 수십 개를 나열하면
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
        {/* 이장님은 관리 메뉴가 하나도 없다 — 빈 제목만 남기지 않는다. */}
        {atLeast('org_admin') && <div className="sidebar__section">관리</div>}
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

      {/* 저작권·약관 — 본문 아래에 띠를 두면 「화면에 맞추기」 높이를 먹는다. 메뉴 맨 아래 빈자리에 둔다. */}
      <LegalLinks className="legal-links--sidebar" />
    </aside>
  );
}

/**
 * 폰·세로 태블릿(900px 이하)용 하단 탭바. 그 너비에서는 왼쪽 메뉴를 숨기므로 이것이 없으면
 * 다른 화면으로 갈 길이 없다. 항목과 권한 규칙은 왼쪽 메뉴와 같고, 많으면 옆으로 민다.
 */
export function MobileNav() {
  const { atLeast, logout } = useAuth();
  const items = [...OPERATION, ...ADMIN]
    .filter((item) => !item.minRole || atLeast(item.minRole))
    .filter((item) => !item.pending);

  return (
    <nav className="tabbar" aria-label="메뉴">
      {items.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.to === '/'}
          className={({ isActive }) =>
            `tabbar__item${item.primary ? ' tabbar__item--primary' : ''}${isActive ? ' active' : ''}`
          }
        >
          {item.label}
        </NavLink>
      ))}
      <button type="button" className="tabbar__item" onClick={() => void logout()}>
        로그아웃
      </button>
      <NavLink to="/terms" className="tabbar__item tabbar__item--minor">
        약관
      </NavLink>
      <NavLink to="/privacy" className="tabbar__item tabbar__item--minor">
        개인정보
      </NavLink>
    </nav>
  );
}
