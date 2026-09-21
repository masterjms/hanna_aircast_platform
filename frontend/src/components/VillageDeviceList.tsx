/**
 * 대시보드 왼쪽 단말 목록 — 기관 > 마을 > 단말 계층(향후검토 6번, 2026-09-21).
 *
 * 지도 마커와 같은 데이터(/api/dashboard/map)의 다른 표현이다(지도 설계 §4.5).
 * 연동은 부모의 selectedMac/hoveredMac 두 상태로만 한다 — 지도를 직접 부르지 않는다.
 *
 * 대시보드는 한눈에 보는 화면이라 **기관은 접힌 요약으로 시작**한다(온라인/전체, 오프라인·
 * 무음 대수). 펼치면 하위 기관과 마을, 마을을 펼치면 단말이다. 지도에서 핀을 누르면 그
 * 단말이 든 가지가 저절로 펼쳐지고 목록이 따라온다. 문제(오프라인·무음) 단말은 마을
 * 안에서 위로 정렬한다.
 *
 * 기관이 없는 계정(이장)은 예전처럼 마을 목록으로 바로 보인다.
 */

import { useEffect, useMemo, useState } from 'react';

import type { MapPin, Organization, Village } from '../api/types';
import { buildForest, type OrgNode } from '../lib/orgtree';

function statusRank(p: MapPin): number {
  if (!p.online) return 0;
  if (p.live === 'RECONNECTING') return 1;
  return 2;
}

function statusBadge(p: MapPin) {
  return (
    <span
      className={`badge badge--${!p.online ? 'danger' : p.live === 'RECONNECTING' ? 'warn' : 'ok'}`}
    >
      {!p.online ? '오프라인' : p.live === 'RECONNECTING' ? '무음' : '온라인'}
    </span>
  );
}

interface Stats {
  total: number;
  online: number;
  offline: number;
  silent: number;
}

function statsOf(pins: MapPin[]): Stats {
  const s: Stats = { total: pins.length, online: 0, offline: 0, silent: 0 };
  for (const p of pins) {
    if (!p.online) s.offline += 1;
    else {
      s.online += 1;
      if (p.live === 'RECONNECTING') s.silent += 1;
    }
  }
  return s;
}

function StatLine({ s }: { s: Stats }) {
  return (
    <span className="vlist__count">
      <span className="dim">
        온라인 {s.online}/{s.total}
      </span>
      {s.offline > 0 && <span className="badge badge--danger">오프라인 {s.offline}</span>}
      {s.silent > 0 && <span className="badge badge--warn">무음 {s.silent}</span>}
    </span>
  );
}

