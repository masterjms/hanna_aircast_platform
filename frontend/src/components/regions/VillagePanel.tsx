/**
 * 지역 관리 — 마을을 골랐을 때의 오른쪽 패널.
 *
 * 트리에서 마을은 "파일"이다. 이름은 트리에서 바꾸고(F2), 여기서는 마을의 **내용**을
 * 고친다: 주소·좌표(대시보드 지도가 단말을 찍을 자리), 구역, 소속 단말.
 *
 * 주소는 마을에만 있다(설계 v2 §1). 기관 마디는 이름과 자리뿐이고, 지도에 찍히는 것은
 * 마을 좌표와 경계 폴리곤이라 여기만 정확하면 된다.
 */

import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import { ApiError, api } from '../../api/client';
import type { Device, Village, VillageInput, Zone } from '../../api/types';
import { AddressSearchField } from '../AddressSearchField';

function coord(v: string): number | null {
  const n = Number(v);
  return v.trim() === '' || Number.isNaN(n) ? null : n;
}

type Form = Required<Omit<VillageInput, 'name' | 'organization_id'>>;

function formOf(v: Village): Form {
  return {
    sido: v.sido ?? '',
    sigungu: v.sigungu ?? '',
    address_detail: v.address_detail ?? '',
    b_code: v.b_code,
    road_address: v.road_address,
    jibun_address: v.jibun_address,
    lat: v.lat,
    lng: v.lng,
  };
}

function sameForm(a: Form, b: Form): boolean {
  return (Object.keys(a) as (keyof Form)[]).every((k) => (a[k] ?? '') === (b[k] ?? ''));
}

function deviceBadge(d: Device) {
  const tone = !d.online ? 'danger' : d.live === 'RECONNECTING' ? 'warn' : 'ok';
  const text = !d.online ? '오프라인' : d.live === 'RECONNECTING' ? '무음' : '온라인';
  return <span className={`badge badge--${tone}`}>{text}</span>;
}

