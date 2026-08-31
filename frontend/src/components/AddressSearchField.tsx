/**
 * 주소 검색 입력 — 마을 등록과 단말 위치 입력이 같이 쓴다.
 *
 * 검색 한 번으로 도로명·지번·법정동코드·좌표가 한꺼번에 나온다(지도 설계 §3).
 * 사람이 치는 것은 검색어뿐이고, 값들은 결과에서 고르는 순간 부모로 넘어간다 —
 * 코드나 좌표를 손으로 치게 만들면 반드시 틀린다.
 */

import { useState } from 'react';

import { api } from '../api/client';
import type { AddressResult } from '../api/types';

export function AddressSearchField({
  onSelect,
  placeholder = '주소 검색 (리 이름만 넣어도 됩니다)',
}: {
  onSelect: (r: AddressResult) => void;
  placeholder?: string;
}) {
  const [q, setQ] = useState('');
  const [results, setResults] = useState<AddressResult[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const search = async () => {
    if (q.trim().length < 2) return;
    setBusy(true);
    setError(null);
    try {
      setResults(await api.geo.searchAddress(q.trim()));
    } catch (e) {
      setError(e instanceof Error ? e.message : '주소 검색에 실패했습니다.');
      setResults(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="field">
      <label>주소 검색</label>
      <div style={{ display: 'flex', gap: 6 }}>
        <input
          type="search"
          value={q}
          placeholder={placeholder}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault(); // 모달 폼 제출 방지
              void search();
            }
          }}
          style={{ flex: 1 }}
        />
        <button type="button" className="btn" onClick={() => void search()} disabled={busy}>
          {busy ? '검색 중…' : '검색'}
        </button>
      </div>
      {error && <p className="hint hint--warn">{error}</p>}
      {results !== null && results.length === 0 && (
        <p className="hint">결과가 없습니다 — 지번(리 이름+번지) 또는 도로명으로 검색해 보세요.</p>
      )}
      {results !== null && results.length > 0 && (
        <ul className="plain-list" style={{ marginTop: 6 }}>
          {results.map((r, i) => (
            <li key={i}>
              <button
                type="button"
                className="btn btn--ghost"
                style={{ textAlign: 'left', width: '100%' }}
                onClick={() => {
                  onSelect(r);
                  setResults(null);
                  setQ('');
                }}
              >
                <span className="strong">{r.address_name}</span>
                {r.road_address && r.road_address !== r.address_name && (
                  <span className="dim"> · {r.road_address}</span>
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
