/**
 * 관리자 계층 (설계 2026-09-08, v2 2026-09-12).
 *
 * 최고 > 기관 > 마을. 숫자가 클수록 위다. 백엔드 app/core/authz.py 의 ROLE_TIER 와
 * 같아야 한다 — 여기는 메뉴를 숨기고 드롭다운을 줄이는 용도이고, 실제 방어선은 백엔드다.
 *
 * 기관 관리자끼리의 위아래는 역할이 아니라 기관 트리의 위치가 정한다. 그래서 역할표만으로는
 * "이 기관 관리자를 내가 관리할 수 있나"를 알 수 없고, 화면은 백엔드가 걸러준 목록을 믿는다.
 */

import type { Role } from '../api/types';

export const ROLE_TIER: Record<Role, number> = {
  super_admin: 2,
  org_admin: 1,
  village_admin: 0,
};

export const ROLE_LABEL: Record<Role, string> = {
  super_admin: '최고 관리자',
  org_admin: '기관 관리자',
  village_admin: '마을 관리자',
};

/** 위에서 아래 순서. 드롭다운이 이 순서로 보여준다. */
export const ROLES_DESC: Role[] = ['super_admin', 'org_admin', 'village_admin'];

/** 기관에 소속되는 역할. 그 밖의 역할은 organization_id 가 null 이다. */
export function isOrgRole(role: Role): boolean {
  return role === 'org_admin';
}

/**
 * 내가 만들고 고칠 수 있는 역할(설계 §5). 최고 관리자는 최고 관리자를 만들지 않는다.
 * 기관 관리자는 기관 관리자도 만든다 — 단 자기 **아래** 마디에만(백엔드가 검사).
 */
export function manageableRoles(mine: Role): Role[] {
  if (mine === 'village_admin') return [];
  return ['org_admin', 'village_admin'];
}