export function VillagePanel({
  village,
  pathLabel,
  onChanged,
  onDelete,
}: {
  village: Village;
  /** 뿌리 → 이 마을 직전 기관까지. 기관 없는 마을이면 빈 문자열. */
  pathLabel: string;
  onChanged: () => Promise<void>;
  onDelete: () => void;
}) {
  const [form, setForm] = useState<Form>(() => formOf(village));
  const [zones, setZones] = useState<Zone[]>([]);
  const [devices, setDevices] = useState<Device[]>([]);
  const [zoneName, setZoneName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const fail = (err: unknown, fallback: string) =>
    setError(err instanceof ApiError ? err.message : fallback);

  const dirty = !sameForm(form, formOf(village));
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;

  // 다른 마을을 고르면 폼·표시를 그 마을 것으로 바꾼다.
  useEffect(() => {
    setForm(formOf(village));
    setSaved(false);
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [village.id]);

  // 같은 마을의 서버 값이 새로 오면(저장 뒤, 트리에서 이름을 바꾼 뒤) 고치던 게 없을 때만
  // 맞춘다 — 주소를 반쯤 적는 중에 트리 조작으로 목록이 갱신돼도 입력이 날아가면 안 된다.
  useEffect(() => {
    if (!dirtyRef.current) setForm(formOf(village));
  }, [village]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const [z, d] = await Promise.all([
          api.villages.zones(village.id),
          api.devices.list({ village_id: village.id }),
        ]);
        if (!cancelled) {
          setZones(z);
          setDevices(d);
        }
      } catch (err) {
        if (!cancelled) fail(err, '구역·단말 목록을 불러오지 못했습니다.');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [village.id]);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.villages.update(village.id, {
        sido: form.sido || null,
        sigungu: form.sigungu || null,
        address_detail: form.address_detail || null,
        b_code: form.b_code,
        road_address: form.road_address,
        jibun_address: form.jibun_address,
        lat: form.lat,
        lng: form.lng,
      });
      await onChanged();
      setSaved(true);
    } catch (err) {
      fail(err, '저장에 실패했습니다.');
    } finally {
      setBusy(false);
    }
  };

  const addZone = async () => {
    if (!zoneName.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.villages.createZone(village.id, { name: zoneName.trim() });
      setZoneName('');
      setZones(await api.villages.zones(village.id));
    } catch (err) {
      fail(err, '구역 추가에 실패했습니다.');
    } finally {
      setBusy(false);
    }
  };

  const removeZone = async (z: Zone) => {
    if (!window.confirm(`구역 "${z.name}" 을(를) 삭제할까요? 소속 단말은 마을에 그대로 남습니다.`))
      return;
    setError(null);
    try {
      await api.villages.removeZone(z.id);
      setZones(await api.villages.zones(village.id));
    } catch (err) {
      fail(err, '구역 삭제에 실패했습니다.');
    }
  };

  return (
    <>
      <div className="detail__head">
        <div>
          <div className="crumbs">
            {pathLabel ? (
              pathLabel.split(' › ').map((n, i) => (
                <span key={i} className="crumbs__item">
                  {n}
                </span>
              ))
            ) : (
              <span className="crumbs__item crumbs__item--warn">기관 없음 · 최고 관리자만 봄</span>
            )}
          </div>
          <h2 className="detail__title">
            <VillageIcon />
            {village.name}
          </h2>
        </div>
        <button type="button" className="btn btn--ghost btn--danger" onClick={onDelete}>
          마을 삭제
        </button>
      </div>

      <div className="tiles tiles--4">
        <Tile label="단말" value={village.device_count} unit="대" />
        <Tile
          label="온라인"
          value={village.online_count}
          unit="대"
          tone={village.device_count > 0 && village.online_count === 0 ? 'danger' : 'ok'}
        />
        <Tile label="구역" value={zones.length} unit="개" />
        <div className="tile">
          <div className="tile__head">
            <span className="tile__label">village_id</span>
          </div>
          <div className="tile__row">
            <span className="tile__value tile__value--mono">{village.village_token}</span>
          </div>
          <div className="tile__note">
            {village.village_code ? '법정동코드 + 연번' : '주소가 없어 옛 8자리'}
          </div>
        </div>
      </div>

      {error && <div className="alert">{error}</div>}

      <section className="card detail__card">
        <div className="detail__cardhead">
          <h3 className="section-title">위치 · 주소</h3>
          <div className="detail__actions">
            {saved && !dirty && <span className="dim">저장됨</span>}
            {dirty && (
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() => setForm(formOf(village))}
                disabled={busy}
              >
                되돌리기
              </button>
            )}
            <button
              type="button"
              className="btn btn--primary btn--sm"
              onClick={() => void save()}
              disabled={busy || !dirty}
            >
              {busy ? '저장 중…' : '저장'}
            </button>
          </div>
        </div>
        <p className="hint">
          지도에서 이 마을의 단말이 찍힐 자리입니다. 주소 검색 한 번으로 도로명·지번·법정동코드·좌표가
          같이 채워집니다. 위치를 따로 안 적은 단말은 이 좌표에 표시됩니다.
        </p>

        <AddressSearchField
          onSelect={(r) =>
            setForm({
              ...form,
              road_address: r.road_address,
              jibun_address: r.jibun_address ?? r.address_name,
              b_code: r.b_code,
              lat: r.lat,
              lng: r.lng,
            })
          }
        />
        {(form.jibun_address || form.road_address) && (
          <p className="hint">
            선택된 주소: <span className="strong">{form.road_address ?? form.jibun_address}</span>
            {form.b_code && (
              <>
                {' '}
                · 법정동코드 <span className="mono">{form.b_code}</span>
              </>
            )}
            {village.has_boundary ? ' · 경계 있음' : ' · 경계 없음'}
          </p>
        )}

        <div className="field-row">
          <div className="field">
            <label htmlFor="v-sido">시/도</label>
            <input
              id="v-sido"
              type="text"
              value={form.sido ?? ''}
              onChange={(e) => setForm({ ...form, sido: e.target.value })}
            />
          </div>
          <div className="field">
            <label htmlFor="v-sigungu">시/군/구</label>
            <input
              id="v-sigungu"
              type="text"
              value={form.sigungu ?? ''}
              onChange={(e) => setForm({ ...form, sigungu: e.target.value })}
            />
          </div>
        </div>
        <div className="field">
          <label htmlFor="v-addr">상세 주소</label>
          <input
            id="v-addr"
            type="text"
            value={form.address_detail ?? ''}
            onChange={(e) => setForm({ ...form, address_detail: e.target.value })}
          />
        </div>
        <div className="field-row">
          <div className="field">
            <label htmlFor="v-lat">위도 (검색하면 자동)</label>
            <input
              id="v-lat"
              type="text"
              inputMode="decimal"
              placeholder="36.5684"
              value={form.lat ?? ''}
              onChange={(e) => setForm({ ...form, lat: coord(e.target.value) })}
            />
          </div>
          <div className="field">
            <label htmlFor="v-lng">경도 (검색하면 자동)</label>
            <input
              id="v-lng"
              type="text"
              inputMode="decimal"
              placeholder="128.7294"
              value={form.lng ?? ''}
              onChange={(e) => setForm({ ...form, lng: coord(e.target.value) })}
            />
          </div>
        </div>
      </section>

      <section className="card detail__card">
        <div className="detail__cardhead">
          <h3 className="section-title">구역</h3>
        </div>
        <div className="filters" style={{ marginBottom: 10 }}>
          <input
            type="text"
            placeholder="구역 이름 (예: 마을회관)"
            value={zoneName}
            onChange={(e) => setZoneName(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && void addZone()}
            style={{ flex: 1 }}
          />
          <button
            type="button"
            className="btn"
            onClick={() => void addZone()}
            disabled={busy || !zoneName.trim()}
          >
            추가
          </button>
        </div>
        {zones.length === 0 ? (
          <div className="empty empty--tight">구역이 없습니다. 없어도 마을 단위로 방송됩니다.</div>
        ) : (
          <ul className="plain-list">
            {zones.map((z) => (
              <li key={z.id}>
                <span className="strong">{z.name}</span>
                <span className="dim num">
                  {z.device_count}대 · 온라인 {z.online_count}
                </span>
                <button
                  type="button"
                  className="btn btn--ghost btn--danger"
                  onClick={() => void removeZone(z)}
                >
                  삭제
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card detail__card">
        <div className="detail__cardhead">
          <h3 className="section-title">단말 {devices.length}대</h3>
          <Link to="/devices" className="btn btn--ghost btn--sm">
            단말 관리에서 등록·배정 →
          </Link>
        </div>
        {devices.length === 0 ? (
          <div className="empty empty--tight">
            이 마을에 배정된 단말이 없습니다. 단말 관리에서 미배정 단말을 이 마을로 배정하세요.
          </div>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>MAC</th>
                  <th>별칭</th>
                  <th>상태</th>
                  <th>구역</th>
                  <th>마지막 접속</th>
                </tr>
              </thead>
              <tbody>
                {devices.map((d) => (
                  <tr key={d.mac}>
                    <td className="mono">{d.mac}</td>
                    <td>{d.label ?? <span className="dim">—</span>}</td>
                    <td>{deviceBadge(d)}</td>
                    <td>{d.zone_name ?? <span className="dim">—</span>}</td>
                    <td className="dim">
                      {d.last_seen_at ? new Date(d.last_seen_at).toLocaleString('ko-KR') : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

export function Tile({
  label,
  value,
  unit,
  note,
  tone,
}: {
  label: string;
  value: number | string;
  unit?: string;
  note?: string;
  tone?: 'ok' | 'warn' | 'danger';
}) {
  return (
    <div className={`tile${tone ? ` tile--${tone}` : ''}`}>
      <div className="tile__head">
        <span className="tile__label">{label}</span>
      </div>
      <div className="tile__row">
        <span className="tile__value">{value}</span>
        {unit && <span className="tile__unit">{unit}</span>}
      </div>
      {note && <div className="tile__note">{note}</div>}
    </div>
  );
}

export function VillageIcon() {
  return (
    <svg className="trow__svg" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path
        d="M8 1.5c-2.5 0-4.5 2-4.5 4.5 0 3.2 4.5 8.5 4.5 8.5s4.5-5.3 4.5-8.5c0-2.5-2-4.5-4.5-4.5zm0 6.2a1.7 1.7 0 1 1 0-3.4 1.7 1.7 0 0 1 0 3.4z"
        fill="currentColor"
      />
    </svg>
  );
}
