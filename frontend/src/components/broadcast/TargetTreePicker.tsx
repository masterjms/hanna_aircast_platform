/**
 * 방송 대상 트리 — 마을·구역·개별 단말을 **같은 트리 UI** 로 고른다.
 *
 *   기관 > (하위 기관) > 마을 > [구역 | 단말]
 *
 * 세 가지 규칙만 기억하면 된다(문제점 향후검토 7·8·9번, 2026-09-20):
 *
 * 1. **상위 기관은 직접 체크했을 때만 체크로 보인다.** 예전에는 "아래 마을이 전부
 *    선택됨"을 체크로 표시해서, 선택 가능한 마을이 하나뿐인 기관은 그 마을만 골라도
 *    기관까지 체크된 것처럼 보였다(단말 개발자 지적). 지금은 아래만 고르면 상위는
 *    중간 표시(−)로만 둔다. 기관을 체크하면 그 아래는 전부 딸려온다(상속).
 * 2. **구역·개별 단말도 같은 트리**를 쓴다. 마을을 체크하면 그 마을의 구역·단말이
 *    전부 선택된다. 화면마다 조작이 달라지지 않는다.
 * 3. **검색**은 이름이 걸리는 가지만 남기고 펼친다. 결과가 없으면 그렇게 말한다.
 *
 * 선택 결과는 항상 "잎(마을 id · 구역 id · MAC)" 목록으로 서버에 간다. 기관 선택은
 * 화면에서만 펼쳐지고, 서버·이력·단말 프로토콜은 그대로다.
 */

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import type { Device, Organization, Village, Zone } from '../../api/types';
import { buildForest, type OrgNode } from '../../lib/orgtree';

export type PickMode = 'village' | 'zone' | 'device';

/** 고른 것. leaves 는 서버로 보낼 값, groups 는 "직접 체크한 기관·마을"(화면 표시용). */
export interface Picked {
  leaves: string[];
  groups: string[];
}

export const EMPTY_PICK: Picked = { leaves: [], groups: [] };

interface Leaf {
  id: string;
  name: string;
  note?: string;
  online: number;
  total: number;
}

interface Node {
  key: string;
  name: string;
  kind: 'org' | 'village';
  children: Node[];
  leaves: Leaf[];
  /** 구역 모드에서 아직 구역을 못 불러온 마을 */
  pending?: boolean;
}

const villageLeaf = (v: Village): Leaf => ({
  id: String(v.id),
  name: v.name,
  online: v.online_count,
  total: v.device_count,
});

const zoneLeaf = (z: Zone): Leaf => ({
  id: String(z.id),
  name: z.name,
  online: z.online_count,
  total: z.device_count,
});

const deviceLeaf = (d: Device): Leaf => ({
  id: d.mac,
  name: d.label ?? d.mac,
  note: d.zone_name ?? undefined,
  online: d.online ? 1 : 0,
  total: 1,
});

/** 방송은 온라인 단말에만 나간다 — 받을 단말이 없는 가지는 고를 수 없다. */
const pickable = (leaf: Leaf) => leaf.online > 0;

function villageNode(
  v: Village,
  mode: PickMode,
  devicesOf: Map<number, Device[]>,
  zonesOf: Record<number, Zone[] | undefined>,
): Node {
  const base: Node = { key: `v:${v.id}`, name: v.name, kind: 'village', children: [], leaves: [] };
  if (mode === 'village') return { ...base, leaves: [villageLeaf(v)] };
  if (mode === 'device') return { ...base, leaves: (devicesOf.get(v.id) ?? []).map(deviceLeaf) };
  const zones = zonesOf[v.id];
  return { ...base, leaves: (zones ?? []).map(zoneLeaf), pending: zones === undefined };
}

