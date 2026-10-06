/**
 * OTA 관리 — 최고 관리자 (문제점 48번, 2026-10-04).
 *
 * 세 덩어리: ① 패키지(IOT_RADIO.pkg 올리기·목록) ② 배포(패키지 + 대상 하나 → OTA_START)
 * ③ 진행·이력(단말별 진행·결과·재부팅 뒤 적용 확인).
 *
 * 대상은 **마을 하나 또는 단말 하나**(문제점 48번). 고르는 창은 방송하기·단말 배정과 같은 지역 트리
 * (TargetTreePicker, 하나만 고르는 모드). 단말 계약(현행 02 §10): 서버는 OTA_START 만 보내고 단말이
 * 다운로드·검증·적용·재부팅을 알아서 한다.
 *
 * 성공의 기준(단말 쪽 정의, 문제점 48번 보조설명 2026-10-04): 단말이 패키지를 **끝까지 받아가면**
 * 성공이다. 실제 펌웨어는 다 받으면 바로 네트워크를 끊고 재부팅하므로 OTA_RESULT 가 오지 않는다.
 * 서버가 마지막 바이트가 나간 것을 보고(OTA_DOWNLOADED) 그 단말을 성공·오프라인으로 돌린다.
 * 몇 분 안에 다시 붙은 뒤 보고하는 p4_fw/c6_fw 가 패키지 버전과 같으면 「적용 확인」 — 이건 덤이고
 * 최종 확인은 사람이 한다.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';

import { ApiError, api } from '../api/client';
import type { Device, Organization, OtaJob, OtaPackage, Village } from '../api/types';
import { StepTitle } from '../components/StepTitle';
import { TargetTreePicker, type PickMode } from '../components/broadcast/TargetTreePicker';
import { POLL_INTERVAL, usePolling } from '../hooks/usePolling';

function fmt(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('ko-KR', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function mb(bytes: number): string {
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
}

/** 단말 한 대의 OTA 상태 글자. 재부팅 뒤 버전 확인이 가장 세고, 그다음이 「다 받아감」(성공). */
function deviceState(d: OtaJob['devices'][number], ended: boolean): { text: string; cls: string } {
  if (d.applied) return { text: '적용 확인 (재부팅 뒤 버전 일치)', cls: 'badge badge--ok' };
  if (!d.sent) return { text: '오프라인 · 안 보냄', cls: 'badge badge--idle' };
  if (d.downloaded) {
    // 다 받아갔다 = 성공. 다시 붙었는데 버전 글자가 다르면 패키지의 「버전」을 잘못 적었거나 롤백.
    if (d.online) return { text: '성공 · 다시 붙음 (버전 글자 다름)', cls: 'badge badge--warn' };
    return { text: '성공 · 다 받음 → 재부팅 중', cls: 'badge badge--ok' };
  }
  if (d.ok === false) return { text: `실패${d.reason ? ` · ${d.reason}` : ''}`, cls: 'badge badge--danger' };
  if (d.progress) return { text: d.progress, cls: 'badge badge--warn badge--plain' };
  return { text: ended ? '응답 없음' : '대기 중', cls: ended ? 'badge badge--danger' : 'badge badge--idle' };
}

