/**
 * 방송 제어.
 *
 * 파일 방송과 실시간 방송 둘 다 여기서 건다.
 *
 * 실시간은 브라우저 마이크 → WSS /ingest → Icecast → 단말 순으로 흐른다.
 * 마운트는 방송마다 갈라져서(/live/<job_id>) 마을 A 와 B 가
 * 동시에 방송할 수 있다.
 *
 * 대상 단말이 이미 다른 방송에 잡혀 있으면 서버가 409 로 거절한다 —
 * 진행 중인 방송을 자동으로 끊지 않고, 어느 방송과 겹치는지 보여준 뒤
 * 사용자가 판단하게 한다.
 */

import { useCallback, useEffect, useRef, useState } from 'react';

import { ApiError, api } from '../api/client';
import type {
  AudioFile,
  BroadcastDetail,
  BroadcastOverlapDetail,
  Device,
  TargetScope,
  Village,
  Zone,
} from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { getToken } from '../api/client';
import { uplinkBlockedReason, useMicUplink } from '../hooks/useMicUplink';
import { POLL_INTERVAL, usePolling } from '../hooks/usePolling';

/** 대상 대수 표시.
 *
 * 방송은 온라인 단말에만 나간다. 등록 대수만 보여주면 운영자가 "3대에
 * 나가겠구나" 하고 눌렀는데 1대만 나가는 일이 생긴다 — 그래서 둘을 같이 쓰고,
 * 꺼진 단말이 있으면 눈에 띄게 한다.
 */
function DeviceCount({ online, total }: { online: number; total: number }) {
  if (total === 0) return <span className="dim">(단말 없음)</span>;
  if (online === 0) return <span className="count count--none">(전부 오프라인 · {total}대)</span>;
  if (online < total)
    return (
      <span className="count count--partial">
        (온라인 {online}/{total}대)
      </span>
    );
  return <span className="dim">(온라인 {online}대)</span>;
}

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
}

/** 지금 몇 초째인지. 초 단위로만 쓰므로 1초마다 다시 그린다. */
function useElapsedSec(since: string): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  return Math.max(0, Math.floor((now - new Date(since).getTime()) / 1000));
}

