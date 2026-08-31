/**
 * 지도 — 왼쪽 단말 목록 + 오른쪽 카카오맵 (지도 설계 §4.5).
 *
 * 목록과 마커는 /api/dashboard/map 응답 한 벌을 두 가지로 그린 것이다 —
 * 데이터를 두 번 받지 않고, 연동은 selectedMac/hoveredMac 두 상태로만 한다(§4.6).
 * 지도는 "어디가 문제인가", 목록은 "무엇이 문제인가" — 오프라인이 목록 위로 온다.
 */

import { useMemo, useState } from 'react';

import { api } from '../api/client';
import { MapView } from '../components/MapView';
import { POLL_INTERVAL, usePolling } from '../hooks/usePolling';
import type { MapPin } from '../api/types';

function statusRank(p: MapPin): number {
  if (!p.online) return 0; // 문제부터 위로
  if (p.live === 'RECONNECTING') return 1;
  return 2;
}

function DeviceListPanel({
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
  const sorted = useMemo(
    () =>
      [...pins].sort(
        (a, b) => statusRank(a) - statusRank(b) || (a.label ?? a.mac).localeCompare(b.label ?? b.mac),
      ),
    [pins],
  );

  return (
    <aside
      className="card"
      style={{ width: 300, flexShrink: 0, overflowY: 'auto', padding: 10 }}
    >
      {sorted.length === 0 && <div className="empty">표시할 단말이 없습니다.</div>}
      <ul className="plain-list">
        {sorted.map((p) => {
          const active = p.mac === selectedMac || p.mac === hoveredMac;
          return (
            <li key={p.mac}>
              <button
                type="button"
                className="btn btn--ghost"
                style={{
                  width: '100%',
                  textAlign: 'left',
                  display: 'block',
                  outline: active ? '2px solid var(--accent, #4a90d9)' : 'none',
                }}
                ref={(el) => {
                  // 마커 클릭 → 목록이 그 항목으로 따라온다 (§4.5 연동 규칙)
                  if (el && p.mac === selectedMac) {
                    el.scrollIntoView({ block: 'nearest' });
                  }
                }}
                onClick={() => onSelect(p.mac === selectedMac ? null : p.mac)}
                onMouseEnter={() => onHover(p.mac)}
                onMouseLeave={() => onHover(null)}
              >
                <span className="strong">{p.label || p.mac}</span>{' '}
                <span
                  className={`badge badge--${!p.online ? 'danger' : p.live === 'RECONNECTING' ? 'warn' : 'ok'}`}
                >
                  {!p.online ? '오프라인' : p.live === 'RECONNECTING' ? '무음(재접속)' : '온라인'}
                </span>
                <br />
                <span className="dim">
                  {p.village_name ?? '미배정'}
                  {p.position_source !== 'device' && ' · 위치 미입력(마을 좌표)'}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {missing.length > 0 && (
        <p className="hint">
          좌표 없음(지도 제외) {missing.length}대:{' '}
          <span className="mono dim">{missing.join(', ')}</span> — 마을을 배정하거나 단말
          관리에서 위치를 입력하세요.
        </p>
      )}
    </aside>
  );
}

export function MapPage() {
  const [selectedMac, setSelectedMac] = useState<string | null>(null);
  const [hoveredMac, setHoveredMac] = useState<string | null>(null);

  const data = usePolling(() => api.dashboard.map(), POLL_INTERVAL.normal);

  return (
    <>
      <div className="page-head">
        <h1>지도</h1>
        <p>
          {data.data?.pins.length ?? 0}대 표시 중 · {POLL_INTERVAL.normal / 1000}초마다 갱신 ·
          위치를 안 적은 단말은 마을 좌표 자리에 표시됩니다
        </p>
      </div>

      {data.error && (
        <div className="alert" style={{ marginBottom: 14 }}>
          {data.error.message}
        </div>
      )}

      {data.data && data.data.kakao_js_key === null ? (
        <div className="empty">
          카카오 JavaScript 키가 설정되지 않았습니다 — 서버 .env 에 KAKAO_JS_KEY 를 넣고
          재기동하세요.
        </div>
      ) : (
        <div style={{ display: 'flex', gap: 14, height: 'calc(100vh - 190px)', minHeight: 420 }}>
          <DeviceListPanel
            pins={data.data?.pins ?? []}
            missing={data.data?.missing_location ?? []}
            selectedMac={selectedMac}
            hoveredMac={hoveredMac}
            onSelect={setSelectedMac}
            onHover={setHoveredMac}
          />
          <div className="card" style={{ flex: 1, padding: 6, minWidth: 0 }}>
            {data.data?.kakao_js_key ? (
              <MapView
                jsKey={data.data.kakao_js_key}
                pins={data.data.pins}
                selectedMac={selectedMac}
                hoveredMac={hoveredMac}
                onSelect={setSelectedMac}
                onHover={setHoveredMac}
              />
            ) : (
              <div className="empty">불러오는 중…</div>
            )}
          </div>
        </div>
      )}
    </>
  );
}
