/**
 * 기관 트리 — 평평한 기관·마을 목록을 트리로 세운다.
 *
 * 백엔드는 `parent_id` 만 준다(깊이 제한 없음, 설계 v2). 지역 관리 탐색기·스케줄
 * 위자드·계정 화면의 드롭다운이 전부 이 모듈로 같은 트리를 만든다 — 화면마다 따로
 * 세우면 정렬이 어긋나고 고아 처리가 갈라진다.
 *
 * 규칙:
 *   · 뿌리 = parent_id 가 null 이거나 목록에 없는 기관. 기관 관리자는 자기 관할만
 *     받으므로 자기 기관이 곧 뿌리다.
 *   · 자식·마을은 이름순. 백엔드 orgtree 와 같은 순서다.
 *   · 기관 없는 마을(organization_id null — 최고 관리자만 본다)은 `orphans` 로 따로.
 */

import type { Organization, Village } from '../api/types';

export interface OrgNode {
  org: Organization;
  parent: OrgNode | null;
  children: OrgNode[];
  villages: Village[];
  depth: number;
}

export interface OrgForest {
  roots: OrgNode[];
  byId: Map<number, OrgNode>;
  orphans: Village[];
}

const byName = <T extends { name: string; id: number }>(a: T, b: T) =>
  a.name.localeCompare(b.name, 'ko') || a.id - b.id;

export function buildForest(orgs: Organization[], villages: Village[]): OrgForest {
  const byId = new Map<number, OrgNode>();
  for (const org of orgs) {
    byId.set(org.id, { org, parent: null, children: [], villages: [], depth: 0 });
  }
  const roots: OrgNode[] = [];
  for (const node of byId.values()) {
    const parent = node.org.parent_id !== null ? byId.get(node.org.parent_id) : undefined;
    if (parent) {
      node.parent = parent;
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }
  const orphans: Village[] = [];
  for (const v of villages) {
    const node = v.organization_id !== null ? byId.get(v.organization_id) : undefined;
    if (node) node.villages.push(v);
    else orphans.push(v);
  }
  const sortRec = (node: OrgNode, depth: number) => {
    node.depth = depth;
    node.children.sort((a, b) => byName(a.org, b.org));
    node.villages.sort(byName);
    for (const c of node.children) sortRec(c, depth + 1);
  };
  roots.sort((a, b) => byName(a.org, b.org));
  for (const r of roots) sortRec(r, 0);
  orphans.sort(byName);
  return { roots, byId, orphans };
}

/** 뿌리 → 자기. breadcrumb 용. */
export function pathOf(node: OrgNode): OrgNode[] {
  const out: OrgNode[] = [];
  for (let cur: OrgNode | null = node; cur; cur = cur.parent) out.unshift(cur);
  return out;
}

export function pathLabel(node: OrgNode, sep = ' › '): string {
  return pathOf(node)
    .map((n) => n.org.name)
    .join(sep);
}

/** 자기 포함 후손 id. 백엔드 subtree() 와 같다. */
export function subtreeIds(node: OrgNode): Set<number> {
  const out = new Set<number>();
  const stack = [node];
  while (stack.length) {
    const cur = stack.pop()!;
    out.add(cur.org.id);
    stack.push(...cur.children);
  }
  return out;
}

export interface SubtreeStats {
  orgs: number;
  villages: number;
  devices: number;
  online: number;
}

/** 부분 트리 합계(자기 제외 기관 수, 마을·단말·온라인은 자기 포함). */
export function subtreeStats(node: OrgNode): SubtreeStats {
  const s: SubtreeStats = { orgs: 0, villages: 0, devices: 0, online: 0 };
  const stack = [node];
  while (stack.length) {
    const cur = stack.pop()!;
    s.orgs += cur.children.length;
    for (const v of cur.villages) {
      s.villages += 1;
      s.devices += v.device_count;
      s.online += v.online_count;
    }
    stack.push(...cur.children);
  }
  return s;
}

/** 부분 트리를 깊이 우선으로 평평하게. 드롭다운(들여쓰기)이 쓴다. */
export function flattenForest(forest: OrgForest): OrgNode[] {
  const out: OrgNode[] = [];
  const walk = (n: OrgNode) => {
    out.push(n);
    n.children.forEach(walk);
  };
  forest.roots.forEach(walk);
  return out;
}

/** 이 마디가 `ancestor` 아래(자기 포함)인가. */
export function isUnder(node: OrgNode, ancestor: OrgNode): boolean {
  for (let cur: OrgNode | null = node; cur; cur = cur.parent) if (cur === ancestor) return true;
  return false;
}
