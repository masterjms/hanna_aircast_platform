/**
 * 마을 관리 (super_admin).
 *
 * 마을을 고르면 우측에 그 마을의 구역이 나온다. 구역은 마을 안에서만 의미가 있어서
 * 별도 화면으로 두지 않고 여기 붙였다.
 */

import { useCallback, useEffect, useState } from 'react';

import { ApiError, api } from '../api/client';
import type { Village, VillageInput, Zone } from '../api/types';
import { AddressSearchField } from '../components/AddressSearchField';
import { Modal } from '../components/Modal';

const EMPTY_VILLAGE: VillageInput = {
  name: '',
  sido: '',
  sigungu: '',
  address_detail: '',
  b_code: null,
  road_address: null,
  jibun_address: null,
  lat: null,
  lng: null,
};

function coord(v: string): number | null {
  const n = Number(v);
  return v.trim() === '' || Number.isNaN(n) ? null : n;
}

export function VillagesPage() {
  const [villages, setVillages] = useState<Village[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [zones, setZones] = useState<Zone[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [villageForm, setVillageForm] = useState<VillageInput | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [zoneName, setZoneName] = useState('');
  const [busy, setBusy] = useState(false);

  const fail = (err: unknown, fallback: string) =>
    setError(err instanceof ApiError ? err.message : fallback);

  const loadVillages = useCallback(async () => {
    try {
      const list = await api.villages.list();
      setVillages(list);
      setSelected((cur) => cur ?? list[0]?.id ?? null);
    } catch (err) {
      fail(err, '마을 목록을 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadVillages();
  }, [loadVillages]);

  useEffect(() => {
    if (selected === null) {
      setZones([]);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const list = await api.villages.zones(selected);
        if (!cancelled) setZones(list);
      } catch (err) {
        if (!cancelled) fail(err, '구역 목록을 불러오지 못했습니다.');
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selected]);

  const reloadZones = async () => {
    if (selected !== null) setZones(await api.villages.zones(selected));
  };

  const saveVillage = async () => {
    if (!villageForm?.name.trim()) return;
    setBusy(true);
    setError(null);
    try {
      if (editingId === null) {
        const created = await api.villages.create(villageForm);
        setSelected(created.id);
      } else {
        await api.villages.update(editingId, villageForm);
      }
      setVillageForm(null);
      setEditingId(null);
      await loadVillages();
    } catch (err) {
      fail(err, '저장에 실패했습니다.');
    } finally {
      setBusy(false);
    }
  };

  const removeVillage = async (v: Village) => {
    const warning =
      v.device_count > 0
        ? `${v.name} 을(를) 삭제하면 소속 단말 ${v.device_count}대가 미배정으로 돌아갑니다. 계속할까요?`
        : `${v.name} 을(를) 삭제할까요?`;
    if (!window.confirm(warning)) return;
    setError(null);
    try {
      await api.villages.remove(v.id);
      setSelected(null);
      await loadVillages();
    } catch (err) {
      fail(err, '삭제에 실패했습니다.');
    }
  };

  const addZone = async () => {
    if (selected === null || !zoneName.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api.villages.createZone(selected, { name: zoneName.trim() });
      setZoneName('');
      await reloadZones();
      await loadVillages();
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
      await reloadZones();
    } catch (err) {
      fail(err, '구역 삭제에 실패했습니다.');
    }
  };

  const current = villages.find((v) => v.id === selected) ?? null;

  return (
    <>
      <div className="page-head page-head--row">
        <div>
          <h1>마을 관리</h1>
          <p>마을 {villages.length}개 · 마을을 고르면 구역이 보입니다.</p>
        </div>
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => {
            setEditingId(null);
            setVillageForm({ ...EMPTY_VILLAGE });
          }}
        >
          마을 추가
        </button>
      </div>

      {error && (
        <div className="alert" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      <div className="split">
        <div className="table-wrap table-wrap--scroll">
          {loading ? (
            <div className="empty">불러오는 중…</div>
          ) : villages.length === 0 ? (
            <div className="empty">등록된 마을이 없습니다.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>마을</th>
                  <th>지역</th>
                  <th className="mono">village_id</th>
                  <th className="num">단말</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {villages.map((v) => (
                  <tr
                    key={v.id}
                    onClick={() => setSelected(v.id)}
                    className={v.id === selected ? 'is-selected' : undefined}
                    style={{ cursor: 'pointer' }}
                  >
                    <td className="strong">{v.name}</td>
                    <td>{[v.sido, v.sigungu].filter(Boolean).join(' ') || '—'}</td>
                    <td className="mono">{v.village_token}</td>
                    <td className="num">{v.device_count}</td>
                    <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                      <button
                        type="button"
                        className="btn btn--ghost"
                        onClick={(e) => {
                          e.stopPropagation();
                          setEditingId(v.id);
                          setVillageForm({
                            name: v.name,
                            sido: v.sido ?? '',
                            sigungu: v.sigungu ?? '',
                            address_detail: v.address_detail ?? '',
                            b_code: v.b_code,
                            road_address: v.road_address,
                            jibun_address: v.jibun_address,
                            lat: v.lat,
                            lng: v.lng,
                          });
                        }}
                      >
                        수정
                      </button>
                      <button
                        type="button"
                        className="btn btn--ghost btn--danger"
                        onClick={(e) => {
                          e.stopPropagation();
                          void removeVillage(v);
                        }}
                      >
                        삭제
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        <aside className="card">
          <h2 className="section-title">{current ? `${current.name} · 구역` : '구역'}</h2>

          {current === null ? (
            <div className="empty">마을을 선택하세요.</div>
          ) : (
            <>
              <div className="filters" style={{ marginBottom: 12 }}>
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
                <div className="empty">구역이 없습니다.</div>
              ) : (
                <ul className="plain-list">
                  {zones.map((z) => (
                    <li key={z.id}>
                      <span className="strong">{z.name}</span>
                      <span className="dim num">{z.device_count}대</span>
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
            </>
          )}
        </aside>
      </div>

      {villageForm && (
        <Modal
          title={editingId === null ? '마을 추가' : '마을 수정'}
          onClose={() => {
            setVillageForm(null);
            setEditingId(null);
          }}
          footer={
            <>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setVillageForm(null);
                  setEditingId(null);
                }}
              >
                취소
              </button>
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => void saveVillage()}
                disabled={busy || !villageForm.name.trim()}
              >
                {busy ? '저장 중…' : '저장'}
              </button>
            </>
          }
        >
          <div className="field">
            <label htmlFor="v-name">마을 이름</label>
            <input
              id="v-name"
              type="text"
              value={villageForm.name}
              onChange={(e) => setVillageForm({ ...villageForm, name: e.target.value })}
            />
          </div>
          <div className="field-row">
            <div className="field">
              <label htmlFor="v-sido">시/도</label>
              <input
                id="v-sido"
                type="text"
                value={villageForm.sido ?? ''}
                onChange={(e) => setVillageForm({ ...villageForm, sido: e.target.value })}
              />
            </div>
            <div className="field">
              <label htmlFor="v-sigungu">시/군/구</label>
              <input
                id="v-sigungu"
                type="text"
                value={villageForm.sigungu ?? ''}
                onChange={(e) => setVillageForm({ ...villageForm, sigungu: e.target.value })}
              />
            </div>
          </div>
          <div className="field">
            <label htmlFor="v-addr">상세 주소</label>
            <input
              id="v-addr"
              type="text"
              value={villageForm.address_detail ?? ''}
              onChange={(e) => setVillageForm({ ...villageForm, address_detail: e.target.value })}
            />
          </div>

          {/* 대표 주소 — 검색 한 번으로 도로명·지번·법정동코드·좌표가 같이 채워진다.
              코드·좌표를 손으로 치게 두지 않는다(지도 설계 §3). */}
          <AddressSearchField
            onSelect={(r) =>
              setVillageForm({
                ...villageForm,
                road_address: r.road_address,
                jibun_address: r.jibun_address ?? r.address_name,
                b_code: r.b_code,
                lat: r.lat,
                lng: r.lng,
              })
            }
          />
          {(villageForm.jibun_address || villageForm.road_address) && (
            <p className="hint">
              선택된 주소: <span className="strong">{villageForm.road_address ?? villageForm.jibun_address}</span>
              {villageForm.b_code && (
                <>
                  {' '}· 법정동코드 <span className="mono">{villageForm.b_code}</span>
                </>
              )}
              {villageForm.lat != null && villageForm.lng != null && (
                <>
                  {' '}· 좌표 <span className="mono">{villageForm.lat.toFixed(5)}, {villageForm.lng.toFixed(5)}</span>
                </>
              )}
            </p>
          )}
          <div className="field-row">
            <div className="field">
              <label htmlFor="v-lat">위도 (검색하면 자동)</label>
              <input
                id="v-lat"
                type="text"
                inputMode="decimal"
                placeholder="36.5684"
                value={villageForm.lat ?? ''}
                onChange={(e) => setVillageForm({ ...villageForm, lat: coord(e.target.value) })}
              />
            </div>
            <div className="field">
              <label htmlFor="v-lng">경도 (검색하면 자동)</label>
              <input
                id="v-lng"
                type="text"
                inputMode="decimal"
                placeholder="128.7294"
                value={villageForm.lng ?? ''}
                onChange={(e) => setVillageForm({ ...villageForm, lng: coord(e.target.value) })}
              />
            </div>
          </div>
          <p className="hint">
            좌표가 있으면 지도 화면에 이 마을의 단말이 찍힙니다. 위치를 안 적은 단말은 마을
            좌표 자리에 표시됩니다.
          </p>
        </Modal>
      )}
    </>
  );
}
