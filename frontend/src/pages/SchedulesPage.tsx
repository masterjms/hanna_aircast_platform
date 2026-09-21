/**
 * 스케줄 — 스케줄 설계 2026-09-09, 목업 4.
 *
 * 세 덩어리: 오늘 방송 일정 / 관할 전체 스케줄 목록(조회·수정·삭제·켜기끄기) / 7일 예정표.
 * 오늘 일정과 예정표는 서버가 규칙에서 계산한 회차(occurrences)를 그대로 그린다 —
 * 실행기와 같은 함수라 여기 보이는 것이 곧 나가는 것이다.
 *
 * 보이는 범위는 서버가 정한다(내 범위와 겹치는 스케줄). 고칠 수 있는지는 editable.
 */

import { useCallback, useEffect, useState } from 'react';

import { ApiError, api } from '../api/client';
import type { Occurrence, Schedule } from '../api/types';
import { ScheduleWizard } from '../components/schedule/ScheduleWizard';
import { kstDateKey, kstDayHeader, kstTime, whenLabel } from '../lib/schedule';

const WEEK_DAYS = 7;

function startOfTodayKst(): Date {
  // KST 자정을 UTC Date 로. 브라우저 시간대와 무관하게 한국 날짜로 묶는다.
  const key = new Date().toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' });
  return new Date(`${key}T00:00:00+09:00`);
}

