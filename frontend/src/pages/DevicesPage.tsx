/**
 * 단말 관리.
 *
 * super_admin 에게만 '미배정 단말' 섹션이 보인다. 미배정 단말은 어느 마을에도
 * 속하지 않으므로 village_admin 의 담당 범위에 들어올 수 없다(백엔드가 걸러낸다).
 */

import { useEffect, useState } from 'react';

import { api } from '../api/client';
import { AddressSearchField } from '../components/AddressSearchField';
import { LocationPickerMap } from '../components/LocationPickerMap';
import { Modal } from '../components/Modal';
import { RegisterDeviceDialog } from '../components/RegisterDeviceDialog';
import { provisioningFrame } from '../lib/serial';
import type { Device, DeviceCredential, DeviceStatusFilter, Village, Zone } from '../api/types';
import { useAuth } from '../auth/AuthContext';
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

/** RSSI 등급. -60 이상 양호, -75 미만 약함. */
function signalTone(rssi: number | null): 'ok' | 'warn' | 'danger' | 'idle' {
  if (rssi === null) return 'idle';
  if (rssi >= -60) return 'ok';
  if (rssi >= -75) return 'warn';
  return 'danger';
}

const TONE_VAR = {
  ok: 'var(--ok-text)',
  warn: 'var(--warn-text)',
  danger: 'var(--danger-text)',
  idle: 'var(--text-muted)',
} as const;

/** 마을·구역 배정.
 *
 * 배정이 곧 방송 대상이다 — 마을이 없는 단말에는 서버가 village_id 를 안 내려주고,
 * 그러면 단말이 마을 topic 을 구독하지 않아 방송이 아예 도달하지 않는다.
 * 그동안 이 화면에 배정 수단이 없어서 API 를 직접 호출해야 했다.
 */
