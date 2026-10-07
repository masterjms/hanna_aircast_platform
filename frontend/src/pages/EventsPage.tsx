/**
 * 방송 기록 — 단말별 한 줄 (문제점 50번, 2026-10-04).
 *
 * 예전에는 대시보드 요약의 최근 10건(방송 단위)을 그대로 보여 줬다. 현장은 **단말마다** 그 방송을
 * 받았는지, 못 받았으면 왜인지(오프라인·응답 없음·실패)를 보고 싶어 한다. 그래서 행 = (방송 × 단말),
 * 10·20·50건씩 넘기고, 종류·기간·검색으로 거른다. 판정 규칙은 서버(history.service.judge)가
 * 방송 제어 화면과 같은 함수로 정한다 — 두 화면이 다른 말을 하지 않는다.
 *
 * 2026-10-04 이전 방송은 단말 스냅숏이 없어 응답한 단말만 줄이 된다(오프라인이던 단말은 알 수 없다).
 */

import { useEffect, useMemo, useState } from 'react';

import { ApiError, api } from '../api/client';
import type { HistoryKind, HistoryPage, HistoryVerdict } from '../api/types';

const SIZES = [10, 20, 50] as const;
type Size = (typeof SIZES)[number];

const KIND_OPTIONS: { value: HistoryKind | ''; label: string }[] = [
  { value: '', label: '모든 종류' },
  { value: 'file', label: '파일 방송' },
  { value: 'live', label: '실시간 방송' },
  { value: 'schedule', label: '예약 방송' },
  { value: 'ota', label: 'OTA' },
];

const VERDICT_CLASS: Record<HistoryVerdict, string> = {
  정상: 'badge badge--ok',
  실패: 'badge badge--danger',
  '응답 없음': 'badge badge--warn',
  오프라인: 'badge badge--idle',
  '진행 중': 'badge badge--idle',
  응답: 'badge badge--idle',
};

