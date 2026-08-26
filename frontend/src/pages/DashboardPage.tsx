/**
 * 대시보드.
 *
 * 진행 중인 방송이 있으면 폴링을 2초로 당긴다(사양: 기본 5초, 방송 중 2초).
 * 지도는 Phase 2 후반에 카카오맵을 붙인다 — 지금은 요약·이상 목록·이력만 보여준다.
 */

import { useEffect, useState } from 'react';

import { api } from '../api/client';
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

  // 기본 주기로 시작해서, 진행 중인 방송이 잡히면 다음 주기부터 당긴다.
  const [fastMode, setFastMode] = useState(false);
  const { data, loading } = usePolling(
    () => api.dashboard.summary(),
    fastMode ? POLL_INTERVAL.broadcasting : POLL_INTERVAL.normal,
  );

  const broadcasting = (data?.active_broadcasts.length ?? 0) > 0;
  useEffect(() => setFastMode(broadcasting), [broadcasting]);

  if (loading && !data) {
    return <div className="empty">불러오는 중…</div>;
  }
  if (!data) {
    return <div className="alert">대시보드를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요.</div>;
  }

  const { devices, alerts, active_broadcasts, recent_events } = data;
  const total = devices.total + devices.unassigned;

  return (
    <>
      <div className="page-head">
        <h1>전체 개요</h1>
        <p>
          {user?.all_villages ? `전체 ${user.villages.length}개 마을` : '담당 마을'} ·{' '}
          {POLL_INTERVAL.normal / 1000}초마다 갱신
          {broadcasting && ' (방송 중 2초)'}
        </p>
      </div>

      <div className="tiles">
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
          value={active_broadcasts.length}
          unit="건"
          note={broadcasting ? '진행 중인 세션이 있습니다' : '진행 중인 방송 없음'}
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

      <section style={{ marginBottom: 22 }}>
        <h2 className="section-title">진행 중인 방송</h2>
        <div className="table-wrap table-wrap--scroll">
          {active_broadcasts.length === 0 ? (
            <div className="empty">진행 중인 방송이 없습니다.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>종류</th>
                  <th>대상</th>
                  <th className="mono">job_id</th>
                  <th>시작</th>
                </tr>
              </thead>
              <tbody>
                {active_broadcasts.map((b) => (
                  <tr key={b.id}>
                    <td>
                      <span className="badge badge--warn">{b.event_type}</span>
                    </td>
                    <td className="strong">
                      {b.target_scope}
                      {b.target_ids.length > 0 ? ` · ${b.target_ids.join(', ')}` : ''}
                    </td>
                    <td className="mono">{b.job_id ?? '—'}</td>
                    <td className="dim">{formatTime(b.triggered_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      <section style={{ marginBottom: 22 }}>
        <h2 className="section-title">
          이상 상태{' '}
          {alerts.length > 0 && (
            <span style={{ color: 'var(--danger-text)', fontSize: 12 }}>{alerts.length}건</span>
          )}
        </h2>
        <div className="table-wrap table-wrap--scroll">
          {alerts.length === 0 ? (
            <div className="empty">모든 단말이 정상입니다.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th className="mono">MAC</th>
                  <th>별칭</th>
                  <th>마을</th>
                  <th>상태</th>
                  <th>마지막 통신</th>
                </tr>
              </thead>
              <tbody>
                {alerts.map((a) => (
                  <tr key={a.mac}>
                    <td className="mono">{a.mac}</td>
                    <td className="strong">{a.label ?? '—'}</td>
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

      <section>
        <h2 className="section-title">최근 이력</h2>
        <div className="table-wrap table-wrap--scroll">
          {recent_events.length === 0 ? (
            <div className="empty">아직 방송 이력이 없습니다.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>종류</th>
                  <th>대상</th>
                  <th>시작</th>
                  <th>종료</th>
                </tr>
              </thead>
              <tbody>
                {recent_events.map((e) => (
                  <tr key={e.id}>
                    <td className="mono">{e.event_type}</td>
                    <td className="strong">
                      {e.target_scope}
                      {e.target_ids.length > 0 ? ` · ${e.target_ids.join(', ')}` : ''}
                    </td>
                    <td className="dim">{formatTime(e.triggered_at)}</td>
                    <td className="dim">{e.ended_at ? formatTime(e.ended_at) : '진행 중'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </>
  );
}
