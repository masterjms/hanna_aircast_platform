/**
 * 관리 기록 · 기록 점검 (문제점 70번 10/15 ①④, 2026-10-17) — 계정 관리 화면 아래.
 *
 * AuditHistory   관리 행위 기록(계정·권한·단말·설정·OTA·방송 변경 — 누가·언제·무엇을·결과).
 *                최고 관리자는 전부, 그 외는 자기 것만.
 * RecordCheck    최고 관리자 전용. 접속기록·감사 기록의 해시 체인을 다시 계산하고(무결성 확인),
 *                월 점검을 남기고(끝 해시를 앵커로 기록), 한 달치 CSV 를 내려받는다(파일 해시 표시).
 */

import { useEffect, useState } from 'react';

import { ApiError, api } from '../api/client';
import type { AuditEventPage, VerifyResult } from '../api/types';
import { ROLE_LABEL } from '../lib/roles';
import { PageSizeSelect, PagerBar, type PageSize } from './Pager';

const fmt = (iso: string | null) => (iso ? new Date(iso).toLocaleString('ko-KR', { hour12: false }) : '—');

/** 요청 내용 요약 한 줄 — 비밀값은 서버가 이미 「***」로 지웠다. */
function detailText(d: Record<string, unknown> | null): string {
  if (!d) return '';
  const body = d.body as Record<string, unknown> | undefined;
  const parts: string[] = [];
  if (body && typeof body === 'object') {
    for (const [k, v] of Object.entries(body)) {
      if (v === null || v === undefined || v === '***') continue;
      parts.push(`${k}=${typeof v === 'object' ? JSON.stringify(v) : String(v)}`);
    }
  }
  if (typeof d.upload_bytes === 'number') parts.push(`파일 ${Math.round(d.upload_bytes / 1024)} KB`);
  if (typeof d.query === 'string') parts.push(d.query);
  return parts.join(' · ');
}