function fmt(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('ko-KR', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** 같은 날이면 종료는 시각만 — 한 줄에 날짜가 두 번 오면 읽기 힘들다. */
function fmtEnd(start: string, end: string | null): string {
  if (!end) return '진행 중';
  const s = new Date(start);
  const e = new Date(end);
  if (s.toDateString() === e.toDateString()) {
    return e.toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
  }
  return fmt(end);
}

function kstDateInput(offsetDays: number): string {
  const d = new Date(Date.now() + 9 * 3600_000 + offsetDays * 86_400_000);
  return d.toISOString().slice(0, 10);
}

export function EventsPage() {
  const [page, setPage] = useState(1);
  const [size, setSize] = useState<Size>(10);
  const [kind, setKind] = useState<HistoryKind | ''>('');
  const [q, setQ] = useState('');
  // 기본 최근 7일(병목 감사 H6). 비우면 서버가 최근 31일로 본다 — 5개월치를 한 번에 훑지 않는다.
  const [from, setFrom] = useState(() => kstDateInput(-6));
  const [to, setTo] = useState(() => kstDateInput(0));
  const [data, setData] = useState<HistoryPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  // 검색어는 치는 동안 매번 묻지 않는다 — 잠깐 멈추면 묻는다.
  const [debouncedQ, setDebouncedQ] = useState('');
  useEffect(() => {
    const t = window.setTimeout(() => setDebouncedQ(q.trim()), 300);
    return () => window.clearTimeout(t);
  }, [q]);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    api.events
      .list({ page, size, kind, q: debouncedQ, from, to })
      .then((d) => {
        if (!alive) return;
        setData(d);
        setError(null);
      })
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : '기록을 불러오지 못했습니다.'))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [page, size, kind, debouncedQ, from, to]);

  // 필터가 바뀌면 1쪽으로
  useEffect(() => setPage(1), [size, kind, debouncedQ, from, to]);

  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / size));
  const items = data?.items ?? [];

  // 같은 방송의 줄은 묶어 보이게 — 첫 줄에만 종류·소스·시각을 쓰고 아래 줄은 비운다.
  const grouped = useMemo(() => {
    let prev: number | null = null;
    return items.map((r) => {
      const first = r.event_id !== prev;
      prev = r.event_id;
      return { row: r, first };
    });
  }, [items]);

  return (
    <>
      <div className="filters">
        <select value={kind} onChange={(e) => setKind(e.target.value as HistoryKind | '')} aria-label="종류">
          {KIND_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <input type="date" value={from} max={to || undefined} onChange={(e) => setFrom(e.target.value)} aria-label="시작일" />
        <span className="dim">~</span>
        <input type="date" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} aria-label="종료일" />
        <button type="button" className="btn btn--sm" onClick={() => (setFrom(kstDateInput(0)), setTo(kstDateInput(0)))}>
          오늘
        </button>
        <button type="button" className="btn btn--sm" onClick={() => (setFrom(kstDateInput(-6)), setTo(kstDateInput(0)))}>
          7일
        </button>
        {(from || to) && (
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            title="기간을 비우면 최근 31일을 봅니다"
            onClick={() => (setFrom(''), setTo(''))}
          >
            기간 지우기 (최근 31일)
          </button>
        )}
        <div className="filters__spacer" />
        <input
          type="search"
          placeholder="단말 별칭 · MAC · 마을 · 파일 이름"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          style={{ minWidth: 260 }}
          aria-label="검색"
        />
        <select value={size} onChange={(e) => setSize(Number(e.target.value) as Size)} aria-label="한 쪽에 보일 줄 수">
          {SIZES.map((s) => (
            <option key={s} value={s}>
              {s}건씩
            </option>
          ))}
        </select>
      </div>

      {error && <div className="alert" style={{ marginBottom: 14 }}>{error}</div>}

      <div className="table-wrap table-wrap--scroll">
        {loading && !data ? (
          <div className="empty">불러오는 중…</div>
        ) : items.length === 0 ? (
          <div className="empty">조건에 맞는 기록이 없습니다.</div>
        ) : (
          <table className="history">
            <thead>
              <tr>
                <th>종류</th>
                <th>소스</th>
                <th>시작</th>
                <th>종료</th>
                <th>대상 단말</th>
                <th>마을</th>
                <th>판정</th>
                <th>응답</th>
              </tr>
            </thead>
            <tbody>
              {grouped.map(({ row: r, first }) => (
                <tr key={`${r.event_id}:${r.mac}`} className={first ? 'history__first' : 'history__more'}>
                  <td>{first ? <span className={`badge badge--idle badge--plain history__kind history__kind--${r.kind}`}>{r.kind_label}</span> : ''}</td>
                  <td className="strong">{first ? r.source ?? '—' : ''}</td>
                  <td className="dim">{first ? fmt(r.started_at) : ''}</td>
                  <td className="dim">{first ? fmtEnd(r.started_at, r.ended_at) : ''}</td>
                  <td>
                    <span className="strong">{r.label || <span className="mono dim">{r.mac}</span>}</span>
                    {r.label && <span className="mono dim history__mac"> {r.mac}</span>}
                  </td>
                  <td>{r.village_name ?? '—'}</td>
                  <td>
                    <span className={VERDICT_CLASS[r.verdict] ?? 'badge badge--idle'} title={r.reason ?? undefined}>
                      {r.verdict}
                      {r.verdict === '실패' && r.reason ? ` · ${r.reason}` : ''}
                    </span>
                  </td>
                  <td className="dim" title={r.result_type ?? undefined}>
                    {r.responded_at ? fmtEnd(r.started_at, r.responded_at) : r.verdict === '오프라인' ? '미발송' : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="pager">
        <span className="dim">
          총 {data?.total ?? 0}건 · {page}/{pages}쪽
        </span>
        <span className="filters__spacer" />
        <button type="button" className="btn btn--sm" disabled={page <= 1} onClick={() => setPage(1)}>
          처음
        </button>
        <button type="button" className="btn btn--sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
          이전
        </button>
        <button type="button" className="btn btn--sm" disabled={page >= pages} onClick={() => setPage((p) => p + 1)}>
          다음
        </button>
        <button type="button" className="btn btn--sm" disabled={page >= pages} onClick={() => setPage(pages)}>
          끝
        </button>
      </div>
      <p className="hint" style={{ marginTop: 8 }}>
        판정: <b>정상</b> 단말이 받아 처리함 · <b>실패</b> 단말이 거절(사유 표시) · <b>응답 없음</b> 보냈지만 답이 없음 ·{' '}
        <b>오프라인</b> 방송 당시 꺼져 있어 보내지 않음. 기록은 {`5개월`}간 보관됩니다.
      </p>
    </>
  );
}
