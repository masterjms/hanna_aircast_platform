/**
 * 마을 현황(대시보드) — 위: [요약 타일 + 마을별 단말 목록] : [지도], 아래: 이상 단말 표(전체 폭).
 *
 * 지도와 목록은 /api/dashboard/map 한 벌의 두 표현이고(지도 설계 §4.5),
 * 연동은 selectedMac/hoveredMac 두 상태뿐이다(§4.6). 이상 단말 표의 행을 눌러도 같은
 * selectedMac 이 바뀌어 지도가 그 단말로 간다(문제점 58번 ⑤).
 *
 * 2026-10-06 (문제점 58번): 단말 목록이 지도 아래 끝까지 내려오고, 이상 단말은 왼쪽 구석의
 * 작은 표가 아니라 지도 아래 전체 폭에 단말 관리와 같은 열(별칭·마을·MAC·상태·RSSI·CFG·
 * 마지막 통신·버전)로 놓인다. 검색과 10·20·50건 쪽 넘기기는 방송 기록과 같다.
 * 2026-10-07 (58번 보조설명): 이 화면은 **한 화면에 맞추지 않는다**. 위 [목록 | 지도]가 화면 높이
 * 가까이 차지하고(트리가 전보다 두 배), 이상 단말 표는 그 아래 — 화면을 내려서 본다. 표는 안에서
 * 스크롤하지 않고 고른 건수만큼 다 보이며 쪽 넘기기 버튼은 늘 표 아래에 있다.
 * 이상 단말의 데이터는 단말 목록 API(status=offline)를 그대로 쓴다 — 요약 API 의 alerts 는
 * 열이 모자라고(별칭·마을·사유뿐) 20건에서 잘린다.
 *
 * 진행 중인 방송이 있으면 폴링을 2초로 당긴다(사양: 기본 5초, 방송 중 2초).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';

import { api } from '../api/client';
import type { Device, GeoGeometry, MapVillage, Organization, Village } from '../api/types';
import { MapView } from '../components/MapView';
import { PageSizeSelect, PagerBar, usePager } from '../components/Pager';
import { VillageDeviceList } from '../components/VillageDeviceList';
import { useAuth } from '../auth/AuthContext';
import { POLL_INTERVAL, usePolling } from '../hooks/usePolling';

type Tone = 'ok' | 'warn' | 'danger' | 'idle';

/**
 * 현황 카드(문제점 85번 ②, 2026-10-12) — 제목·상태 배지·큰 숫자·막대 그래프. 기준 설명은 늘 보이지 않고
 * 카드에 마우스를 올리면(또는 키보드로 초점을 주면) 나타난다. 숫자가 주인공이 되게 한다.
 */