export function AuditHistory() {
  const [page, setPage] = useState(1);
  const [size, setSize] = useState<PageSize>(10);
  const [q, setQ] = useState('');
  const [data, setData] = useState<AuditEventPage | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    api.audit
      .list({ page, size, q: q.trim() || undefined })
      .then((d) => alive && (setData(d), setError(null)))
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : '관리 기록을 불러오지 못했습니다.'));
    return () => {
      alive = false;
    };
  }, [page, size, q]);
  const pages = Math.max(1, Math.ceil((data?.total ?? 0) / size));
  return (
    <section style={{ marginTop: 28 }}>
      <div className="filters">
        <h2 className="section-title" style={{ margin: 0 }}>
          관리 기록
        </h2>
        <div className="filters__spacer" />
        <input
          type="search"
          placeholder="아이디 · 행위 · 대상 · IP"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setPage(1);
          }}
          style={{ minWidth: 220 }}
          aria-label="관리 기록 검색"
        />
        <PageSizeSelect
          value={size}
          onChange={(s) => {
            setSize(s);
            setPage(1);
          }}
        />
      </div>
      {error && <div className="alert">{error}</div>}
      <div className="table-wrap table-wrap--scroll">
        {!data ? (
          <div className="empty">불러오는 중…</div>
        ) : data.items.length === 0 ? (
          <div className="empty">기록이 없습니다.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>시각</th>
                <th>아이디</th>
                <th>행위</th>
                <th>대상</th>
                <th>결과</th>
                <th>IP</th>
                <th>내용</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((e) => {
                const ok = e.status < 400;
                const text = detailText(e.detail);
                return (
                  <tr key={e.id}>
                    <td className="dim">{fmt(e.at)}</td>
                    <td>
                      <span className="strong">{e.username ?? '—'}</span>
                      {e.role && <span className="dim"> {ROLE_LABEL[e.role as keyof typeof ROLE_LABEL] ?? e.role}</span>}
                    </td>
                    <td title={`${e.method} ${e.path}`}>{e.action}</td>
                    <td>{e.target ?? '—'}</td>
                    <td>
                      <span className={`badge ${ok ? 'badge--ok' : e.status === 403 ? 'badge--warn' : 'badge--danger'}`}>
                        {ok ? '완료' : e.status === 403 ? '권한 없음' : `실패 ${e.status}`}
                      </span>
                    </td>
                    <td>{e.ip ?? '—'}</td>
                    <td className="dim" title={text || undefined}>
                      {text ? (text.length > 60 ? `${text.slice(0, 60)}…` : text) : '—'}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
      <PagerBar total={data?.total ?? 0} page={page} pages={pages} onPage={setPage} />
      <p className="hint" style={{ marginTop: 6 }}>
        계정·권한·지역·단말·환경 설정·OTA·방송 자료·예약·방송의 변경을 실패한 것까지 남깁니다. 비밀번호는 남기지 않습니다. 2년
        보관하며, 고치거나 지울 수 없습니다(1년이 지난 기록만 보관 기간 정리로 지워짐).
      </p>
    </section>
  );
}

/** 지난달(한국 시간) YYYY-MM — 월 점검은 보통 지난달 기록을 내려받는다. */
function lastMonth(): string {
  const now = new Date(Date.now() + 9 * 3600_000);
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - 1, 1));
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}`;
}

export function RecordCheck() {
  const [result, setResult] = useState<VerifyResult | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [month, setMonth] = useState(lastMonth);
  const [saved, setSaved] = useState<{ name: string; sha256: string }[]>([]);

  const run = async (kind: 'verify' | 'review') => {
    setBusy(kind);
    setError(null);
    try {
      setResult(kind === 'verify' ? await api.audit.verify() : await api.audit.review());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '확인하지 못했습니다.');
    } finally {
      setBusy(null);
    }
  };
  useEffect(() => {
    void run('verify');
  }, []);

  const download = async (kind: 'login' | 'audit') => {
    setBusy(kind);
    setError(null);
    try {
      const f = await api.audit.exportCsv(kind, month);
      const url = URL.createObjectURL(f.blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = f.filename;
      a.click();
      URL.revokeObjectURL(url);
      setSaved((s) => [{ name: f.filename, sha256: f.sha256 }, ...s.filter((x) => x.name !== f.filename)]);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '내려받지 못했습니다.');
    } finally {
      setBusy(null);
    }
  };

  return (
    <section className="card record-check" style={{ marginTop: 28 }}>
      <div className="filters" style={{ marginBottom: 10 }}>
        <h2 className="section-title" style={{ margin: 0 }}>
          기록 점검
        </h2>
        {result && (
          <span className={`badge ${result.ok ? 'badge--ok' : 'badge--danger'}`}>
            {result.ok ? '위·변조 없음' : '어긋난 기록 있음'}
          </span>
        )}
        <div className="filters__spacer" />
        <button type="button" className="btn btn--sm" onClick={() => void run('verify')} disabled={busy !== null}>
          {busy === 'verify' ? '확인 중…' : '무결성 확인'}
        </button>
        <button type="button" className="btn btn--sm btn--primary" onClick={() => void run('review')} disabled={busy !== null}>
          {busy === 'review' ? '기록 중…' : '이번 달 점검 기록'}
        </button>
      </div>
      {error && <div className="alert">{error}</div>}
      {result && (
        <div className="record-check__rows">
          {result.chains.map((c) => (
            <div key={c.table} className="record-check__row">
              <span className="strong">{c.label}</span>
              <span className="dim">{c.rows.toLocaleString('ko-KR')}줄</span>
              {c.ok ? (
                <span className="badge badge--ok">해시 이어짐</span>
              ) : (
                <span className="badge badge--danger">
                  {c.first_bad_seq !== null && `${c.first_bad_seq}번째 줄부터 어긋남`}
                  {c.unchained > 0 && ` · 체인 밖 ${c.unchained}줄`}
                  {c.anchor_mismatch.length > 0 && ` · 지난 점검과 다름(${c.anchor_mismatch.join(', ')})`}
                </span>
              )}
              {c.last_hash && (
                <span className="mono dim" title={c.last_hash}>
                  끝 해시 {c.last_hash.slice(0, 12)}…
                </span>
              )}
            </div>
          ))}
          <p className="hint" style={{ margin: '6px 0 0' }}>
            마지막 점검: {result.last_review_at ? `${fmt(result.last_review_at)} · ${result.last_review_by ?? ''}` : '없음'}. 「이번 달
            점검 기록」은 확인 결과와 끝 해시를 관리 기록에 남깁니다 — 나중에 기록을 통째로 다시 계산해 덮어써도 그 자리에서
            드러납니다.
          </p>
        </div>
      )}
      <div className="filters" style={{ marginTop: 14, marginBottom: 0 }}>
        <span className="dim">월별 내려받기(엑셀)</span>
        <input type="month" value={month} onChange={(e) => setMonth(e.target.value)} aria-label="내려받을 달" />
        <button type="button" className="btn btn--sm" onClick={() => void download('login')} disabled={busy !== null || !month}>
          접속기록
        </button>
        <button type="button" className="btn btn--sm" onClick={() => void download('audit')} disabled={busy !== null || !month}>
          관리 기록
        </button>
      </div>
      {saved.length > 0 && (
        <ul className="record-check__files">
          {saved.map((f) => (
            <li key={f.name}>
              {f.name} <span className="mono dim">SHA-256 {f.sha256}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