export function VillageDeviceList({
  pins,
  missing,
  orgs,
  villages,
  selectedMac,
  hoveredMac,
  onSelect,
  onHover,
}: {
  pins: MapPin[];
  missing: string[];
  orgs: Organization[];
  villages: Village[];
  selectedMac: string | null;
  hoveredMac: string | null;
  onSelect: (mac: string | null) => void;
  onHover: (mac: string | null) => void;
}) {
  // 펼친 가지. 키는 'o:<id>' · 'v:<id>' · 'v:none'. 기본은 전부 접힘 — 기관 요약부터 본다.
  // 기관이 없는 계정(이장)은 마을이 곧 맨 위라 마을을 펼친 채로 시작한다.
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const flat = orgs.length === 0;

  const pinsByVillage = useMemo(() => {
    const m = new Map<number | null, MapPin[]>();
    for (const p of pins) {
      const list = m.get(p.village_id) ?? [];
      list.push(p);
      m.set(p.village_id, list);
    }
    for (const list of m.values()) {
      list.sort(
        (a, b) =>
          statusRank(a) - statusRank(b) || (a.label ?? a.mac).localeCompare(b.label ?? b.mac),
      );
    }
    return m;
  }, [pins]);

  const forest = useMemo(() => buildForest(orgs, villages), [orgs, villages]);

  // 기관 아래 전부의 단말(요약용).
  const subtreePins = useMemo(() => {
    const memo = new Map<number, MapPin[]>();
    const walk = (n: OrgNode): MapPin[] => {
      const out: MapPin[] = [];
      for (const v of n.villages) out.push(...(pinsByVillage.get(v.id) ?? []));
      for (const c of n.children) out.push(...walk(c));
      memo.set(n.org.id, out);
      return out;
    };
    forest.roots.forEach(walk);
    return memo;
  }, [forest, pinsByVillage]);

  // 지도에서 핀을 고르면 그 단말이 든 가지를 펼친다 — 목록이 따라와야 한다(§4.5).
  useEffect(() => {
    if (!selectedMac) return;
    const pin = pins.find((p) => p.mac === selectedMac);
    if (!pin) return;
    const keys = [pin.village_id === null ? 'v:none' : `v:${pin.village_id}`];
    const v = villages.find((x) => x.id === pin.village_id);
    let node = v?.organization_id != null ? forest.byId.get(v.organization_id) : undefined;
    while (node) {
      keys.push(`o:${node.org.id}`);
      node = node.parent ?? undefined;
    }
    setOpen((prev) => {
      // 이장(평평한 목록)은 마을이 기본으로 펼쳐져 있고 set 에는 "접은" 마을이 든다.
      if (flat) {
        if (!prev.has(keys[0])) return prev;
        const next = new Set(prev);
        next.delete(keys[0]);
        return next;
      }
      if (keys.every((k) => prev.has(k))) return prev;
      const next = new Set(prev);
      keys.forEach((k) => next.add(k));
      return next;
    });
  }, [selectedMac, pins, villages, forest, flat]);

  const toggle = (key: string) =>
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  if (pins.length === 0) {
    return <div className="empty">표시할 단말이 없습니다.</div>;
  }

  const deviceItems = (list: MapPin[]) => (
    <ul className="vlist__items">
      {list.map((p) => {
        const active = p.mac === selectedMac || p.mac === hoveredMac;
        return (
          <li key={p.mac}>
            <button
              type="button"
              className={`vlist__item${active ? ' is-active' : ''}`}
              ref={(el) => {
                // 마커 클릭 → 목록이 그 항목으로 따라온다(§4.5)
                if (el && p.mac === selectedMac) el.scrollIntoView({ block: 'nearest' });
              }}
              onClick={() => onSelect(p.mac === selectedMac ? null : p.mac)}
              onMouseEnter={() => onHover(p.mac)}
              onMouseLeave={() => onHover(null)}
              title={p.position_source !== 'device' ? '위치 미입력 — 마을 좌표에 표시' : undefined}
            >
              <span className="strong vlist__name">{p.label || p.mac}</span>
              {statusBadge(p)}
            </button>
          </li>
        );
      })}
    </ul>
  );

  const villageBlock = (key: string, name: string, list: MapPin[], forceOpen = false) => {
    if (list.length === 0) return null;
    const isOpen = forceOpen ? !open.has(key) : open.has(key);
    return (
      <section key={key} className="vlist__group">
        <button
          type="button"
          className="vlist__head"
          onClick={() => toggle(key)}
          aria-expanded={isOpen}
        >
          <span aria-hidden="true">{isOpen ? '▼' : '▶'}</span> {name} <StatLine s={statsOf(list)} />
        </button>
        {isOpen && deviceItems(list)}
      </section>
    );
  };

  const orgBlock = (node: OrgNode): React.ReactNode => {
    const list = subtreePins.get(node.org.id) ?? [];
    if (list.length === 0) return null; // 단말 없는 가지는 대시보드에서 뺀다
    const key = `o:${node.org.id}`;
    const isOpen = open.has(key);
    return (
      <section key={key} className="vlist__group vlist__group--org">
        <button
          type="button"
          className="vlist__head vlist__head--org"
          onClick={() => toggle(key)}
          aria-expanded={isOpen}
        >
          <span aria-hidden="true">{isOpen ? '▼' : '▶'}</span>{' '}
          <span className="strong">{node.org.name}</span> <StatLine s={statsOf(list)} />
        </button>
        {isOpen && (
          <div className="vlist__children">
            {node.children.map(orgBlock)}
            {node.villages.map((v) =>
              villageBlock(`v:${v.id}`, v.name, pinsByVillage.get(v.id) ?? []),
            )}
          </div>
        )}
      </section>
    );
  };

  const unassigned = pinsByVillage.get(null) ?? [];

  return (
    // 구분선은 묶음 사이에만 둔다. 단말 사이에 줄을 그으면 목록이 표처럼 무거워진다
    // (2026-09-03 현장 요청).
    <div className="vlist">
      {flat
        ? villages.map((v) => villageBlock(`v:${v.id}`, v.name, pinsByVillage.get(v.id) ?? [], true))
        : forest.roots.map(orgBlock)}
      {!flat &&
        forest.orphans.length > 0 &&
        forest.orphans.map((v) => villageBlock(`v:${v.id}`, v.name, pinsByVillage.get(v.id) ?? []))}
      {villageBlock('v:none', '미배정', unassigned)}
      {missing.length > 0 && (
        <p className="hint">
          좌표 없음(지도 제외) {missing.length}대: <span className="mono dim">{missing.join(', ')}</span>
        </p>
      )}
    </div>
  );
}
