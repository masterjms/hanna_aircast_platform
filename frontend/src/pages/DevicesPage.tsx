/**
 * 단말 관리.
 *
 * super_admin 에게만 '미배정 단말' 섹션이 보인다. 미배정 단말은 어느 마을에도
 * 속하지 않으므로 village_admin 의 담당 범위에 들어올 수 없다(백엔드가 걸러낸다).
 */

import { useEffect, useState } from 'react';

import { api } from '../api/client';
import { Modal } from '../components/Modal';
import type { Device, DeviceStatusFilter, Village, Zone } from '../api/types';
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

      {error && <p className="hint hint--warn">{error}</p>}
    </Modal>
  );
}

function DeviceTable({
  devices,
  showVillage,
  onAssign,
}: {
  devices: Device[];
  showVillage: boolean;
  onAssign: (d: Device) => void;
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
            <td className="mono">{d.mac}</td>
            {/* 이름이 비면 MAC 을 쓴다 — 목록에 빈 칸이 생기지 않게 (등록 사양 §3.3) */}
            <td className="strong">{d.label || <span className="mono dim">{d.mac}</span>}</td>
            {showVillage && <td>{d.village_name ?? '미배정'}</td>}
            <td>{d.zone_name ?? '—'}</td>
            <td>
              <span className={`badge badge--${d.online ? 'ok' : 'danger'}`}>
                {d.online ? (d.state ?? '온라인') : '오프라인'}
              </span>
            </td>
            <td className="num" style={{ color: TONE_VAR[signalTone(d.rssi)], fontWeight: 600 }}>
              {d.rssi ?? '—'}
            </td>
            <td className="num">{d.config_version ?? '—'}</td>
            <td className="num dim">{formatTime(d.last_seen_at)}</td>
            <td className="num">
              <button type="button" className="btn btn--sm" onClick={() => onAssign(d)}>
                배정
              </button>
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
            />
          </div>
        </section>
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
