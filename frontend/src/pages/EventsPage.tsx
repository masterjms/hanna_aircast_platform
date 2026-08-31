/**
 * 이력 — 대시보드에 있던 최근 이력을 이 탭으로 옮겼다 (2026-08-31).
 *
 * 지금은 대시보드 요약 API 의 최근 10건을 그대로 보여준다. 필터·페이지네이션·
 * 단말별 응답 상세는 Phase 6 의 전용 API(GET /api/events)와 함께 붙는다.
 */

import { api } from '../api/client';
import { POLL_INTERVAL, usePolling } from '../hooks/usePolling';

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('ko-KR', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function EventsPage() {
  const { data, loading, error } = usePolling(() => api.dashboard.summary(), POLL_INTERVAL.normal);

  if (loading && !data) return <div className="empty">불러오는 중…</div>;
  if (error) return <div className="alert">{error.message}</div>;

  const events = data?.recent_events ?? [];

  return (
    <>
      <div className="table-wrap table-wrap--scroll">
        {events.length === 0 ? (
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
              {events.map((e) => (
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
      <p className="hint" style={{ marginTop: 10 }}>
        최근 10건입니다. 기간 필터·검색·단말별 응답 상세는 Phase 6 에서 확장됩니다.
      </p>
    </>
  );
}
