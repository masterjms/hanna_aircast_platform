/**
 * 대시보드 — [왼쪽: 요약 타일 + 마을별 단말 목록 + 이상단말] : [오른쪽: 지도] = 4:6.
 *
 * 지도와 목록은 /api/dashboard/map 한 벌의 두 표현이고(지도 설계 §4.5),
 * 연동은 selectedMac/hoveredMac 두 상태뿐이다(§4.6). 최근 이력은 이력 탭으로
 * 옮겼고, 진행 중 방송 표는 뺐다(방송 제어 화면의 일) — 타일 카운트만 남긴다.
 *
 * 진행 중인 방송이 있으면 폴링을 2초로 당긴다(사양: 기본 5초, 방송 중 2초).
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { api } from '../api/client';
import type { Organization, Village } from '../api/types';
import { MapView } from '../components/MapView';
import { VillageDeviceList } from '../components/VillageDeviceList';
import { useAuth } from '../auth/AuthContext';
import { POLL_INTERVAL, usePolling } from '../hooks/usePolling';

type Tone = 'ok' | 'warn' | 'danger' | 'idle';

function Tile({
  label,
  value,
  unit,
  note,
  tone = 'idle',
}: {
  label: string;
  value: number;
  unit: string;
  note: string;
  tone?: Tone;
}) {
  return (
    <div className={`tile tile--${tone}`}>
      <div className="tile__head">
        <span className="tile__label">{label}</span>
        <span className="tile__dot" aria-hidden="true" />
      </div>
      <div className="tile__row">
        <span className="tile__value">{value}</span>
        <span className="tile__unit">{unit}</span>
      </div>
      <div className="tile__note">{note}</div>
    </div>
  );
}

/**
 * 지도가 차지하는 폭(%) — 향후검토 13번. 세 단계 버튼과 끌어서 조절하는 손잡이가 같은
 * 값을 바꾸고, 브라우저에 기억한다(다음에 열어도 그 크기). 예전 고정값은 60% 였는데
 * 「지도가 너무 크다」는 지적이라 기본을 50% 로 낮췄다.
 */
const MAP_PRESETS = [
  { label: '작게', pct: 35 },
  { label: '보통', pct: 50 },
  { label: '크게', pct: 65 },
] as const;
const MAP_PCT_KEY = 'xwifi.dashboard.mapPct';
const MAP_PCT_MIN = 25;
const MAP_PCT_MAX = 75;

