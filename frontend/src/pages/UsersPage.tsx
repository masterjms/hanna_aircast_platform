/**
 * 계정 관리 (super_admin).
 *
 * village_admin 은 담당 마을을 반드시 지정해야 의미가 있다 — 비워두면
 * 로그인은 되지만 아무것도 못 보는 계정이 된다. 화면에서 경고로 알려준다.
 */

import { useCallback, useEffect, useState } from 'react';

import { ApiError, api } from '../api/client';
import type { Role, User, Village } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { Modal } from '../components/Modal';

interface FormState {
  username: string;
  password: string;
  role: Role;
  village_ids: number[];
}

const EMPTY: FormState = { username: '', password: '', role: 'village_admin', village_ids: [] };

export function UsersPage() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [villages, setVillages] = useState<Village[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [form, setForm] = useState<FormState | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const fail = (err: unknown, fallback: string) =>
    setError(err instanceof ApiError ? err.message : fallback);

  const load = useCallback(async () => {
    try {
      const [u, v] = await Promise.all([api.users.list(), api.villages.list()]);
      setUsers(u);
      setVillages(v);
    } catch (err) {
      fail(err, '계정 목록을 불러오지 못했습니다.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const save = async () => {
    if (!form) return;
    setBusy(true);
    setError(null);
    try {
      if (editingId === null) {
        await api.users.create({
          username: form.username.trim(),
          password: form.password,
          role: form.role,
          village_ids: form.role === 'super_admin' ? [] : form.village_ids,
        });
      } else {
        // 비밀번호는 입력했을 때만 보낸다 — 빈 문자열을 보내면 초기화돼 버린다.
        await api.users.update(editingId, {
          ...(form.password ? { password: form.password } : {}),
          role: form.role,
          ...(form.role === 'super_admin' ? {} : { village_ids: form.village_ids }),
        });
      }
      setForm(null);
      setEditingId(null);
      await load();
    } catch (err) {
      fail(err, '저장에 실패했습니다.');
    } finally {
      setBusy(false);
    }
  };

  const remove = async (u: User) => {
    if (!window.confirm(`계정 "${u.username}" 을(를) 삭제할까요?`)) return;
    setError(null);
    try {
      await api.users.remove(u.id);
      await load();
    } catch (err) {
      fail(err, '삭제에 실패했습니다.');
    }
  };

  const toggleVillage = (id: number) => {
    if (!form) return;
    const has = form.village_ids.includes(id);
    setForm({
      ...form,
      village_ids: has ? form.village_ids.filter((x) => x !== id) : [...form.village_ids, id],
    });
  };

  const villageNames = (ids: number[]) =>
    ids.length === 0
      ? '—'
      : ids
          .map((id) => villages.find((v) => v.id === id)?.name ?? `#${id}`)
          .join(', ');

  const canSubmit =
    form !== null &&
    (editingId !== null || (form.username.trim().length >= 3 && form.password.length >= 8)) &&
    (form.password === '' || form.password.length >= 8);

  return (
    <>
      <div className="page-head page-head--row">
        <div>
          <h1>계정 관리</h1>
          <p>관리자 계정 {users.length}개</p>
        </div>
        <button
          type="button"
          className="btn btn--primary"
          onClick={() => {
            setEditingId(null);
            setForm({ ...EMPTY });
          }}
        >
          계정 추가
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
        ) : (
          <table>
            <thead>
              <tr>
                <th>아이디</th>
                <th>역할</th>
                <th>담당 마을</th>
                <th>생성</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td className="strong">
                    {u.username}
                    {u.id === me?.id && <span className="tag">나</span>}
                  </td>
                  <td>
                    <span className={`badge badge--${u.role === 'super_admin' ? 'ok' : 'idle'}`}>
                      {u.role === 'super_admin' ? '최고 관리자' : '마을 관리자'}
                    </span>
                  </td>
                  <td>
                    {u.role === 'super_admin' ? (
                      <span className="dim">전체</span>
                    ) : u.village_ids.length === 0 ? (
                      <span className="badge badge--warn">담당 마을 없음</span>
                    ) : (
                      villageNames(u.village_ids)
                    )}
                  </td>
                  <td className="dim">{new Date(u.created_at).toLocaleDateString('ko-KR')}</td>
                  <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
                    <button
                      type="button"
                      className="btn btn--ghost"
                      onClick={() => {
                        setEditingId(u.id);
                        setForm({
                          username: u.username,
                          password: '',
                          role: u.role,
                          village_ids: [...u.village_ids],
                        });
                      }}
                    >
                      수정
                    </button>
                    <button
                      type="button"
                      className="btn btn--ghost btn--danger"
                      onClick={() => void remove(u)}
                      disabled={u.id === me?.id}
                      title={u.id === me?.id ? '자기 계정은 삭제할 수 없습니다' : undefined}
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
          title={editingId === null ? '계정 추가' : `계정 수정 · ${form.username}`}
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
                disabled={busy || !canSubmit}
              >
                {busy ? '저장 중…' : '저장'}
              </button>
            </>
          }
        >
          <div className="field">
            <label htmlFor="u-name">아이디</label>
            <input
              id="u-name"
              type="text"
              autoComplete="off"
              value={form.username}
              disabled={editingId !== null}
              onChange={(e) => setForm({ ...form, username: e.target.value })}
            />
            {editingId === null && <p className="hint">영문·숫자·. _ - 만, 3자 이상</p>}
          </div>

          <div className="field">
            <label htmlFor="u-pw">비밀번호</label>
            <input
              id="u-pw"
              type="password"
              autoComplete="new-password"
              placeholder={editingId === null ? '' : '변경할 때만 입력'}
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
            />
            <p className="hint">8자 이상 64자 이하</p>
          </div>

          <div className="field">
            <label htmlFor="u-role">역할</label>
            <select
              id="u-role"
              value={form.role}
              onChange={(e) => setForm({ ...form, role: e.target.value as Role })}
            >
              <option value="village_admin">마을 관리자</option>
              <option value="super_admin">최고 관리자</option>
            </select>
          </div>

          {form.role === 'village_admin' && (
            <div className="field">
              <label>담당 마을</label>
              {villages.length === 0 ? (
                <p className="hint">먼저 마을을 등록하세요.</p>
              ) : (
                <div className="checks">
                  {villages.map((v) => (
                    <label key={v.id} className="check">
                      <input
                        type="checkbox"
                        checked={form.village_ids.includes(v.id)}
                        onChange={() => toggleVillage(v.id)}
                      />
                      <span>{v.name}</span>
                    </label>
                  ))}
                </div>
              )}
              {form.village_ids.length === 0 && villages.length > 0 && (
                <p className="hint hint--warn">
                  담당 마을이 없으면 로그인은 되지만 아무 데이터도 보이지 않습니다.
                </p>
              )}
            </div>
          )}

          {form.role === 'super_admin' && (
            <p className="hint">최고 관리자는 전체 마을에 접근합니다. 담당 마을을 지정하지 않습니다.</p>
          )}
        </Modal>
      )}
    </>
  );
}