function AssignDialog({
  device,
  villages,
  onClose,
  onSaved,
}: {
  device: Device;
  villages: Village[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [villageId, setVillageId] = useState<number | ''>(device.village_id ?? '');
  const [zoneId, setZoneId] = useState<number | ''>(device.zone_id ?? '');
  const [label, setLabel] = useState(device.label ?? '');
  const [zones, setZones] = useState<Zone[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // 설치 위치 — 주소 검색이 채우고, 동/호만 수동. 비우면 지도가 마을 좌표로 대신 찍는다.
  const [loc, setLoc] = useState<{
    road_address: string | null;
    jibun_address: string | null;
    lat: number | null;
    lng: number | null;
  }>({
    road_address: device.road_address,
    jibun_address: device.jibun_address,
    lat: device.lat,
    lng: device.lng,
  });
  const [addressDetail, setAddressDetail] = useState(device.address_detail ?? '');
  //: 위치 보정 미니맵용 JS 키. 좌표가 있을 때만 한 번 받아온다.
  const [jsKey, setJsKey] = useState<string | null>(null);

  useEffect(() => {
    if (loc.lat === null || jsKey !== null) return;
    let alive = true;
    api.dashboard
      .map()
      .then((d) => alive && setJsKey(d.kakao_js_key))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [loc.lat, jsKey]);

  // 구역은 마을에 딸린 값이라 마을이 바뀌면 다시 불러온다.
  useEffect(() => {
    if (villageId === '') {
      setZones([]);
      return;
    }
    let alive = true;
    api.villages
      .zones(villageId)
      .then((z) => alive && setZones(z))
      .catch(() => alive && setZones([]));
    return () => {
      alive = false;
    };
  }, [villageId]);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.devices.update(device.mac, {
        label: label.trim() || null,
        village_id: villageId === '' ? null : villageId,
        // 마을을 바꾸면 이전 마을의 구역은 남아 있을 수 없다.
        zone_id: villageId === '' ? null : zoneId === '' ? null : zoneId,
        road_address: loc.road_address,
        jibun_address: loc.jibun_address,
        address_detail: addressDetail.trim() || null,
        lat: loc.lat,
        lng: loc.lng,
      });
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : '저장에 실패했습니다.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      title="단말 배정"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>
            취소
          </button>
          <button type="button" className="btn btn--primary" onClick={save} disabled={busy}>
            {busy ? '저장 중…' : '저장'}
          </button>
        </>
      }
    >
      <p className="mono dim" style={{ marginTop: 0 }}>
        {device.mac}
      </p>

      <div className="field">
        <label htmlFor="a-village">마을</label>
        <select
          id="a-village"
          value={villageId}
          onChange={(e) => {
            setVillageId(e.target.value === '' ? '' : Number(e.target.value));
            setZoneId('');
          }}
        >
          <option value="">미배정</option>
          {villages.map((v) => (
            <option key={v.id} value={v.id}>
              {v.name}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label htmlFor="a-zone">구역 (선택)</label>
        <select
          id="a-zone"
          value={zoneId}
          disabled={villageId === ''}
          onChange={(e) => setZoneId(e.target.value === '' ? '' : Number(e.target.value))}
        >
          <option value="">지정 안 함</option>
          {zones.map((z) => (
            <option key={z.id} value={z.id}>
              {z.name}
            </option>
          ))}
        </select>
      </div>

      <div className="field">
        <label htmlFor="a-label">별칭 (선택)</label>
        <input
          id="a-label"
          value={label}
          maxLength={40}
          placeholder="비우면 목록에 MAC 이 표시됩니다"
          onChange={(e) => setLabel(e.target.value)}
        />
      </div>

      <p className="hint">
        마을을 배정하면 서버가 CONFIG 로 단말에 알려주고, 단말이 그 마을 방송을 구독합니다.
        단말 STATUS 에 반영되는 데 몇 초 걸립니다.
      </p>

      <hr style={{ margin: '14px 0', opacity: 0.2 }} />
      <p className="strong" style={{ margin: '0 0 8px' }}>
        설치 위치 (선택)
      </p>
      <AddressSearchField
        placeholder="그 집 주소 검색 (예: 월선리 123-4)"
        onSelect={(r) =>
          setLoc({
            road_address: r.road_address,
            jibun_address: r.jibun_address ?? r.address_name,
            lat: r.lat,
            lng: r.lng,
          })
        }
      />
      {(loc.road_address || loc.jibun_address) && (
        <p className="hint">
          선택된 주소: <span className="strong">{loc.road_address ?? loc.jibun_address}</span>{' '}
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            onClick={() => setLoc({ road_address: null, jibun_address: null, lat: null, lng: null })}
          >
            지우기
          </button>
        </p>
      )}
      {/* 지오코딩은 지번 대표점까지라(아파트 단지 = 한 점) 마커가 동 단위로
          어긋난다 — 지도에서 끌어 보정하는 것이 정석이다. */}
      {loc.lat !== null && loc.lng !== null && jsKey && (
        <LocationPickerMap
          jsKey={jsKey}
          lat={loc.lat}
          lng={loc.lng}
          onChange={(lat, lng) => setLoc((prev) => ({ ...prev, lat, lng }))}
        />
      )}
      <div className="field">
        <label htmlFor="a-detail">동/호 (선택 — 사람이 치는 유일한 주소 항목)</label>
        <input
          id="a-detail"
          value={addressDetail}
          maxLength={100}
          placeholder="예: 201호, 안채"
          onChange={(e) => setAddressDetail(e.target.value)}
        />
      </div>
      <p className="hint">
        위치를 비워 두면 지도에서 마을 좌표 자리에 표시됩니다. 입력하면 그 집 위치에 찍힙니다.
      </p>

      {error && <p className="hint hint--warn">{error}</p>}
    </Modal>
  );
}

/** 단말별 MQTT 계정 — 발행·표시.
 *
 * 열면 발행 API 를 부른다. 이미 발행된 단말이면 기존 값을 그대로 돌려받는다
 * (재사용이 기본 — 계정은 단말이 폐기될 때까지 안 바꾼다).
 * [재발행]은 라인 재작업 전용이다: 새 값은 케이블로 단말에 다시 넣어야 하므로,
 * 현장에 나가 있는 단말에 쓰면 그 단말은 다시는 브로커에 못 붙는다.
 */
function CredentialDialog({ device, onClose }: { device: Device; onClose: () => void }) {
  const [cred, setCred] = useState<DeviceCredential | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const load = async (reissue: boolean) => {
    setBusy(true);
    setError(null);
    try {
      setCred(await api.devices.credential(device.mac, reissue));
    } catch (e) {
      setError(e instanceof Error ? e.message : '계정 발행에 실패했습니다.');
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    void load(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [device.mac]);

  const copy = async () => {
    if (!cred) return;
    try {
      await navigator.clipboard.writeText(
        provisioningFrame({
          serverHost: cred.server_host,
          mac: cred.username,
          password: cred.password,
        }),
      );
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* 클립보드 권한이 없으면 사용자가 직접 드래그해 복사한다 */
    }
  };

  const reissue = () => {
    if (
      window.confirm(
        '새 비밀번호를 발행하면 이전 값은 즉시 무효가 됩니다.\n' +
          '케이블이 꽂힌 단말(라인 재작업)에만 쓰세요 — 현장 단말에 쓰면 그 단말은 접속이 끊깁니다.\n' +
          '계속할까요?',
      )
    ) {
      void load(true);
    }
  };

  return (
    <Modal
      title="MQTT 계정"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn--ghost" onClick={reissue} disabled={busy}>
            재발행 (라인 전용)
          </button>
          <button type="button" className="btn btn--primary" onClick={onClose}>
            닫기
          </button>
        </>
      }
    >
      <p className="mono dim" style={{ marginTop: 0 }}>
        {device.mac}
      </p>
      {error && <p className="hint hint--warn">{error}</p>}
      {busy && !cred && <p className="hint">발행 중…</p>}
      {cred && (
        <>
          <div className="field">
            <label>username (= MAC)</label>
            <input className="mono" readOnly value={cred.username} />
          </div>
          <div className="field">
            <label>password</label>
            <input className="mono" readOnly value={cred.password} />
          </div>
          <button type="button" className="btn btn--sm" onClick={copy} disabled={busy}>
            {copied ? '복사됨 ✓' : '시리얼 명령 복사 (@SERVER/@MQTTID/@MQTTPW)'}
          </button>
          <p className="hint">
            생산 라인에서 시리얼로 단말에 넣는 값입니다. 발행된 계정은 단말 폐기 전까지 바뀌지
            않습니다{cred.issued ? ' — 방금 새로 발행됐습니다.' : ' — 기존 값을 다시 표시했습니다.'}
          </p>
        </>
      )}
    </Modal>
  );
}

/** 단말 삭제 — DB 행 + 브로커 계정 + CONFIG retain 을 한 묶음으로 지운다.
 *
 * 도난·회수 실패 단말을 막는 유일한 수단이 이 삭제다(계정 사양 §4.1).
 * 실수 방지로 MAC 끝 4자리를 직접 치게 한다(등록 흐름 사양 §D3 의 안전장치).
 *
 * ⚠ 이행기 한정: 공유 계정(xwifi-device)이 살아 있는 동안은, 삭제해도 단말이
 *   계속 접속해 있으면 다음 STATUS 로 미등록 행이 다시 생긴다. 단말 전원을
 *   끄고 지우거나, 공유 계정 폐기 후에는 계정 삭제만으로 완전히 끊긴다.
 */
function DeleteDialog({
  device,
  onClose,
  onDeleted,
}: {
  device: Device;
  onClose: () => void;
  onDeleted: () => void;
}) {
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const last4 = device.mac.slice(-4);

  const remove = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.devices.remove(device.mac);
      onDeleted();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : '삭제에 실패했습니다.');
      setBusy(false);
    }
  };

  return (
    <Modal
      title="단말 삭제"
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>
            취소
          </button>
          <button
            type="button"
            className="btn btn--danger"
            onClick={remove}
            disabled={busy || confirm.trim().toLowerCase() !== last4}
          >
            {busy ? '삭제 중…' : '영구 삭제'}
          </button>
        </>
      }
    >
      <p className="mono dim" style={{ marginTop: 0 }}>
        {device.mac} {device.label && `(${device.label})`}
      </p>
      <p className="hint hint--warn">
        DB 기록·MQTT 계정·마을 배정이 함께 삭제되며 되돌릴 수 없습니다. 이 단말은 더 이상
        브로커에 접속할 수 없게 됩니다(도난·폐기 단말 차단 수단). 다시 쓰려면 신규 단말
        등록부터 다시 해야 합니다.
      </p>
      <div className="field">
        <label htmlFor="del-confirm">
          확인을 위해 MAC 끝 4자리(<span className="mono">{last4}</span>)를 입력하세요
        </label>
        <input
          id="del-confirm"
          className="mono"
          value={confirm}
          maxLength={4}
          onChange={(e) => setConfirm(e.target.value)}
        />
      </div>
      {error && <p className="hint hint--warn">{error}</p>}
    </Modal>
  );
}

function DeviceTable({
  devices,
  showVillage,
  onAssign,
  onCredential,
  onDelete,
}: {
  devices: Device[];
  showVillage: boolean;
  onAssign: (d: Device) => void;
  /** super_admin 에게만 넘어온다 — 비밀번호가 응답에 실리는 기능이라서 */
  onCredential?: (d: Device) => void;
  /** super_admin 전용 — 계정 삭제(차단)가 같이 일어나는 파괴적 작업 */
  onDelete?: (d: Device) => void;
}) {
  if (devices.length === 0) {
    return <div className="empty">조건에 맞는 단말이 없습니다.</div>;
  }
  return (
    <table>
      <thead>
        <tr>
          <th className="mono">MAC</th>
          <th>별칭</th>
          {showVillage && <th>마을</th>}
          <th>구역</th>
          <th>상태</th>
          <th className="num">RSSI</th>
          <th className="num">CFG</th>
          <th className="num">마지막 통신</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {devices.map((d) => (
          <tr key={d.mac}>
            <td className="mono">
              {d.mac}
              {/* 계정 미발행 = 「미등록*」. 브로커에 붙긴 하는데 서버가 발행한
                  계정이 없는 단말 — 단말별 계정 전환이 끝나면 방송 대상에서도
                  빠진다(레지스트리 사양 §3.6). 계정 버튼으로 발행하면 사라진다. */}
              {!d.has_credential && (
                <span
                  className="badge badge--warn badge--plain"
                  style={{ marginLeft: 6 }}
                  title="MQTT 계정 미발행 — 단말별 계정 전환 후에는 브로커 접속과 방송 대상에서 제외됩니다. [계정] 버튼으로 발행하세요."
                >
                  미등록*
                </span>
              )}
            </td>
            {/* 이름이 비면 MAC 을 쓴다 — 목록에 빈 칸이 생기지 않게 (등록 사양 §3.3) */}
            <td className="strong">{d.label || <span className="mono dim">{d.mac}</span>}</td>
            {showVillage && <td>{d.village_name ?? '미배정'}</td>}
            <td>{d.zone_name ?? '—'}</td>
            <td>
              {/* RECONNECTING = 방송은 살아 있는데 스피커가 무음(사양 §5) — 경고색으로 */}
              <span
                className={`badge badge--${
                  !d.online ? 'danger' : d.live === 'RECONNECTING' ? 'warn' : 'ok'
                }`}
              >
                {!d.online
                  ? '오프라인'
                  : d.live === 'RECONNECTING'
                    ? '무음(재접속)'
                    : (d.state ?? '온라인')}
              </span>
            </td>
            <td className="num" style={{ color: TONE_VAR[signalTone(d.rssi)], fontWeight: 600 }}>
              {d.rssi ?? '—'}
            </td>
            <td className="num">{d.config_version ?? '—'}</td>
            <td className="num dim">{formatTime(d.last_seen_at)}</td>
            <td className="num">
              <div style={{ display: 'flex', gap: 6, justifyContent: 'flex-end' }}>
                {onCredential && (
                  <button type="button" className="btn btn--sm" onClick={() => onCredential(d)}>
                    계정
                  </button>
                )}
                <button type="button" className="btn btn--sm" onClick={() => onAssign(d)}>
                  배정
                </button>
                {onDelete && (
                  <button
                    type="button"
                    className="btn btn--sm btn--ghost btn--danger"
                    onClick={() => onDelete(d)}
                  >
                    삭제
                  </button>
                )}
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function DevicesPage() {
  const { user, isSuperAdmin } = useAuth();
  const [villageId, setVillageId] = useState<number | ''>('');
  const [statusFilter, setStatusFilter] = useState<DeviceStatusFilter | ''>('');
  const [search, setSearch] = useState('');
  const [assigning, setAssigning] = useState<Device | null>(null);
  const [credentialFor, setCredentialFor] = useState<Device | null>(null);
  const [deleting, setDeleting] = useState<Device | null>(null);
  const [registering, setRegistering] = useState(false);

  const villages = usePolling(() => api.villages.list(), 60_000);

  const devices = usePolling(
    () =>
      api.devices.list({
        village_id: villageId === '' ? undefined : villageId,
        status: statusFilter === '' ? undefined : statusFilter,
        q: search.trim() || undefined,
      }),
    POLL_INTERVAL.normal,
    [villageId, statusFilter, search],
  );

  // 미배정은 super_admin 만 본다. 마을 필터가 걸려 있으면 의미가 없으므로 숨긴다.
  const showUnassigned = isSuperAdmin && villageId === '' && statusFilter === '';
  const unassigned = usePolling(
    () => (showUnassigned ? api.devices.unassigned() : Promise.resolve([])),
    POLL_INTERVAL.normal,
    [showUnassigned],
  );

  return (
    <>
      <div className="page-head">
        <h1>단말 관리</h1>
        <p>
          {devices.data?.length ?? 0}대 표시 중 · {POLL_INTERVAL.normal / 1000}초마다 갱신
        </p>
      </div>

      <div className="filters">
        <select
          value={villageId}
          onChange={(e) => setVillageId(e.target.value === '' ? '' : Number(e.target.value))}
          aria-label="마을"
        >
          <option value="">{user?.all_villages ? '전체 마을' : '담당 마을 전체'}</option>
          {(villages.data ?? []).map((v) => (
            <option key={v.id} value={v.id}>
              {v.name} ({v.device_count})
            </option>
          ))}
        </select>

        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as DeviceStatusFilter | '')}
          aria-label="상태"
        >
          <option value="">전체 상태</option>
          <option value="online">온라인</option>
          <option value="offline">오프라인</option>
          {isSuperAdmin && <option value="unassigned">미배정</option>}
        </select>

        {/* 서버가 MAC 을 모르는 단말을 처음 넣는 입구 — QR 스캔/수동 + 계정 발행 + 시리얼 주입 */}
        {isSuperAdmin && (
          <button type="button" className="btn btn--primary" onClick={() => setRegistering(true)}>
            + 신규 단말 등록
          </button>
        )}

        <div className="filters__spacer" />

        <input
          type="search"
          placeholder="MAC 또는 별칭 검색"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ minWidth: 240 }}
        />
      </div>

      {devices.error && (
        <div className="alert" style={{ marginBottom: 14 }}>
          {devices.error.message}
        </div>
      )}

      <div className="table-wrap table-wrap--scroll">
        {devices.loading && !devices.data ? (
          <div className="empty">불러오는 중…</div>
        ) : (
          <DeviceTable
            devices={devices.data ?? []}
            showVillage={(user?.villages.length ?? 0) > 1}
            onAssign={setAssigning}
            onCredential={isSuperAdmin ? setCredentialFor : undefined}
            onDelete={isSuperAdmin ? setDeleting : undefined}
          />
        )}
      </div>

      {showUnassigned && (unassigned.data?.length ?? 0) > 0 && (
        <section className="unassigned">
          <div className="unassigned__head">
            <span className="unassigned__title">미배정 단말</span>
            <span className="badge badge--warn badge--plain">{unassigned.data?.length}</span>
          </div>
          <p className="unassigned__desc">
            STATUS 를 보내와 자동 등록됐지만 아직 마을이 지정되지 않은 단말입니다. 마을을 배정하면
            서버가 CONFIG 로 단말에 알려줍니다.
          </p>
          <div className="table-wrap table-wrap--scroll" style={{ marginTop: 14 }}>
            <DeviceTable
              devices={unassigned.data ?? []}
              showVillage={false}
              onAssign={setAssigning}
              onCredential={isSuperAdmin ? setCredentialFor : undefined}
              onDelete={isSuperAdmin ? setDeleting : undefined}
            />
          </div>
        </section>
      )}

      {deleting && (
        <DeleteDialog
          device={deleting}
          onClose={() => setDeleting(null)}
          onDeleted={() => {
            devices.reload();
            unassigned.reload();
          }}
        />
      )}

      {registering && (
        <RegisterDeviceDialog
          onClose={() => {
            setRegistering(false);
            devices.reload();
            unassigned.reload();
          }}
          onRegistered={() => {
            devices.reload();
            unassigned.reload();
          }}
        />
      )}

      {credentialFor && (
        <CredentialDialog
          device={credentialFor}
          onClose={() => {
            setCredentialFor(null);
            devices.reload();
            unassigned.reload();
          }}
        />
      )}

      {assigning && (
        <AssignDialog
          device={assigning}
          villages={villages.data ?? []}
          onClose={() => setAssigning(null)}
          onSaved={() => {
            devices.reload();
            unassigned.reload();
          }}
        />
      )}
    </>
  );
}