/** 진행 중인 방송 한 건. 단말 응답을 성공/실패/대기로 접어 보여준다. */
function ActiveCard({
  broadcast,
  onStop,
  busy,
  readyWaitSec,
}: {
  broadcast: BroadcastDetail;
  onStop: (broadcast: BroadcastDetail) => void;
  busy: boolean;
  /** 이 시간을 넘겨도 준비가 안 된 단말이 있으면 알려준다(설정값). */
  readyWaitSec: number;
}) {
  const isLive = broadcast.event_type.startsWith('LIVE');
  const done = broadcast.results.filter((r) => r.ok === true).length;
  const failed = broadcast.results.filter((r) => r.ok === false).length;
  // results 는 단말당 1행이라 행 수가 곧 대수다. 예전에는 메시지마다 1행이라
  // 단말 1대가 3행이 되면 "1 / 3대"처럼 대수를 틀리게 셌다.
  const total = Math.max(broadcast.expected_count ?? broadcast.target_count, broadcast.results.length);
  const pct = total > 0 ? Math.round(((done + failed) / total) * 100) : 0;

  // 중지를 눌렀지만 아직 단말 응답을 기다리는 중.
  const stopping = broadcast.stop_requested_at !== null && broadcast.ended_at === null;
  const elapsed = useElapsedSec(broadcast.triggered_at);
  // 라이브인데 기준 시간이 지나도 전부 준비되지 않았다 — 그냥 진행할지, 더
  // 기다릴지, 중지할지는 방송하는 사람이 판단할 일이라 알리기만 한다.
  const readyLagging = isLive && !stopping && elapsed >= readyWaitSec && done < total;

  return (
    <div className="active-card">
      <div className="active-card__head">
        <div>
          <div className="active-card__eyebrow">
            <span className="active-card__dot" />
            {isLive ? 'ON AIR · 실시간' : '송출 중 · 파일'}
          </div>
          <div className="active-card__title">
            {broadcast.file_name ?? (isLive ? '실시간 방송' : broadcast.event_type)} ·{' '}
            {broadcast.target_scope}
            {broadcast.target_ids.length > 0 ? ` ${broadcast.target_ids.join(', ')}` : ''}
          </div>
          <div className="dim" style={{ fontSize: 12 }}>
            job_id {broadcast.job_id} · {formatTime(broadcast.triggered_at)} 시작
          </div>
          {isLive && !broadcast.uplink_connected && (
            <div className="hint hint--warn" style={{ marginTop: 6 }}>
              마이크가 연결되지 않아 무음이 나가는 중입니다.
            </div>
          )}
          {stopping && (
            <div className="hint" style={{ marginTop: 6 }}>
              중지를 보냈습니다. 단말의 종료 응답을 기다리는 중입니다 ({done + failed}/{total}대
              응답). 응답이 다 오면 즉시, 늦어지면 설정한 대기 시간 뒤에 종료됩니다.
            </div>
          )}
          {readyLagging && (
            <div className="hint hint--warn" style={{ marginTop: 6 }}>
              {elapsed}초가 지났는데 {total - done}대가 아직 준비되지 않았습니다. 그대로
              방송할지, 더 기다릴지, 중지할지 선택하세요.
            </div>
          )}
          {isLive && broadcast.stream_url && (
            <div className="dim mono" style={{ fontSize: 11, marginTop: 6 }}>
              {broadcast.stream_url}
            </div>
          )}
        </div>
        <button
          type="button"
          className="btn btn--stop"
          onClick={() => onStop(broadcast)}
          disabled={busy || stopping}
        >
          {stopping ? '중지 중…' : '중지'}
        </button>
      </div>

      {/* 막대는 "응답이 온 비율"이다 — 성공(ok=true)과 실패(ok=false)를 함께 센다.
          라이브의 성공은 LIVE_READY ok=true(P4 오디오 준비 완료), 파일은
          FILE_RESULT ok=true(재생까지 끝남)를 뜻한다(통신 사양 §5.4). */}
      <div
        className="progress"
        title={`응답 ${done + failed} / ${total}대 (성공 ${done} · 실패 ${failed})`}
      >
        <span style={{ width: `${pct}%` }} />
      </div>
      <div className="active-card__stats">
        <span>
          {isLive ? '준비 완료' : '완료'} <strong>{done}</strong> / {total}대
        </span>
        {failed > 0 && (
          <span style={{ color: 'var(--danger-text)' }}>
            실패 <strong>{failed}</strong>
          </span>
        )}
      </div>

      {broadcast.results.length > 0 && (
        <ul className="result-list">
          {broadcast.results.map((r) => (
            <li key={r.mac}>
              <span className={`badge badge--${r.ok === true ? 'ok' : r.ok === false ? 'danger' : 'idle'}`}>
                {r.result_type ?? '대기'}
              </span>
              <span className="mono dim">{r.mac}</span>
              <span className="strong">{r.label ?? ''}</span>
              {r.reason && <span className="dim">{r.reason}</span>}
              {r.live === 'RECONNECTING' && (
                <span className="badge badge--warn">🔇 재접속 중 · 무음</span>
              )}
              {r.stats && <span className="dim">{r.stats}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function BroadcastPage() {
  const { user, isSuperAdmin } = useAuth();

  const [scope, setScope] = useState<TargetScope>('village');
  // 마을·단말은 다중 선택이다 — "마을 2곳 동시 방송" 같은 요구를 담는다.
  // villageId(단수)는 구역·단말 목록을 거르는 필터로만 쓴다.
  const [villageIds, setVillageIds] = useState<number[]>([]);
  const [villageId, setVillageId] = useState<number | ''>('');
  const [zoneId, setZoneId] = useState<number | ''>('');
  const [macs, setMacs] = useState<string[]>([]);
  const [fileId, setFileId] = useState<number | ''>('');
  const [storeFlash, setStoreFlash] = useState(false);

  /** 라이브 준비 지연 알림 기준(초). 설정값이고, 못 읽으면 기본 10초. */
  const [readyWaitSec, setReadyWaitSec] = useState(10);

  const [villages, setVillages] = useState<Village[]>([]);
  const [zones, setZones] = useState<Zone[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [files, setFiles] = useState<AudioFile[]>([]);

  const [error, setError] = useState<string | null>(null);
  const [overlap, setOverlap] = useState<BroadcastOverlapDetail | null>(null);
  const [busy, setBusy] = useState(false);

  // 진행 중인 실시간 방송의 event id. 마이크는 이 세션에 물린다.
  const [liveId, setLiveId] = useState<number | null>(null);
  // 단말 flash 녹음. 기본 켬 — 10분을 넘길 긴 방송만 끄면 된다(사양 §11.2).
  const [recordFlash, setRecordFlash] = useState(true);
  const mic = useMicUplink();
  /**
   * 실시간 방송을 시작한 시각(ms). 아래 감시 효과가 "이 시각 이후에 받은
   * 목록"으로만 판단하게 한다 — 시작 직후에는 목록이 아직 새 방송을 모른다.
   */
  const liveStartedAt = useRef(0);
  // 왜 못 쓰는지까지 알려준다 — http 로 열어서인지, 브라우저가 낡아서인지
  // 구분이 안 되면 사용자가 엉뚱한 곳을 고치게 된다.
  const micBlocked = uplinkBlockedReason();

  const active = usePolling(() => api.broadcast.active(), POLL_INTERVAL.broadcasting);

  useEffect(() => {
    void (async () => {
      try {
        const [v, f, d] = await Promise.all([
          api.villages.list(),
          api.files.list(),
          api.devices.list(),
        ]);
        setVillages(v);
        setFiles(f);
        setDevices(d);
        if (v.length === 1) setVillageId(v[0].id);
        if (f.length > 0) setFileId(f[0].id);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : '기본 정보를 불러오지 못했습니다.');
      }
    })();
  }, []);

  useEffect(() => {
    if (villageId === '') {
      setZones([]);
      return;
    }
    void api.villages.zones(villageId).then(setZones).catch(() => setZones([]));
    setZoneId('');
  }, [villageId]);

  const targetIds = (): string[] => {
    if (scope === 'all') return [];
    if (scope === 'village') return villageIds.map(String);
    if (scope === 'zone') return zoneId === '' ? [] : [String(zoneId)];
    return macs;
  };

  const targetReady = scope === 'all' || targetIds().length > 0;
  const ready = fileId !== '' && targetReady;

  const startFile = async () => {
    if (!ready) return;
    setBusy(true);
    setError(null);
    setOverlap(null);
    try {
      await api.broadcast.fileStart({
        file_id: Number(fileId),
        target_scope: scope,
        target_ids: targetIds(),
        store_flash: storeFlash,
        autoplay: true,
      });
      active.reload();
    } catch (err) {
      if (err instanceof ApiError && err.code === 'BROADCAST_OVERLAP') {
        setOverlap(err.detail as unknown as BroadcastOverlapDetail);
        setError(err.message);
      } else {
        setError(err instanceof ApiError ? err.message : '방송을 시작하지 못했습니다.');
      }
    } finally {
      setBusy(false);
    }
  };

  const startLive = async () => {
    if (!targetReady) return;
    setBusy(true);
    setError(null);
    setOverlap(null);
    try {
      // 1) 서버가 세션을 만들고 Icecast 소스를 세운다
      const b = await api.broadcast.liveStart({
        target_scope: scope,
        target_ids: targetIds(),
        record_flash: recordFlash,
      });
      // 아래 감시 효과가 오판하지 않도록 시작 시각을 먼저 찍는다.
      liveStartedAt.current = Date.now();
      setLiveId(b.id);
      active.reload();

      // 2) 마이크를 그 세션에 물린다. 실패하면 방송을 되돌린다 —
      //    소리 없는 방송이 켜진 채로 남으면 안 된다.
      const token = getToken();
      if (b.job_id === null || !token) throw new Error('세션 정보를 받지 못했습니다.');
      await mic.start(b.job_id, token);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'BROADCAST_OVERLAP') {
        setOverlap(err.detail as unknown as BroadcastOverlapDetail);
        setError(err.message);
      } else {
        setError(err instanceof ApiError ? err.message : '실시간 방송을 시작하지 못했습니다.');
      }
    } finally {
      setBusy(false);
    }
  };

  // 설정은 자주 바뀌지 않으니 화면을 열 때 한 번만 읽는다. 실패해도 기본값으로 돈다.
  useEffect(() => {
    void api.config
      .get()
      .then((c) => setReadyWaitSec(c.live_stop_wait_sec))
      .catch(() => undefined);
  }, []);

  const stop = useCallback(
    async (broadcast: BroadcastDetail) => {
      setBusy(true);
      setError(null);
      try {
        if (broadcast.event_type.startsWith('LIVE')) {
          // 마이크를 먼저 끊는다. 서버가 세션을 지운 뒤에 끊으면
          // 업링크가 없는 세션으로 잠깐 남는다.
          mic.stop();
          setLiveId(null);
          await api.broadcast.liveStop(broadcast.id);
        } else {
          await api.broadcast.fileStop(broadcast.id);
        }
        active.reload();
      } catch (err) {
        setError(err instanceof ApiError ? err.message : '중지에 실패했습니다.');
      } finally {
        setBusy(false);
      }
    },
    [active, mic],
  );

  const running = active.data ?? [];

  // 방송이 서버 쪽에서 끝났는데(다른 창에서 중지 등) 마이크가 남아 있으면 정리한다.
  //
  // 시작 시각보다 오래된 목록으로는 판단하지 않는다. setLiveId 는 즉시 반영되는데
  // 목록 갱신은 비동기라, 그 사이에 "없으니 끝났다"고 오판하면 방금 만든 업링크를
  // 핸드셰이크 도중에 끊어버린다(WebSocket 1006).
  useEffect(() => {
    if (liveId === null) return;
    if (active.fetchedAt <= liveStartedAt.current) return;
    if (running.some((b) => b.id === liveId)) return;
    mic.stop();
    setLiveId(null);
  }, [liveId, running, mic, active.fetchedAt]);

  return (
    <>
      <div className="page-head">
        <h1>방송 제어</h1>
        <p>대상을 고르고 실시간 또는 파일로 송출합니다.</p>
      </div>

      {error && (
        <div className="alert" style={{ marginBottom: 16 }}>
          {error}
          {overlap && (
            <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
              {overlap.conflicts.map((c) => (
                <li key={c.id}>
                  job_id {c.job_id} · {c.event_type} — 겹치는 단말 {c.macs.length}대 (
                  {c.macs.slice(0, 3).join(', ')}
                  {c.macs.length > 3 ? ' 외' : ''})
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {running.length > 0 && (
        <section style={{ marginBottom: 22 }}>
          <h2 className="section-title">진행 중인 방송 {running.length}건</h2>
          <div className="active-list">
            {running.map((b) => (
              <ActiveCard
                key={b.id}
                broadcast={b}
                onStop={(x) => void stop(x)}
                busy={busy}
                readyWaitSec={readyWaitSec}
              />
            ))}
          </div>
        </section>
      )}

      <div className="split">
        <section className="card">
          <h2 className="section-title">방송 대상</h2>

          <div className="checks" style={{ marginBottom: 16 }}>
            {(['village', 'zone', 'device', ...(isSuperAdmin ? ['all' as const] : [])] as TargetScope[]).map(
              (s) => (
                <label key={s} className="check">
                  <input
                    type="radio"
                    name="scope"
                    checked={scope === s}
                    onChange={() => setScope(s)}
                  />
                  <span>
                    {s === 'village' ? '마을' : s === 'zone' ? '구역' : s === 'device' ? '개별 단말' : '전체'}
                  </span>
                </label>
              ),
            )}
          </div>

          {scope === 'village' && (
            <div className="field">
              <label>마을 (여러 곳 선택 가능)</label>
              <div className="check-list">
                {villages.map((v) => (
                  <label key={v.id} className="check-list__item">
                    <input
                      type="checkbox"
                      checked={villageIds.includes(v.id)}
                      disabled={v.online_count === 0}
                      onChange={(e) =>
                        setVillageIds((prev) =>
                          e.target.checked ? [...prev, v.id] : prev.filter((x) => x !== v.id),
                        )
                      }
                    />
                    {v.name} <DeviceCount online={v.online_count} total={v.device_count} />
                  </label>
                ))}
              </div>
              {villageIds.length > 1 && (
                <p className="hint">
                  선택한 {villageIds.length}개 마을이 같은 방송을 동시에 받습니다.
                </p>
              )}
            </div>
          )}

          {(scope === 'zone' || scope === 'device') && (
            <div className="field">
              <label htmlFor="b-village">마을</label>
              <select
                id="b-village"
                value={villageId}
                onChange={(e) => setVillageId(e.target.value === '' ? '' : Number(e.target.value))}
              >
                <option value="">선택하세요</option>
                {villages.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name} (온라인 {v.online_count}/{v.device_count}대)
                  </option>
                ))}
              </select>
            </div>
          )}

          {scope === 'zone' && (
            <div className="field">
              <label htmlFor="b-zone">구역</label>
              <select
                id="b-zone"
                value={zoneId}
                onChange={(e) => setZoneId(e.target.value === '' ? '' : Number(e.target.value))}
                disabled={villageId === ''}
              >
                <option value="">선택하세요</option>
                {zones.map((z) => (
                  <option key={z.id} value={z.id}>
                    {z.name} (온라인 {z.online_count}/{z.device_count}대)
                  </option>
                ))}
              </select>
            </div>
          )}

          {scope === 'device' && (
            <div className="field">
              <label>단말 (여러 대 선택 가능)</label>
              <div className="check-list">
                {devices
                  .filter((d) => villageId === '' || d.village_id === villageId)
                  .map((d) => (
                    <label key={d.mac} className="check-list__item">
                      <input
                        type="checkbox"
                        checked={macs.includes(d.mac)}
                        disabled={!d.online}
                        onChange={(e) =>
                          setMacs((prev) =>
                            e.target.checked ? [...prev, d.mac] : prev.filter((m) => m !== d.mac),
                          )
                        }
                      />
                      {d.label ?? d.mac} {d.online ? '' : '(오프라인)'}
                    </label>
                  ))}
              </div>
            </div>
          )}

          {scope === 'all' && (
            <p className="hint hint--warn">
              배정된 전 마을의 온라인 단말에 송출됩니다. 대상이 넓으니 파일을 다시 확인하세요.
            </p>
          )}
        </section>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <section className="card">
            <h2 className="section-title">실시간 방송</h2>

            {micBlocked ? (
              <p className="hint hint--warn">{micBlocked}</p>
            ) : (
              <>
                <div className="mic">
                  <div className="mic__meter" aria-hidden="true">
                    {Array.from({ length: 14 }, (_, i) => (
                      <span
                        key={i}
                        className={mic.level * 14 > i ? 'is-on' : undefined}
                        style={{ height: `${20 + i * 5}%` }}
                      />
                    ))}
                  </div>
                  <div className="mic__state">
                    <span
                      className={`badge badge--${
                        mic.state === 'live' ? 'ok' : mic.state === 'error' ? 'danger' : 'idle'
                      }`}
                    >
                      {mic.state === 'live'
                        ? '마이크 송출 중'
                        : mic.state === 'connecting'
                          ? '연결 중'
                          : mic.state === 'error'
                            ? '오류'
                            : '대기'}
                    </span>
                    {mic.state === 'live' && (
                      <span className="dim num">{Math.round(mic.bytesSent / 1024)} KB 전송</span>
                    )}
                  </div>
                </div>

                {mic.error && (
                  <div className="alert" style={{ marginTop: 12 }}>
                    {mic.error}
                  </div>
                )}

                <label className="check-list__item" style={{ marginBottom: 8 }}>
                  <input
                    type="checkbox"
                    checked={recordFlash}
                    onChange={(e) => setRecordFlash(e.target.checked)}
                    disabled={liveId !== null}
                  />
                  단말에 녹음 저장
                  <span className="dim" style={{ fontSize: 12 }}>
                    (10분 넘길 방송은 끄세요)
                  </span>
                </label>
                <button
                  type="button"
                  className="btn btn--primary btn--block"
                  style={{ marginTop: 16 }}
                  onClick={() => void startLive()}
                  disabled={busy || !targetReady || liveId !== null}
                >
                  {liveId !== null ? '방송 중' : busy ? '연결 중…' : '실시간 방송 시작'}
                </button>

                <p className="hint">
                  마이크 권한을 허용해야 시작됩니다. 단말은 방송마다 다른 주소
                  (/live/&lt;방송번호&gt;)로 붙으므로, 내용이 다른 방송을 마을끼리 동시에
                  내보낼 수 있습니다.
                </p>
              </>
            )}
          </section>

          <section className="card">
            <h2 className="section-title">파일 방송</h2>

          <div className="field">
            <label htmlFor="b-file">파일</label>
            <select
              id="b-file"
              value={fileId}
              onChange={(e) => setFileId(e.target.value === '' ? '' : Number(e.target.value))}
            >
              <option value="">선택하세요</option>
              {files.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.filename}
                </option>
              ))}
            </select>
            {files.length === 0 && <p className="hint">파일함에 먼저 mp3 를 올려주세요.</p>}
          </div>

          <label className="check" style={{ marginBottom: 16 }}>
            <input
              type="checkbox"
              checked={storeFlash}
              onChange={(e) => setStoreFlash(e.target.checked)}
            />
            <span>단말에 저장 (반복 재생용)</span>
          </label>

          <button
            type="button"
            className="btn btn--primary btn--block"
            onClick={() => void startFile()}
            disabled={busy || !ready}
          >
            {busy ? '전송 중…' : '파일 방송 시작'}
          </button>

            <p className="hint">
              단말은 MQTT 로 명령을 받고 서버에서 직접 파일을 내려받습니다. 다운로드가 끝나면
              자동 재생됩니다.
            </p>
          </section>
        </div>
      </div>

      {user && !isSuperAdmin && (
        <p className="hint" style={{ marginTop: 16 }}>
          담당 마을 밖 단말에는 방송할 수 없습니다.
        </p>
      )}
    </>
  );
}