function readMapPct(): number {
  try {
    const v = Number(localStorage.getItem(MAP_PCT_KEY));
    if (v >= MAP_PCT_MIN && v <= MAP_PCT_MAX) return v;
  } catch {
    /* 저장소를 못 쓰는 브라우저 — 기본값 */
  }
  return 50;
}

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('ko-KR', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function DashboardPage() {
  const { user } = useAuth();
  const [selectedMac, setSelectedMac] = useState<string | null>(null);
  const [hoveredMac, setHoveredMac] = useState<string | null>(null);

  // 기본 주기로 시작해서, 진행 중인 방송이 잡히면 다음 주기부터 당긴다.
  const [fastMode, setFastMode] = useState(false);
  const interval = fastMode ? POLL_INTERVAL.broadcasting : POLL_INTERVAL.normal;
  const { data, loading } = usePolling(() => api.dashboard.summary(), interval);
  const map = usePolling(() => api.dashboard.map(), interval);

  const broadcasting = (data?.active_broadcasts.length ?? 0) > 0;
  useEffect(() => setFastMode(broadcasting), [broadcasting]);

  // 기관 트리(향후검토 6번). 구조는 자주 안 바뀌므로 열 때 한 번 읽는다.
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [villages, setVillages] = useState<Village[]>([]);
  useEffect(() => {
    void Promise.all([
      api.organizations.list().catch(() => [] as Organization[]),
      api.villages.list().catch(() => [] as Village[]),
    ]).then(([o, v]) => {
      setOrgs(o);
      setVillages(v);
    });
  }, []);

  // ── 지도 크기(향후검토 13번) ──
  const [mapPct, setMapPctState] = useState(readMapPct);
  const [dragging, setDragging] = useState(false);
  const rowRef = useRef<HTMLDivElement>(null);
  const setMapPct = useCallback((pct: number) => {
    if (!Number.isFinite(pct)) return;
    const clamped = Math.round(Math.min(MAP_PCT_MAX, Math.max(MAP_PCT_MIN, pct)));
    setMapPctState(clamped);
    try {
      localStorage.setItem(MAP_PCT_KEY, String(clamped));
    } catch {
      /* 기억만 못 할 뿐 크기는 바뀐다 */
    }
  }, []);

  const onSplitDown = (e: React.PointerEvent) => {
    e.preventDefault();
    setDragging(true);
    document.body.classList.add('is-resizing');
    const onMove = (ev: PointerEvent) => {
      const box = rowRef.current?.getBoundingClientRect();
      // 폭이 0 이면(화면이 그려지기 전) 나눗셈이 NaN 이 되어 저장값이 깨진다.
      if (!box || box.width <= 0 || !Number.isFinite(ev.clientX)) return;
      setMapPct(((box.right - ev.clientX) / box.width) * 100);
    };
    const onUp = () => {
      setDragging(false);
      document.body.classList.remove('is-resizing');
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onUp);
    };
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onUp);
  };

  if (loading && !data) {
    return <div className="empty">불러오는 중…</div>;
  }
  if (!data) {
    return <div className="alert">대시보드를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.</div>;
  }

  const { devices, alerts } = data;
  const total = devices.total + devices.unassigned;

  return (
    // 상단바가 이미 "전체 개요"를 보여주므로 제목을 반복하지 않는다.
    <div ref={rowRef} className="dash">
      {/* ── 왼쪽: 요약 + 기관·마을별 단말 + 이상단말 ──
          세로 flex 로 세 구역을 쌓고, 단말 목록만 남는 높이를 차지해 안에서
          스크롤한다. 단말·마을이 늘어도 화면 전체가 길어지지 않는다 — 타일과
          이상단말은 항상 제자리에 있어야 한다(2026-09-03 현장 요청). */}
      <div
        style={{
          flex: `1 1 ${100 - mapPct}%`,
          minWidth: 0,
          display: 'flex',
          flexDirection: 'column',
          gap: 14,
          paddingRight: 4,
        }}
      >
        {/* 좁은 왼쪽 칼럼에서도 와이어프레임처럼 2×2 를 유지한다 */}
        <div className="tiles" style={{ gridTemplateColumns: 'repeat(2, 1fr)', marginBottom: 0 }}>
          <Tile
            label="온라인"
            value={devices.online}
            unit={`/ ${total}`}
            note="최근 5분 내 STATUS 수신 기준"
            tone="ok"
          />
          <Tile
            label="오프라인"
            value={devices.offline}
            unit="대"
            note="LWT 수신 또는 5분 이상 무응답"
            tone={devices.offline ? 'danger' : 'idle'}
          />
          <Tile
            label="방송 중"
            value={data.active_broadcasts.length}
            unit="건"
            note={broadcasting ? '진행 중 — 방송 제어에서 확인' : '진행 중인 방송 없음'}
            tone={broadcasting ? 'warn' : 'idle'}
          />
          {user?.all_villages && (
            <Tile
              label="미배정"
              value={devices.unassigned}
              unit="대"
              note="마을 배정 대기 중"
              tone={devices.unassigned ? 'warn' : 'idle'}
            />
          )}
        </div>

        <section className="card" style={{ padding: 10, flex: 1, minHeight: 0, overflowY: 'auto' }}>
          <VillageDeviceList
            pins={map.data?.pins ?? []}
            missing={map.data?.missing_location ?? []}
            orgs={orgs}
            villages={villages}
            selectedMac={selectedMac}
            hoveredMac={hoveredMac}
            onSelect={setSelectedMac}
            onHover={setHoveredMac}
          />
        </section>

        <section style={{ flex: 'none' }}>
          <h2 className="section-title">
            이상단말{' '}
            {alerts.length > 0 && (
              <span style={{ color: 'var(--danger-text)', fontSize: 12 }}>{alerts.length}건</span>
            )}
          </h2>
          {/* 이상단말이 많아져도 이 높이 안에서만 스크롤한다 */}
          <div className="table-wrap table-wrap--scroll" style={{ maxHeight: 180, overflowY: 'auto' }}>
            {alerts.length === 0 ? (
              <div className="empty">모든 단말이 정상입니다.</div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>별칭</th>
                    <th>마을</th>
                    <th>상태</th>
                    <th>마지막 통신</th>
                  </tr>
                </thead>
                <tbody>
                  {alerts.map((a) => (
                    <tr key={a.mac}>
                      <td className="strong">
                        {a.label ?? <span className="mono dim">{a.mac}</span>}
                      </td>
                      <td>{a.village_name ?? '미배정'}</td>
                      <td>
                        <span className="badge badge--danger">{a.reason}</span>
                      </td>
                      <td className="dim">{formatTime(a.last_seen_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>
      </div>

      {/* 목록과 지도 사이 손잡이 — 끌어서 지도 폭을 바꾼다. 키보드는 ←→ 로 5%씩. */}
      <div
        className={`dash-split${dragging ? ' is-dragging' : ''}`}
        role="separator"
        aria-orientation="vertical"
        aria-label="지도 크기 조절"
        aria-valuemin={MAP_PCT_MIN}
        aria-valuemax={MAP_PCT_MAX}
        aria-valuenow={mapPct}
        tabIndex={0}
        onPointerDown={onSplitDown}
        onKeyDown={(e) => {
          if (e.key === 'ArrowLeft') setMapPct(mapPct + 5);
          if (e.key === 'ArrowRight') setMapPct(mapPct - 5);
        }}
        title="끌어서 지도 크기 조절"
      />

      {/* ── 오른쪽: 지도 ── */}
      <div
        className="card"
        style={{ flex: `1 1 ${mapPct}%`, minWidth: 0, padding: 6, position: 'relative' }}
      >
        <div className="map-size" role="group" aria-label="지도 크기">
          {MAP_PRESETS.map((p) => (
            <button
              key={p.label}
              type="button"
              aria-pressed={mapPct === p.pct}
              onClick={() => setMapPct(p.pct)}
            >
              {p.label}
            </button>
          ))}
        </div>
        {map.data?.kakao_js_key ? (
          <MapView
            jsKey={map.data.kakao_js_key}
            pins={map.data.pins}
            villages={map.data.villages}
            selectedMac={selectedMac}
            hoveredMac={hoveredMac}
            onSelect={setSelectedMac}
            onHover={setHoveredMac}
          />
        ) : map.data && map.data.kakao_js_key === null ? (
          <div className="empty">
            카카오 JavaScript 키가 설정되지 않았습니다 — 서버 .env 에 KAKAO_JS_KEY 를 넣고
            재기동하세요.
          </div>
        ) : (
          <div className="empty">지도를 불러오는 중…</div>
        )}
      </div>
    </div>
  );
}