function orgNode(
  node: OrgNode,
  mode: PickMode,
  devicesOf: Map<number, Device[]>,
  zonesOf: Record<number, Zone[] | undefined>,
): Node {
  const o = node.org;
  const villages = node.villages.map((v) => villageNode(v, mode, devicesOf, zonesOf));
  if (mode === 'village') {
    // 마을 모드에서는 마을이 잎이다 — 한 단계 덜 들어간다.
    return {
      key: `o:${o.id}`,
      name: o.name,
      kind: 'org',
      children: node.children.map((c) => orgNode(c, mode, devicesOf, zonesOf)),
      leaves: node.villages.map(villageLeaf),
    };
  }
  return {
    key: `o:${o.id}`,
    name: o.name,
    kind: 'org',
    children: [...node.children.map((c) => orgNode(c, mode, devicesOf, zonesOf)), ...villages],
    leaves: [],
  };
}

function allLeaves(node: Node): Leaf[] {
  const out = [...node.leaves];
  for (const c of node.children) out.push(...allLeaves(c));
  return out;
}

function subtreeKeys(node: Node): string[] {
  const out = [node.key];
  for (const c of node.children) out.push(...subtreeKeys(c));
  return out;
}

/** 검색에 걸리는 가지만 남긴다. 이름이 걸리면 그 아래는 통째로 남긴다. */
function filterTree(node: Node, q: string): Node | null {
  if (!q) return node;
  const hit = node.name.toLowerCase().includes(q);
  if (hit) return node;
  const children = node.children.map((c) => filterTree(c, q)).filter((c): c is Node => c !== null);
  const leaves = node.leaves.filter(
    (l) => l.name.toLowerCase().includes(q) || l.id.toLowerCase().includes(q),
  );
  if (children.length === 0 && leaves.length === 0) return null;
  return { ...node, children, leaves };
}

function TriCheckbox({
  checked,
  indeterminate,
  disabled,
  onChange,
}: {
  checked: boolean;
  indeterminate: boolean;
  disabled: boolean;
  onChange: (on: boolean) => void;
}) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <input
      ref={ref}
      type="checkbox"
      checked={checked}
      disabled={disabled}
      onChange={(e) => onChange(e.target.checked)}
    />
  );
}

/** 사람이 읽는 선택 요약. 직접 체크한 기관·마을은 「경기도 전체」로 접는다. */
export function summarize(
  picked: Picked,
  orgs: Organization[],
  villages: Village[],
  mode: PickMode,
  devices: Device[],
  zonesOf: Record<number, Zone[] | undefined>,
): string[] {
  const groups = new Set(picked.groups);
  const leaves = new Set(picked.leaves);
  const forest = buildForest(orgs, villages);
  const roots = forest.roots.map((r) => orgNode(r, mode, groupDevices(devices), zonesOf));
  if (forest.orphans.length > 0 && mode === 'village') {
    roots.push({
      key: 'o:none',
      name: '기관 없음',
      kind: 'org',
      children: [],
      leaves: forest.orphans.map(villageLeaf),
    });
  }
  const out: string[] = [];
  const walk = (node: Node, inherited: boolean) => {
    const self = groups.has(node.key);
    if (self && !inherited) {
      out.push(`${node.name} 전체`);
    }
    for (const c of node.children) walk(c, inherited || self);
    if (!(inherited || self)) {
      for (const l of node.leaves) if (leaves.has(l.id)) out.push(l.name);
    }
  };
  roots.forEach((r) => walk(r, false));
  return out;
}

export function groupDevices(devices: Device[]): Map<number, Device[]> {
  const out = new Map<number, Device[]>();
  for (const d of devices) {
    if (d.village_id === null) continue; // 미배정 단말에는 방송이 나가지 않는다
    const list = out.get(d.village_id) ?? [];
    list.push(d);
    out.set(d.village_id, list);
  }
  for (const list of out.values()) list.sort((a, b) => (a.label ?? a.mac).localeCompare(b.label ?? b.mac, 'ko'));
  return out;
}