function RunBadge({ s }: { s: Schedule }) {
  if (!s.enabled) return <span className="badge badge--idle">꺼짐</span>;
  const r = s.last_run;
  if (!r) return <span className="dim">아직 없음</span>;
  const when = new Date(r.fire_at).toLocaleString('ko-KR', {
    timeZone: 'Asia/Seoul',
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
  if (r.status === 'started') return <span className="badge badge--ok" title={when}>나감 · {when}</span>;
  if (r.status === 'skipped')
    return (
      <span className="badge badge--warn" title={r.reason ?? undefined}>
        건너뜀 · {when}
      </span>
    );
  if (r.status === 'failed')
    return (
      <span className="badge badge--danger" title={r.reason ?? undefined}>
        실패 · {when}
      </span>
    );
  return <span className="badge badge--idle">처리 중</span>;
}

export function SchedulesPage() {
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [week, setWeek] = useState<Occurrence[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [wizard, setWizard] = useState<{ open: boolean; editing?: Schedule }>({ open: false });

  const fail = (err: unknown, fallback: string) =>
    setError(err instanceof ApiError ? err.message : fallback);

  const load = useCallback(async () => {
    try {
      const from = startOfTodayKst();
      const to = new Date(from.getTime() + WEEK_DAYS * 86_400_000);
      const [list, occ] = await Promise.all([api.schedules.list(), api.schedules.occurrences(from, to)]);
      setSchedules(list);
      setWeek(occ);
      setError(null);
    } catch (err) {
      fail(err, '스케줄을 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const toggle = async (s: Schedule) => {
    try {
      await api.schedules.update(s.id, { enabled: !s.enabled });
      await load();
    } catch (err) {
      fail(err, '변경에 실패했습니다.');
    }
  };

  const remove = async (s: Schedule) => {
    if (!window.confirm(`"${whenLabel(s)} · ${s.target_label}" 스케줄을 삭제할까요?\n앞으로의 회차가 모두 사라집니다. 지난 기록은 남습니다.`))
      return;
    try {
      await api.schedules.remove(s.id);
      await load();
    } catch (err) {
      fail(err, '삭제에 실패했습니다.');
    }
  };

  // 예정표 — 오늘부터 7일, KST 날짜로 묶는다.
  const todayKey = kstDateKey(new Date().toISOString());
  const days = Array.from({ length: WEEK_DAYS }, (_, i) => {
    const d = new Date(startOfTodayKst().getTime() + i * 86_400_000);
    const key = kstDateKey(d.toISOString());
    return { key, date: d, items: week.filter((o) => kstDateKey(o.fire_at) === key) };
  });
  const today = days[0]?.items ?? [];

  return (
    <>
      <div className="page-head page-head--row">
        <div>
          <h1>예약 방송</h1>
          <p>자동방송 규칙 {schedules.length}개 · 시각은 한국 시간입니다.</p>
        </div>
        <button type="button" className="btn btn--primary" onClick={() => setWizard({ open: true })}>
          예약 추가하기
        </button>
      </div>

      {error && (
        <div className="alert" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      <div className="sched-grid">
        <div className="card sched-today">
          <div className="sched-today__title">오늘 방송 일정</div>
          {today.length === 0 ? (
            <div className="dim">오늘은 예정된 방송이 없습니다.</div>
          ) : (
            today.map((o) => (
              <div key={`${o.schedule_id}-${o.fire_at}`} className="sched-today__item">
                <div>{o.target_label}</div>
                <div>
                  <span className="time">{kstTime(o.fire_at)}</span>
                  <span className="dim"> · {o.file_name ?? '파일 없음'}</span>
                </div>
              </div>
            ))
          )}
        </div>

        <div className="table-wrap table-wrap--scroll">
          {loading ? (
            <div className="empty">불러오는 중…</div>
          ) : schedules.length === 0 ? (
            <div className="empty">등록된 예약이 없습니다. 「예약 추가하기」나 「방송하기」 화면에서 만들 수 있습니다.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>언제</th>
                  <th>어디에</th>
                  <th>무엇을</th>
                  <th>다음 실행</th>
                  <th>최근 결과</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {schedules.map((s) => (
                  <tr key={s.id} style={s.enabled ? undefined : { opacity: 0.55 }}>
                    <td className="strong">{whenLabel(s)}</td>
                    <td>{s.target_label}</td>
                    <td>{s.file_name ?? <span className="dim">파일 없음</span>}</td>
                    <td className="dim">
                      {s.next_fire_at
                        ? new Date(s.next_fire_at).toLocaleString('ko-KR', {
                            timeZone: 'Asia/Seoul',
                            month: 'numeric',
                            day: 'numeric',
                            hour: '2-digit',
                            minute: '2-digit',
                          })
                        : '—'}
                    </td>
                    <td>
                      <RunBadge s={s} />
                    </td>
                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                      {s.editable ? (
                        <>
                          <button type="button" className="btn btn--ghost" onClick={() => void toggle(s)}>
                            {s.enabled ? '끄기' : '켜기'}
                          </button>
                          <button
                            type="button"
                            className="btn btn--ghost"
                            onClick={() => setWizard({ open: true, editing: s })}
                          >
                            수정
                          </button>
                          <button type="button" className="btn btn--ghost btn--danger" onClick={() => void remove(s)}>
                            삭제
                          </button>
                        </>
                      ) : (
                        <span className="dim" title="대상 전체가 내 관할이 아니라 볼 수만 있습니다">
                          조회만
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>

      <div className="section-title">방송 예정표</div>
      <div className="table-wrap" style={{ overflowX: 'auto' }}>
        <div className="sched-week">
          {days.map((d) => (
            <div key={d.key} className="sched-week__day">
              <div className={`sched-week__head${d.key === todayKey ? ' sched-week__head--today' : ''}`}>
                {kstDayHeader(d.date)}
              </div>
              <div className="sched-week__body">
                {d.items.length === 0 ? (
                  <span className="dim">예정된 방송이 없습니다.</span>
                ) : (
                  d.items.map((o) => (
                    <div key={`${o.schedule_id}-${o.fire_at}`} className="sched-week__entry">
                      <div>{o.target_label}</div>
                      <div className="dim">
                        {kstTime(o.fire_at)} · {o.file_name ?? ''}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {wizard.open && (
        <ScheduleWizard
          initial={wizard.editing}
          onClose={() => setWizard({ open: false })}
          onSaved={() => void load()}
        />
      )}
    </>
  );
}
