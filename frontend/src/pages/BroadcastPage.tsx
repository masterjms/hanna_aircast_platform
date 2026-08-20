/**
 * 방송 제어.
 *
 * 파일 방송과 실시간 방송 둘 다 여기서 건다.
 *
 * 실시간은 브라우저 마이크 → WSS /ingest → Icecast → 단말 순으로 흐른다.
 * 마운트는 세션마다 갈라져서(/live/<마을8자리>/<세션id>) 마을 A 와 B 가
 * 동시에 방송할 수 있다.
 *
 * 대상 단말이 이미 다른 방송에 잡혀 있으면 서버가 409 로 거절한다 —
 * 진행 중인 방송을 자동으로 끊지 않고, 어느 방송과 겹치는지 보여준 뒤
 * 사용자가 판단하게 한다.
 */

import { useCallback, useEffect, useState } from 'react';

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
import { isUplinkSupported, useMicUplink } from '../hooks/useMicUplink';
import { POLL_INTERVAL, usePolling } from '../hooks/usePolling';

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' });
}

/** 진행 중인 방송 한 건. 단말 응답을 성공/실패/대기로 접어 보여준다. */
function ActiveCard({
  broadcast,
  onStop,
  busy,
}: {
  broadcast: BroadcastDetail;
  onStop: (broadcast: BroadcastDetail) => void;
  busy: boolean;
}) {
  const isLive = broadcast.event_type.startsWith('LIVE');
  const done = broadcast.results.filter((r) => r.ok === true).length;
  const failed = broadcast.results.filter((r) => r.ok === false).length;
  const total = Math.max(broadcast.target_count, broadcast.results.length);
  const pct = total > 0 ? Math.round(((done + failed) / total) * 100) : 0;

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
            {broadcast.target_id ? ` ${broadcast.target_id}` : ''}
          </div>
          <div className="dim" style={{ fontSize: 12 }}>
            job_id {broadcast.job_id} · {formatTime(broadcast.triggered_at)} 시작
          </div>
          {isLive && !broadcast.uplink_connected && (
            <div className="hint hint--warn" style={{ marginTop: 6 }}>
              마이크가 연결되지 않아 무음이 나가는 중입니다.
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
          disabled={busy}
        >
          중지
        </button>
      </div>

      <div className="progress">
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
  const [villageId, setVillageId] = useState<number | ''>('');
  const [zoneId, setZoneId] = useState<number | ''>('');
  const [mac, setMac] = useState('');
  const [fileId, setFileId] = useState<number | ''>('');
  const [storeFlash, setStoreFlash] = useState(false);

  const [villages, setVillages] = useState<Village[]>([]);
  const [zones, setZones] = useState<Zone[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [files, setFiles] = useState<AudioFile[]>([]);

  const [error, setError] = useState<string | null>(null);
  const [overlap, setOverlap] = useState<BroadcastOverlapDetail | null>(null);
  const [busy, setBusy] = useState(false);

  // 진행 중인 실시간 방송의 event id. 마이크는 이 세션에 물린다.
  const [liveId, setLiveId] = useState<number | null>(null);
  const mic = useMicUplink();
  const micSupported = isUplinkSupported();

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

  const targetId = (): string | null => {
    if (scope === 'all') return null;
    if (scope === 'village') return villageId === '' ? null : String(villageId);
    if (scope === 'zone') return zoneId === '' ? null : String(zoneId);
    return mac || null;
  };

  const targetReady = scope === 'all' || targetId() !== null;
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
        target_id: targetId(),
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
        target_id: targetId(),
      });
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
  useEffect(() => {
    if (liveId !== null && !running.some((b) => b.id === liveId)) {
      mic.stop();
      setLiveId(null);
    }
  }, [liveId, running, mic]);

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
              <ActiveCard key={b.id} broadcast={b} onStop={(x) => void stop(x)} busy={busy} />
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

          {scope !== 'all' && (
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
                    {v.name} ({v.device_count}대)
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
                    {z.name} ({z.device_count}대)
                  </option>
                ))}
              </select>
            </div>
          )}

          {scope === 'device' && (
            <div className="field">
              <label htmlFor="b-mac">단말</label>
              <select id="b-mac" value={mac} onChange={(e) => setMac(e.target.value)}>
                <option value="">선택하세요</option>
                {devices
                  .filter((d) => villageId === '' || d.village_id === villageId)
                  .map((d) => (
                    <option key={d.mac} value={d.mac} disabled={!d.online}>
                      {d.label ?? d.mac} {d.online ? '' : '(오프라인)'}
                    </option>
                  ))}
              </select>
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

            {!micSupported ? (
              <p className="hint hint--warn">
                이 브라우저에서는 마이크를 사용할 수 없습니다. 최신 브라우저에서 열어 주세요.
              </p>
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
                  마이크 권한을 허용해야 시작됩니다. 단말은 세션 전용 주소
                  (/live/&lt;마을&gt;/&lt;세션&gt;)로 붙어서 마을끼리 동시에 방송할 수 있습니다.
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