function StatCard({
  label,
  value,
  unit,
  ratio,
  percent,
  badge,
  tip,
  tone = 'idle',
  onClick,
  action,
}: {
  label: string;
  value: number;
  unit: string;
  /** 막대의 채움(0~1). 없으면 막대를 비워 둔다. */
  ratio: number;
  /** 숫자 옆 백분율 칩 */
  percent?: number;
  badge: string;
  tip: string;
  tone?: Tone;
  onClick?: () => void;
  /** 숫자 옆 짧은 행동 안내(예: 「원인 확인 ›」) */
  action?: string;
}) {
  const fill = Math.max(0, Math.min(1, Number.isFinite(ratio) ? ratio : 0));
  const Tag = onClick ? 'button' : 'div';
  return (
    <Tag
      type={onClick ? 'button' : undefined}
      className={`stat stat--${tone}${onClick ? ' stat--action' : ''}`}
      onClick={onClick}
      tabIndex={onClick ? undefined : 0}
      aria-label={`${label} ${value}${unit}. ${tip}`}
    >
      <div className="stat__head">
        <span className="stat__label">{label}</span>
        <span className={`stat__badge stat__badge--${tone}`}>
          <span className="stat__dot" aria-hidden="true" />
          {badge}
        </span>
      </div>
      <div className="stat__row">
        <span className="stat__value">{value}</span>
        <span className="stat__unit">{unit}</span>
        {percent !== undefined && <span className={`stat__pct stat__pct--${tone}`}>{percent.toFixed(1)}%</span>}
        {action && <span className="stat__action">{action} ›</span>}
      </div>
      <div className="stat__bar" aria-hidden="true">
        <span style={{ width: `${(fill * 100).toFixed(1)}%` }} />
      </div>
      <div className="stat__tip" role="tooltip">
        {tip}
      </div>
    </Tag>
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

function signalTone(rssi: number | null): Tone {
  if (rssi === null) return 'idle';
  if (rssi >= -60) return 'ok';
  if (rssi >= -75) return 'warn';
  return 'danger';
}

const TONE_VAR: Record<Tone, string> = {
  ok: 'var(--ok-text)',
  warn: 'var(--warn-text)',
  danger: 'var(--danger-text)',
  idle: 'var(--text-3)',
};

/** 왜 이상인가 — 서버 요약의 _alert_reason 과 같은 세 가지. */
function alertReason(d: Device): string {
  if (!d.last_seen_at) return '한 번도 통신하지 않음';
  if (d.state === 'OFFLINE') return '연결 끊김(LWT)';
  return '응답 없음';
}

/** 지도 아래 이상 단말 표 — 단말 관리와 같은 열, 방송 기록과 같은 검색·쪽 넘기기. */
function AlertTable({
  devices,
  selectedMac,
  onSelect,
}: {
  devices: Device[] | null;
  selectedMac: string | null;
  onSelect: (mac: string | null) => void;
}) {
  const [q, setQ] = useState('');

  const filtered = useMemo(() => {
    const list = devices ?? [];
    const needle = q.trim().toLowerCase();
    if (!needle) return list;
    return list.filter((d) => [d.label, d.mac, d.village_name].some((v) => v?.toLowerCase().includes(needle)));
  }, [devices, q]);
  // 목록 공통 쪽 넘기기(문제점 64번). 필터로 쪽 수가 줄면 마지막 쪽으로.
  const pager = usePager(filtered);
  const rows = pager.rows;

  return (
    <section className="dash__alerts">
      <div className="filters">
        <h2 className="section-title" style={{ margin: 0 }}>
          이상 단말{' '}
          {devices && devices.length > 0 && (
            <span style={{ color: 'var(--danger-text)', fontSize: 12 }}>{devices.length}대</span>
          )}
        </h2>
        <div className="filters__spacer" />
        <input
          type="search"
          placeholder="별칭 · MAC · 지역"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            pager.setPage(1);
          }}
          style={{ minWidth: 220 }}
          aria-label="이상 단말 검색"
        />
        <PageSizeSelect value={pager.size} onChange={pager.setSize} />
      </div>
      <div className="table-wrap table-wrap--scroll">
        {devices === null ? (
          <div className="empty">불러오는 중…</div>
        ) : filtered.length === 0 ? (
          <div className="empty">{q ? '조건에 맞는 이상 단말이 없습니다.' : '모든 단말이 정상입니다.'}</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>별칭</th>
                <th>지역</th>
                <th className="mono">MAC</th>
                <th>상태</th>
                <th className="num">RSSI</th>
                <th className="num">CFG</th>
                <th>마지막 통신</th>
                <th title="마지막 STATUS 가 보고한 펌웨어 — APP(P4) / Wi-Fi(C6) 모듈">버전 (APP / Wi-Fi)</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((d) => (
                <tr
                  key={d.mac}
                  className={d.mac === selectedMac ? 'is-active' : undefined}
                  onClick={() => onSelect(d.mac === selectedMac ? null : d.mac)}
                  title="누르면 지도에서 이 단말을 보여 줍니다"
                >
                  <td className="strong">{d.label || <span className="mono dim">{d.mac}</span>}</td>
                  <td>{d.village_name ?? '미배정'}</td>
                  <td className="mono">{d.mac}</td>
                  <td>
                    {/* 단말 관리와 같은 글자 「오프라인」(문제점 61번). 사유는 마우스를 올리면. */}
                    <span className="badge badge--danger" title={alertReason(d)}>
                      오프라인
                    </span>
                  </td>
                  <td className="num" style={{ color: TONE_VAR[signalTone(d.rssi)], fontWeight: 600 }}>
                    {d.rssi ?? '—'}
                  </td>
                  <td className="num">{d.config_version ?? '—'}</td>
                  <td className="dim" title={alertReason(d)}>{formatTime(d.last_seen_at)}</td>
                  <td className="mono dim">
                    {(d.p4_fw ?? d.p4_version) || '—'} / {(d.c6_fw ?? d.c6_version) || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      {pager.total > 0 && (
        <PagerBar total={pager.total} page={pager.page} pages={pager.pages} onPage={pager.setPage} unit="대" />
      )}
    </section>
  );
}

export function DashboardPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [selectedMac, setSelectedMac] = useState<string | null>(null);
  const [hoveredMac, setHoveredMac] = useState<string | null>(null);

  // 기본 주기로 시작해서, 진행 중인 방송이 잡히면 다음 주기부터 당긴다.
  const [fastMode, setFastMode] = useState(false);
  const interval = fastMode ? POLL_INTERVAL.broadcasting : POLL_INTERVAL.normal;
  const { data, loading } = usePolling(() => api.dashboard.summary(), interval);
  const map = usePolling(() => api.dashboard.map(), interval);
  // 이상 단말 = 오프라인 단말. 판정은 서버 단말 목록(status=offline)이 요약 타일과 같은 규칙으로 한다.
  const offline = usePolling(() => api.devices.list({ status: 'offline' }), interval);

  const broadcasting = (data?.active_broadcasts.length ?? 0) > 0;
  useEffect(() => setFastMode(broadcasting), [broadcasting]);

  // 기관 트리(향후검토 6번)와 마을 경계(병목 감사 H5). 둘 다 자주 안 바뀌므로 열 때 한 번 읽는다.
  // 경계는 수 MB 라 /map 폴링에서 떼어 냈다 — 폴링 응답은 핀과 마을 이름뿐이다.
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [villages, setVillages] = useState<Village[]>([]);
  const [boundaries, setBoundaries] = useState<Record<number, GeoGeometry>>({});
  useEffect(() => {
    void Promise.all([
      api.organizations.list().catch(() => [] as Organization[]),
      api.villages.list().catch(() => [] as Village[]),
      api.dashboard.boundaries().catch(() => []),
    ]).then(([o, v, b]) => {
      setOrgs(o);
      setVillages(v);
      setBoundaries(Object.fromEntries(b.map((x) => [x.id, x.boundary])));
    });
  }, []);
  const mapVillages = useMemo<MapVillage[]>(
    () => (map.data?.villages ?? []).map((v) => ({ ...v, boundary: boundaries[v.id] ?? null })),
    [map.data?.villages, boundaries],
  );

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

  const { devices } = data;
  const total = devices.total + devices.unassigned;

  return (
    // 상단바가 이미 "전체 개요"를 보여주므로 제목을 반복하지 않는다.
    <div className="dash">
      <div ref={rowRef} className="dash__top">
        {/* ── 왼쪽: 요약 + 기관·마을별 단말 ──
            타일은 제자리에 있고 단말 목록이 남는 높이(지도 아래 끝까지)를 차지해 안에서
            스크롤한다. 단말·지역이 늘어도 화면 전체가 길어지지 않는다. */}
        <div className="dash__left" style={{ flex: `1 1 ${100 - mapPct}%` }}>
          {/* 좁은 왼쪽 칼럼에서도 와이어프레임처럼 2×2 를 유지한다 */}
          <div className="stats">
            <StatCard
              label="온라인 단말"
              value={devices.online}
              unit={`/ ${total}`}
              percent={total > 0 ? (devices.online / total) * 100 : 0}
              ratio={total > 0 ? devices.online / total : 0}
              // 정상 상태를 세는 카드라 늘 초록 「정상 가동」(문제점 85번 10/12). 끊긴 단말은 오프라인 카드가 알린다.
              badge={total === 0 ? '단말 없음' : '정상 가동'}
              tone={total === 0 ? 'idle' : 'ok'}
              tip="최근 5분 안에 STATUS 를 보낸 단말 수 / 전체 단말 수"
            />
            <StatCard
              label="오프라인 단말"
              value={devices.offline}
              unit="대"
              ratio={total > 0 ? devices.offline / total : 0}
              badge={devices.offline > 0 ? '통신 장애' : '이상 없음'}
              tone={devices.offline > 0 ? 'danger' : 'ok'}
              tip="LWT(연결 끊김)를 받았거나 5분 이상 응답이 없는 단말. 누르면 아래 이상 단말 표로 갑니다."
              action={devices.offline > 0 ? '원인 확인' : undefined}
              onClick={
                devices.offline > 0
                  ? () => document.querySelector('.dash__alerts')?.scrollIntoView({ behavior: 'smooth' })
                  : undefined
              }
            />
            <StatCard
              label="진행 중인 방송"
              value={data.active_broadcasts.length}
              unit="건"
              ratio={broadcasting ? 1 : 0}
              badge={broadcasting ? '송출 중' : '대기 중'}
              tone={broadcasting ? 'warn' : 'idle'}
              tip={broadcasting ? '진행 중인 방송이 있습니다. 방송하기 화면에서 확인·중지합니다.' : '진행 중인 방송 없음'}
            />
            {user?.all_villages && (
              <StatCard
                label="미배정 단말"
                value={devices.unassigned}
                unit="대"
                ratio={total > 0 ? devices.unassigned / total : 0}
                // 없을 때는 회색 「관리 정상」, 생기면 노랑(문제점 85번 10/12)
                badge={devices.unassigned > 0 ? '배정 대기' : '관리 정상'}
                tone={devices.unassigned > 0 ? 'warn' : 'idle'}
                tip="지역 배정을 기다리는 단말. 누르면 단말 관리의 미배정 목록으로 갑니다."
                // 있을 때만 「미배정 확인 ›」 + 눌러서 이동(문제점 101번)
                action={devices.unassigned > 0 ? '미배정 확인' : undefined}
                onClick={devices.unassigned > 0 ? () => navigate('/devices?status=unassigned') : undefined}
              />
            )}
          </div>

          <section className="card dash__list">
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
        <div className="card dash__map" style={{ flex: `1 1 ${mapPct}%` }}>
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
              villages={mapVillages}
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

      {/* ── 아래: 이상 단말 (전체 폭) ── */}
      <AlertTable devices={offline.data} selectedMac={selectedMac} onSelect={setSelectedMac} />
    </div>
  );
}
