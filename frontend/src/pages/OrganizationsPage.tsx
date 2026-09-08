/**
 * 기관 관리 (super_admin) — 관리자 계층 설계 §3·§9.
 *
 * 시·도청과 시·군청의 트리다. 권한은 이 트리를 따르고, 마을의 주소는 트리의
 * 입력값이 아니다(주소상 다른 군에 있는 마을을 이 군청이 관리하는 위탁이 흔하다).
 *
 * 소속 마을·계정이 있는 기관은 지울 수 없다. 목록에 그 수를 같이 보여 왜 안 지워지는지
 * 바로 알 수 있게 한다.
 */

import { useCallback, useEffect, useState } from 'react';

import { ApiError, api } from '../api/client';
import type { OrgLevel, Organization, OrganizationInput } from '../api/types';
import { Modal } from '../components/Modal';

const LEVEL_LABEL: Record<OrgLevel, string> = { sido: '시·도', sigungu: '시·군' };

const EMPTY: OrganizationInput = { name: '', level: 'sigungu', parent_id: null, jurisdiction_code: '' };

export function OrganizationsPage() {
  const [orgs, setOrgs] = useState<Organization[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState<OrganizationInput | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const fail = (err: unknown, fallback: string) =>
    setError(err instanceof ApiError ? err.message : fallback);

  const load = useCallback(async () => {
    try {
      setOrgs(await api.organizations.list());
    } catch (err) {
      fail(err, '기관 목록을 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const sidos = orgs.filter((o) => o.level === 'sido');

  const save = async () => {
    if (!form?.name.trim()) return;
    setBusy(true);
    setError(null);
    const body: OrganizationInput = {
      ...form,
      name: form.name.trim(),
      // 시·도는 상위가 없다. 코드 칸이 비면 null 로 보낸다(빈 문자열은 검증에 걸린다).
      parent_id: form.level === 'sido' ? null : form.parent_id,
      jurisdiction_code: form.jurisdiction_code?.trim() ? form.jurisdiction_code.trim() : null,
    };
    try {
      if (editingId === null) await api.organizations.create(body);
      else await api.organizations.update(editingId, body);
      setForm(null);
      setEditingId(null);
      await load();
    } catch (err) {
      fail(err, '저장에 실패했습니다.');
    } finally {
      setBusy(false);
    }
  };

  const remove = async (o: Organization) => {
    if (!window.confirm(`기관 "${o.name}" 을(를) 삭제할까요?`)) return;
    setError(null);
    try {
      await api.organizations.remove(o.id);
      await load();
    } catch (err) {
      fail(err, '삭제에 실패했습니다.');
    }
  };

  return (
    <>
      <div className="page-head page-head--row">
        <div>
          <h1>기관 관리</h1>
          <p>
            시·도 {sidos.length}개 · 시·군 {orgs.length - sidos.length}개. 권한은 이 트리를 따릅니다 —
            마을의 주소가 아니라 여기서 정한 소속이 관할입니다.
          </p>
        </div>
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => {
            setEditingId(null);
            setForm({ ...EMPTY });
          }}
        >
          기관 추가
        </button>
      </div>

      {error && (
        <div className="alert" style={{ marginBottom: 16 }}>
          {error}
        </div>
      )}

      <div className="table-wrap table-wrap--scroll">
        {loading ? (
          <div className="empty">불러오는 중…</div>
        ) : orgs.length === 0 ? (
          <div className="empty">등록된 기관이 없습니다. 시·도청부터 만들고 그 아래 시·군청을 추가하세요.</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>기관</th>
                <th>수준</th>
                <th>상위 기관</th>
                <th className="mono">관할 코드</th>
                <th className="num">마을</th>
                <th className="num">계정</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {orgs.map((o) => (
                <tr key={o.id}>
                  <td className="strong">{o.name}</td>
                  <td>
                    <span className={`badge badge--${o.level === 'sido' ? 'ok' : 'idle'}`}>
                      {LEVEL_LABEL[o.level]}
                    </span>
                  </td>
                  <td>{o.parent_name ?? <span className="dim">—</span>}</td>
                  <td className="mono">{o.jurisdiction_code ?? <span className="dim">—</span>}</td>
                  <td className="num">{o.village_count}</td>
                  <td className="num">{o.user_count}</td>
                  <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    <button
                      type="button"
                      className="btn btn--ghost"
                      onClick={() => {
                        setEditingId(o.id);
                        setForm({
                          name: o.name,
                          level: o.level,
                          parent_id: o.parent_id,
                          jurisdiction_code: o.jurisdiction_code ?? '',
                        });
                      }}
                    >
                      수정
                    </button>
                    <button
                      type="button"
                      className="btn btn--ghost btn--danger"
                      onClick={() => void remove(o)}
                      disabled={o.village_count > 0 || o.user_count > 0}
                      title={
                        o.village_count > 0 || o.user_count > 0
                          ? '소속 마을이나 계정이 있으면 삭제할 수 없습니다'
                          : undefined
                      }
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

      {form && (
        <Modal
          title={editingId === null ? '기관 추가' : `기관 수정 · ${form.name}`}
          onClose={() => {
            setForm(null);
            setEditingId(null);
          }}
          footer={
            <>
              <button
                type="button"
                className="btn"
                onClick={() => {
                  setForm(null);
                  setEditingId(null);
                }}
              >
                취소
              </button>
              <button
                type="button"
                className="btn btn--primary"
                onClick={() => void save()}
                disabled={busy || !form.name.trim()}
              >
                {busy ? '저장 중…' : '저장'}
              </button>
            </>
          }
        >
          <div className="field">
            <label htmlFor="o-name">기관 이름</label>
            <input
              id="o-name"
              type="text"
              placeholder="예: 금산군청, 충청남도청"
              value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })}
            />
          </div>

          <div className="field">
            <label htmlFor="o-level">수준</label>
            <select
              id="o-level"
              value={form.level}
              disabled={editingId !== null}
              onChange={(e) => {
                const level = e.target.value as OrgLevel;
                setForm({ ...form, level, parent_id: level === 'sido' ? null : form.parent_id });
              }}
            >
              <option value="sigungu">시·군 (군청 · 시청)</option>
              <option value="sido">시·도 (도청)</option>
            </select>
            {editingId !== null && <p className="hint">수준은 만든 뒤 바꿀 수 없습니다.</p>}
          </div>

          {form.level === 'sigungu' && (
            <div className="field">
              <label htmlFor="o-parent">상위 시·도</label>
              <select
                id="o-parent"
                value={form.parent_id ?? ''}
                onChange={(e) =>
                  setForm({ ...form, parent_id: e.target.value === '' ? null : Number(e.target.value) })
                }
              >
                <option value="">(없음)</option>
                {sidos.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
              <p className="hint">상위를 두면 그 시·도 관리자가 이 기관의 마을을 함께 봅니다.</p>
            </div>
          )}

          <div className="field">
            <label htmlFor="o-code">관할 코드 (법정동코드 앞자리)</label>
            <input
              id="o-code"
              className="mono"
              type="text"
              inputMode="numeric"
              placeholder={form.level === 'sido' ? '2자리, 예: 44' : '5자리, 예: 44710'}
              value={form.jurisdiction_code ?? ''}
              onChange={(e) => setForm({ ...form, jurisdiction_code: e.target.value })}
            />
            <p className="hint">
              마을을 만들 때 주소로 기관을 <strong>제안</strong>하는 데만 씁니다. 권한은 코드가 아니라
              마을에 지정한 소속을 따르므로, 비워 둬도 됩니다.
            </p>
          </div>
        </Modal>
      )}
    </>
  );
}