export function TargetTreePicker({
  mode,
  orgs,
  villages,
  devices,
  zonesOf,
  onNeedZones,
  value,
  onChange,
  renderCount,
}: {
  mode: PickMode;
  orgs: Organization[];
  villages: Village[];
  devices: Device[];
  zonesOf: Record<number, Zone[] | undefined>;
  onNeedZones: (villageId: number) => void;
  value: Picked;
  onChange: (next: Picked) => void;
  renderCount: (online: number, total: number) => ReactNode;
}) {
  const [query, setQuery] = useState('');
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());

  // 구역·단말 모드는 마을을 접은 채로 연다 — 단말이 많으면 화면이 끝없이 길어진다.
  // 기관은 펼쳐 둔다(어느 기관이 있는지는 바로 보여야 한다).
  useEffect(() => {
    setCollapsed(mode === 'village' ? new Set() : new Set(villages.map((v) => `v:${v.id}`)));
  }, [mode, villages]);

  const devicesOf = useMemo(() => groupDevices(devices), [devices]);
  const roots = useMemo(() => {
    const forest = buildForest(orgs, villages);
    const list = forest.roots.map((r) => orgNode(r, mode, devicesOf, zonesOf));
    if (forest.orphans.length > 0) {
      list.push({
        key: 'o:none',
        name: '기관 없음',
        kind: 'org' as const,
        children: mode === 'village' ? [] : forest.orphans.map((v) => villageNode(v, mode, devicesOf, zonesOf)),
        leaves: mode === 'village' ? forest.orphans.map(villageLeaf) : [],
      });
    }
    if (orgs.length === 0) {
      // 이장 — 기관이 없다. 담당 마을이 바로 뿌리가 된다.
      return mode === 'village'
        ? [{ key: 'o:none', name: '', kind: 'org' as const, children: [], leaves: villages.map(villageLeaf) }]
        : villages.map((v) => villageNode(v, mode, devicesOf, zonesOf));
    }
    return list;
  }, [orgs, villages, mode, devicesOf, zonesOf]);

  const q = query.trim().toLowerCase();
  const shown = useMemo(
    () => roots.map((r) => filterTree(r, q)).filter((r): r is Node => r !== null),
    [roots, q],
  );

  const leaves = new Set(value.leaves);
  const groups = new Set(value.groups);

  const emit = (nextLeaves: Set<string>, nextGroups: Set<string>) =>
    onChange({ leaves: [...nextLeaves], groups: [...nextGroups] });

  const toggleGroup = (node: Node, on: boolean, ancestors: string[]) => {
    const nextLeaves = new Set(leaves);
    const nextGroups = new Set(groups);
    for (const leaf of allLeaves(node)) {
      if (!pickable(leaf)) continue;
      if (on) nextLeaves.add(leaf.id);
      else nextLeaves.delete(leaf.id);
    }
    for (const key of subtreeKeys(node)) nextGroups.delete(key);
    if (on) nextGroups.add(node.key);
    // 해제는 상위의 "전체" 표시를 깬다 — 위가 체크인데 아래가 빠져 있으면 거짓말이 된다.
    else for (const key of ancestors) nextGroups.delete(key);
    emit(nextLeaves, nextGroups);
  };

  const toggleLeaf = (leaf: Leaf, on: boolean, ancestors: string[]) => {
    const nextLeaves = new Set(leaves);
    const nextGroups = new Set(groups);
    if (on) nextLeaves.add(leaf.id);
    else {
      nextLeaves.delete(leaf.id);
      for (const key of ancestors) nextGroups.delete(key);
    }
    emit(nextLeaves, nextGroups);
  };

  const toggleCollapse = (key: string, node: Node) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
    if (node.pending && node.kind === 'village') onNeedZones(Number(node.key.slice(2)));
  };

  const leafRow = (leaf: Leaf, ancestors: string[], inherited: boolean) => {
    const on = inherited || leaves.has(leaf.id);
    const can = pickable(leaf);
    return (
      <li key={leaf.id}>
        <div className="tree__row">
          <span className="tree__caret tree__caret--none" aria-hidden="true" />
          <label className={on ? 'is-on' : undefined}>
            <input
              type="checkbox"
              checked={on}
              disabled={!can}
              onChange={(e) => toggleLeaf(leaf, e.target.checked, ancestors)}
            />
            <span className="tree__name">
              {leaf.name}
              {leaf.note && <span className="dim"> · {leaf.note}</span>}
            </span>
            <span className="tree__kind">{renderCount(leaf.online, leaf.total)}</span>
          </label>
        </div>
      </li>
    );
  };

  const groupRow = (node: Node, ancestors: string[], inherited: boolean): ReactNode => {
    const self = groups.has(node.key);
    const checked = inherited || self;
    const all = allLeaves(node);
    const can = all.filter(pickable);
    const onCount = can.filter((l) => leaves.has(l.id)).length;
    const hasKids = node.children.length > 0 || node.leaves.length > 0 || node.pending;
    const isCollapsed = collapsed.has(node.key) && !q;
    const online = all.reduce((n, l) => n + l.online, 0);
    const total = all.reduce((n, l) => n + l.total, 0);
    const childAncestors = [...ancestors, node.key];
    const unit = node.kind === 'org' ? (mode === 'village' ? '마을' : '') : mode === 'zone' ? '구역' : '단말';
    return (
      <li key={node.key}>
        <div className="tree__row">
          {hasKids ? (
            <button
              type="button"
              className="tree__caret"
              aria-label={isCollapsed ? `${node.name} 펼치기` : `${node.name} 접기`}
              aria-expanded={!isCollapsed}
              onClick={() => toggleCollapse(node.key, node)}
            >
              {isCollapsed ? '▸' : '▾'}
            </button>
          ) : (
            <span className="tree__caret tree__caret--none" aria-hidden="true" />
          )}
          <label className={checked ? 'is-on' : undefined}>
            <TriCheckbox
              checked={checked}
              indeterminate={!checked && onCount > 0}
              disabled={can.length === 0 && !node.pending}
              onChange={(on) => toggleGroup(node, on, ancestors)}
            />
            <span className={node.kind === 'org' ? 'tree__name strong' : 'tree__name'}>{node.name}</span>
            <span className="tree__kind">
              {node.pending ? '구역 불러오는 중…' : all.length > 0 && unit ? `${unit} ${all.length}개 · ` : ''}
              {!node.pending && renderCount(online, total)}
            </span>
          </label>
        </div>
        {hasKids && !isCollapsed && (
          <ul>
            {node.children.map((c) => groupRow(c, childAncestors, checked))}
            {node.leaves.map((l) => leafRow(l, childAncestors, checked))}
            {!node.pending && node.children.length === 0 && node.leaves.length === 0 && (
              <li>
                <div className="tree__group">{mode === 'zone' ? '구역 없음' : '단말 없음'}</div>
              </li>
            )}
          </ul>
        )}
      </li>
    );
  };

  return (
    <>
      <div className="tree-picker__search">
        <input
          type="search"
          value={query}
          placeholder={
            mode === 'device' ? '단말·마을 이름 검색' : mode === 'zone' ? '구역·마을 이름 검색' : '마을·기관 이름 검색'
          }
          onChange={(e) => setQuery(e.target.value)}
          aria-label="방송 대상 검색"
        />
      </div>
      {shown.length === 0 ? (
        <p className="hint">검색 결과가 없습니다.</p>
      ) : (
        <ul className="tree tree-picker">
          {shown.map((r) =>
            r.name === '' ? (
              // 이장: 기관 칸 없이 담당 마을만
              r.leaves.map((l) => leafRow(l, [], false))
            ) : (
              groupRow(r, [], false)
            ),
          )}
        </ul>
      )}
    </>
  );
}