export function OtaPage() {
  const [packages, setPackages] = useState<OtaPackage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadPackages = useCallback(() => {
    api.ota
      .packages()
      .then(setPackages)
      .catch((e) => setError(e instanceof ApiError ? e.message : '패키지 목록을 불러오지 못했습니다.'));
  }, []);
  useEffect(loadPackages, [loadPackages]);

  // ── 업로드 ──
  const [file, setFile] = useState<File | null>(null);
  const [version, setVersion] = useState('');
  const [pkgVersion, setPkgVersion] = useState('');
  const [note, setNote] = useState('');
  const [uploading, setUploading] = useState(false);

  const upload = async () => {
    if (!file) return;
    setUploading(true);
    setError(null);
    try {
      const pkg = await api.ota.upload(file, { version: version.trim(), pkg_version: Number(pkgVersion), note: note.trim() || undefined });
      setNotice(`패키지 #${pkg.id} ${pkg.filename} (${pkg.version}) 을 올렸습니다.`);
      setFile(null);
      setVersion('');
      setPkgVersion('');
      setNote('');
      loadPackages();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '업로드에 실패했습니다.');
    } finally {
      setUploading(false);
    }
  };

  const removePackage = async (p: OtaPackage) => {
    if (!window.confirm(`패키지 "${p.filename} (${p.version})" 을 지울까요?\n끝난 OTA 기록에는 이름이 남습니다.`)) return;
    try {
      await api.ota.removePackage(p.id);
      loadPackages();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '삭제에 실패했습니다.');
    }
  };

  // ── 배포 ──
  const [packageId, setPackageId] = useState<number | ''>('');
  const [mode, setMode] = useState<PickMode>('village');
  const [picked, setPicked] = useState<string[]>([]);
  const [villages, setVillages] = useState<Village[]>([]);
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [starting, setStarting] = useState(false);
  useEffect(() => {
    void Promise.all([api.villages.list(), api.organizations.list().catch(() => [] as Organization[]), api.devices.list()])
      .then(([v, o, d]) => {
        setVillages(v);
        setOrgs(o);
        setDevices(d);
      })
      .catch(() => undefined);
  }, []);
  useEffect(() => setPicked([]), [mode]);

  const pkg = packages.find((p) => p.id === packageId);
  const targetName = useMemo(() => {
    const id = picked[0];
    if (!id) return null;
    if (mode === 'village') return villages.find((v) => String(v.id) === id)?.name ?? id;
    const d = devices.find((x) => x.mac === id);
    return d ? d.label || d.mac : id;
  }, [picked, mode, villages, devices]);
  const onlineInTarget = useMemo(() => {
    const id = picked[0];
    if (!id) return 0;
    if (mode === 'village') return villages.find((v) => String(v.id) === id)?.online_count ?? 0;
    return devices.find((x) => x.mac === id)?.online ? 1 : 0;
  }, [picked, mode, villages, devices]);

  const start = async () => {
    if (!pkg || picked.length !== 1) return;
    const what = mode === 'village' ? `마을 「${targetName}」의 켜진 단말 ${onlineInTarget}대` : `단말 「${targetName}」`;
    if (!window.confirm(`${what}에 펌웨어 ${pkg.version} 을 보냅니다.\n단말은 다운로드·검증 뒤 스스로 재부팅합니다. 진행할까요?`)) return;
    setStarting(true);
    setError(null);
    try {
      const job = await api.ota.start({ package_id: pkg.id, target_scope: mode === 'village' ? 'village' : 'device', target_ids: picked });
      setNotice(`OTA 를 시작했습니다 — job ${job.broadcast.job_id}, 보낸 단말 ${job.sent_count}대. 아래 진행 표에서 확인하세요.`);
      setPicked([]);
      jobs.reload();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'OTA 를 시작하지 못했습니다.');
    } finally {
      setStarting(false);
    }
  };

  // ── 진행·이력 ──
  const jobs = usePolling(() => api.ota.jobs(20), POLL_INTERVAL.broadcasting);
  const [openJob, setOpenJob] = useState<number | null>(null);

  const canStart = !!pkg && picked.length === 1 && onlineInTarget > 0 && !starting;

  return (
    <div className="ota">
      {error && <div className="alert" style={{ marginBottom: 14 }}>{error}</div>}
      {notice && (
        <div className="alert alert--ok" style={{ marginBottom: 14 }}>
          {notice}
        </div>
      )}

      <div className="ota__grid">
        {/* ① 패키지 */}
        <section className="card ota__card">
          <StepTitle n={1} done={packageId !== ''}>펌웨어 패키지</StepTitle>
          <div className="ota__upload">
            <div className="file-pick">
              <input
                id="ota-file"
                type="file"
                accept=".pkg"
                className="file-pick__input"
                onClick={(e) => ((e.target as HTMLInputElement).value = '')}
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                aria-label="패키지 파일"
              />
              <label htmlFor="ota-file" className="btn">파일 고르기</label>
              <span className={file ? 'strong' : 'dim'}>{file ? `${file.name} · ${mb(file.size)}` : '.pkg 파일을 골라 주세요'}</span>
            </div>
            <div className="field">
              <label htmlFor="ota-ver">
                펌웨어 버전 — 단말이 STATUS 로 보고하는 글자 그대로. P4 나 C6 하나만 바뀌면 그 버전, 둘 다면 「P4버전 / C6버전」
              </label>
              <input id="ota-ver" type="text" value={version} onChange={(e) => setVersion(e.target.value)} placeholder="예: V.260905-1  또는  V.260905-1 / V.260901-2" maxLength={50} />
            </div>
            <div className="field">
              <label htmlFor="ota-pkgver">pkg_version (OTA_START 에 실리는 번호, 1 이상 정수)</label>
              <input
                id="ota-pkgver"
                type="text"
                inputMode="numeric"
                pattern="[0-9]*"
                value={pkgVersion}
                onChange={(e) => setPkgVersion(e.target.value.replace(/[^0-9]/g, ''))}
                placeholder="예: 3"
              />
            </div>
            <div className="field">
              <label htmlFor="ota-note">메모 (선택)</label>
              <input id="ota-note" type="text" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="무엇이 바뀐 펌웨어인지" />
            </div>
            <button
              type="button"
              className="btn btn--primary"
              disabled={!file || !version.trim() || !(Number(pkgVersion) >= 1) || uploading}
              onClick={() => void upload()}
            >
              {uploading ? '올리는 중…' : '패키지 올리기'}
            </button>
          </div>
          <div className="table-wrap table-wrap--scroll" style={{ marginTop: 12 }}>
            {packages.length === 0 ? (
              <div className="empty">올린 패키지가 없습니다.</div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>파일</th>
                    <th>버전</th>
                    <th className="num">pkg_version</th>
                    <th className="num">크기</th>
                    <th>올린 사람 · 때</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {packages.map((p) => (
                    <tr key={p.id} className={p.id === packageId ? 'is-on' : undefined}>
                      <td className="strong" title={p.note ?? undefined}>
                        {p.filename}
                        {p.note && <div className="dim" style={{ fontWeight: 400, fontSize: 12 }}>{p.note}</div>}
                      </td>
                      <td className="mono">{p.version}</td>
                      <td className="num">{p.pkg_version}</td>
                      <td className="num dim">{mb(p.size_bytes)}</td>
                      <td className="dim">
                        {p.uploaded_by_name ?? '—'} · {fmt(p.created_at)}
                      </td>
                      <td className="num">
                        <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                          <button type="button" className="btn btn--sm" onClick={() => setPackageId(p.id)} aria-pressed={p.id === packageId}>
                            {p.id === packageId ? '선택됨' : '배포에 쓰기'}
                          </button>
                          <button
                            type="button"
                            className="btn btn--sm btn--ghost btn--danger"
                            disabled={p.active_jobs > 0}
                            title={p.active_jobs > 0 ? '진행 중인 OTA 가 있어 지울 수 없습니다' : undefined}
                            onClick={() => void removePackage(p)}
                          >
                            삭제
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </section>

        {/* ② 배포 */}
        <section className="card ota__card">
          <StepTitle n={2}>어디에 보낼까요? — 마을 하나 또는 단말 하나</StepTitle>
          <div className="bc-seg ota__seg" role="group" aria-label="대상 단위">
            <button type="button" aria-pressed={mode === 'village'} onClick={() => setMode('village')}>
              마을 하나
            </button>
            <button type="button" aria-pressed={mode === 'device'} onClick={() => setMode('device')}>
              단말 하나
            </button>
          </div>
          <TargetTreePicker
            mode={mode}
            single
            orgs={orgs}
            villages={villages}
            devices={devices}
            zonesOf={{}}
            onNeedZones={() => undefined}
            value={{ leaves: picked, groups: [] }}
            onChange={(next) => setPicked(next.leaves.slice(0, 1))}
            renderCount={(online, total) => (
              <span className={online > 0 ? 'dim' : 'count--none'}>{total === 0 ? '단말 없음' : `켜짐 ${online}/${total}`}</span>
            )}
          />
          <div className="ota__go">
            <p className="ota__sentence">
              {!pkg ? (
                <span className="dim">① 에서 패키지를 「배포에 쓰기」로 고르세요.</span>
              ) : !targetName ? (
                <span className="dim">위 트리에서 {mode === 'village' ? '마을' : '단말'} 하나를 고르세요.</span>
              ) : onlineInTarget === 0 ? (
                <span className="count--none">「{targetName}」에 켜진 단말이 없어 보낼 수 없습니다.</span>
              ) : (
                <>
                  <strong>{targetName}</strong>
                  {mode === 'village' ? ` (켜진 단말 ${onlineInTarget}대)` : ''}에 <strong>{pkg.filename}</strong> ({pkg.version}) 을 보냅니다.
                </>
              )}
            </p>
            <button type="button" className="btn btn--primary" disabled={!canStart} onClick={() => void start()}>
              {starting ? '보내는 중…' : 'OTA 시작'}
            </button>
          </div>
          <p className="hint">
            방송 중인 단말은 OTA 를 거절(BUSY)하고, OTA 중인 단말에는 방송을 걸 수 없습니다. 단말이 패키지를 끝까지 받아가면 그
            단말은 <b>성공</b>이고, 곧 연결을 끊고 스스로 재부팅하므로 오프라인으로 표시됩니다. 몇 분 안에 다시 붙는지는 사람이
            확인합니다. 다시 붙은 뒤 보고하는 펌웨어 버전이 패키지 버전과 같으면 「적용 확인」까지 붙습니다.
          </p>
        </section>
      </div>

      {/* ③ 진행·이력 */}
      <section className="card ota__card" style={{ marginTop: 16 }}>
        <StepTitle n={3}>진행 · 이력</StepTitle>
        <p className="hint" style={{ marginTop: -6 }}>
          상태 읽는 법 — <b>성공 · 다 받음 → 재부팅 중</b>: 패키지를 끝까지 받아갔다(=성공), 지금은 끊고 재부팅하는 중 ·{' '}
          <b>적용 확인</b>: 다시 붙어 보고한 펌웨어 버전이 패키지 버전과 같다 · <b>성공 · 다시 붙음 (버전 글자 다름)</b>: 받아는 갔는데 다시
          붙어 보고한 버전 글자가 패키지에 적은 것과 다르다(버전 칸을 잘못 적었거나 롤백) · <b>실패 · 사유</b>: 단말이 거절·검증 실패 ·{' '}
          <b>진행 %</b>: 받는 중 · <b>대기 중</b>: 아직 신호 없음 · <b>응답 없음</b>: 10분 안에 아무 신호가 없었다 · <b>오프라인 · 안 보냄</b>:
          시작할 때 꺼져 있어 보내지 않았다.
        </p>
        {jobs.error && <div className="alert">{jobs.error.message}</div>}
        {(jobs.data ?? []).length === 0 ? (
          <div className="empty">아직 OTA 작업이 없습니다.</div>
        ) : (
          <div className="ota__jobs">
            {(jobs.data ?? []).map((j) => {
              const b = j.broadcast;
              const ended = b.ended_at !== null;
              const open = openJob === b.id;
              return (
                <div key={b.id} className={`ota__job${ended ? '' : ' is-live'}`}>
                  <button type="button" className="ota__jobhead" onClick={() => setOpenJob(open ? null : b.id)} aria-expanded={open}>
                    <span className={`badge ${ended ? 'badge--idle' : 'badge--warn'}`}>{ended ? '종료' : '진행 중'}</span>
                    <span className="strong">{b.file_name ?? j.package?.filename ?? '패키지'}</span>
                    <span className="dim">→ {b.target_label}</span>
                    <span className="dim">{fmt(b.triggered_at)}</span>
                    <span className="filters__spacer" />
                    <span className="ota__counts">
                      성공 <b>{j.done_count}</b> / 보냄 {j.sent_count}
                      {j.applied_count > 0 ? ` · 적용 확인 ${j.applied_count}` : ''}
                      {j.devices.length > j.sent_count ? ` · 오프라인 ${j.devices.length - j.sent_count}` : ''}
                    </span>
                    <span aria-hidden="true">{open ? '▾' : '▸'}</span>
                  </button>
                  {open && (
                    <div className="table-wrap" style={{ marginTop: 8 }}>
                      <table>
                        <thead>
                          <tr>
                            <th>단말</th>
                            <th>마을</th>
                            <th>상태</th>
                            <th>지금 펌웨어 (P4 / C6)</th>
                            <th>통신</th>
                          </tr>
                        </thead>
                        <tbody>
                          {j.devices.map((d) => {
                            const st = deviceState(d, ended);
                            return (
                              <tr key={d.mac}>
                                <td>
                                  <span className="strong">{d.label || <span className="mono dim">{d.mac}</span>}</span>
                                  {d.label && <span className="mono dim"> {d.mac}</span>}
                                </td>
                                <td>{d.village_name ?? '—'}</td>
                                <td>
                                  <span className={st.cls}>{st.text}</span>
                                </td>
                                <td className="mono dim">
                                  {d.p4_fw ?? '—'} / {d.c6_fw ?? '—'}
                                </td>
                                <td>
                                  <span className={`badge ${d.online ? 'badge--ok' : 'badge--danger'}`}>{d.online ? '온라인' : '오프라인'}</span>
                                </td>
                              </tr>
                            );
                          })}
                          {j.devices.length === 0 && (
                            <tr>
                              <td colSpan={5} className="dim">
                                단말 응답이 아직 없습니다.
                              </td>
                            </tr>
                          )}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>
    </div>
  );
}
