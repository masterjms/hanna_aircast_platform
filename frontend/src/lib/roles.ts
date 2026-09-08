/**
 * 관리자 계층 (설계 2026-09-08 §2).
 *
 * 숫자가 클수록 위다. 백엔드 app/core/authz.py 의 ROLE_TIER 와 같아야 한다 —
 * 여기는 메뉴를 숨기고 드롭다운을 줄이는 용도이고, 실제 방어선은 백엔드다.
 */

import type { Role } from '../api/types';

export const ROLE_TIER: Record<Role, number> = {
  super_admin: 3,
  sido_admin: 2,
  sigungu_admin: 1,
  village_admin: 0,
};

export const ROLE_LABEL: Record<Role, string> = {
  super_admin: '최고 관리자',
  sido_admin: '시·도 관리자',
  sigungu_admin: '시·군 관리자',
  village_admin: '마을 관리자',
};

/** 위에서 아래 순서. 드롭다운이 이 순서로 보여준다. */
export const ROLES_DESC: Role[] = ['super_admin', 'sido_admin', 'sigungu_admin', 'village_admin'];

/** 기관에 소속되는 역할. 그 밖의 역할은 organization_id 가 null 이다. */
export function isOrgRole(role: Role): boolean {
  return role === 'sido_admin' || role === 'sigungu_admin';
}

/** 내가 만들고 고칠 수 있는 역할 — 나보다 낮은 계층 전부(설계 §5). */
export function manageableRoles(mine: Role): Role[] {
  return ROLES_DESC.filter((r) => ROLE_TIER[r] < ROLE_TIER[mine]);
}
