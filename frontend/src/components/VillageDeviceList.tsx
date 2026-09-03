/**
 * 마을별 접이식 단말 목록 — 대시보드 왼쪽 패널.
 *
 * 지도 마커와 같은 데이터(/api/dashboard/map)의 다른 표현이다(지도 설계 §4.5).
 * 연동은 부모의 selectedMac/hoveredMac 두 상태로만 한다 — 지도를 직접 부르지 않는다.
 * 문제(오프라인·무음)가 있는 단말을 마을 안에서 위로 정렬한다.
 */

import { useMemo, useState } from 'react';

import type { MapPin } from '../api/types';

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

export function VillageDeviceList({
  pins,
  missing,
  selectedMac,
  hoveredMac,
  onSelect,
  onHover,
}: {
  pins: MapPin[];
  missing: string[];
  selectedMac: string | null;
  hoveredMac: string | null;
  onSelect: (mac: string | null) => void;
  onHover: (mac: string | null) => void;
}) {
  //: 접힌 마을 이름들. 기본은 전부 펼침 — 한눈에 보는 게 대시보드의 일이다.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const groups = useMemo(() => {
    const byVillage = new Map<string, MapPin[]>();
    for (const p of pins) {
      const key = p.village_name ?? '미배정';
      const list = byVillage.get(key);
      if (list) list.push(p);
      else byVillage.set(key, [p]);
    }
    for (const list of byVillage.values()) {
      list.sort(
        (a, b) =>
          statusRank(a) - statusRank(b) || (a.label ?? a.mac).localeCompare(b.label ?? b.mac),
      );
    }
    // 마을 이름순, 미배정은 맨 뒤.
    return [...byVillage.entries()].sort(([a], [b]) =>
      a === '미배정' ? 1 : b === '미배정' ? -1 : a.localeCompare(b),
    );
  }, [pins]);

  const toggle = (name: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  if (pins.length === 0) {
    return <div className="empty">표시할 단말이 없습니다.</div>;
  }

  return (
    // 구분선은 마을 사이에만 둔다. 단말 사이에 줄을 그으면 목록이 표처럼 무거워지고,
    // 어차피 마을 머리말이 묶음을 말해 준다(2026-09-03 현장 요청).
    <div className="vlist">
      {groups.map(([village, list]) => {
        const online = list.filter((p) => p.online).length;
        const isCollapsed = collapsed.has(village);
        return (
          <section key={village} className="vlist__group">
            <button
              type="button"
              className="vlist__head"
              onClick={() => toggle(village)}
              aria-expanded={!isCollapsed}
            >
              <span aria-hidden="true">{isCollapsed ? '▶' : '▼'}</span> {village}{' '}
              <span className="dim vlist__count">
                온라인 {online}/{list.length}
              </span>
            </button>
            {!isCollapsed && (
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
            )}
          </section>
        );
      })}
      {missing.length > 0 && (
        <p className="hint">
          좌표 없음(지도 제외) {missing.length}대: <span className="mono dim">{missing.join(', ')}</span>
        </p>
      )}
    </div>
  );
}
