/**
 * 계정 관리 (시·군 관리자 이상) — 관리자 계층 설계 §5·§9.
 *
 * 나보다 낮은 계층의 계정만 보이고 만들 수 있다. 역할 드롭다운이 그만큼만 나온다.
 * 시·도/시·군 관리자는 기관으로, 이장은 담당 마을로 범위가 정해진다.
 *
 * village_admin 은 담당 마을을 반드시 지정해야 의미가 있다 — 비워두면
 * 로그인은 되지만 아무것도 못 보는 계정이 된다. 화면에서 경고로 알려준다.
 */

import { useCallback, useEffect, useState } from 'react';

import { ApiError, api } from '../api/client';
import type { Organization, Role, User, Village } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { Modal } from '../components/Modal';
import { ROLE_LABEL, isOrgRole, manageableRoles } from '../lib/roles';

interface FormState {
  username: string;
  password: string;
  role: Role;
  village_ids: number[];
  organization_id: number | '';
  /** 사용 기간(일). '' 는 무기한. */
  valid_days: number | '';
}

//: 계정 사용 기간(문제점 26번). 1~30일, 기본 15일.
const VALID_DAYS_DEFAULT = 15;
const VALID_DAYS_MIN = 1;
const VALID_DAYS_MAX = 30;

const EMPTY: FormState = {
  username: '',
  password: '',
  role: 'village_admin',
  village_ids: [],
  organization_id: '',
  valid_days: VALID_DAYS_DEFAULT,
};

/** 만료 표시. 지난 계정은 눈에 띄게 한다 — 로그인이 이미 막혀 있다. */
function ExpiryCell({ at }: { at: string | null }) {
  if (at === null) return <span className="dim">무기한</span>;
  const left = Math.ceil((new Date(at).getTime() - Date.now()) / 86_400_000);
  const date = new Date(at).toLocaleDateString('ko-KR');
  if (left <= 0) return <span className="badge badge--warn">만료됨 · {date}</span>;
  return (
    <span className={left <= 3 ? 'badge badge--warn' : 'dim'}>
      {date} ({left}일 남음)
    </span>
  );
}

export function UsersPage() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<User[]>([]);
  const [villages, setVillages] = useState<Village[]>([]);
  const [orgs, setOrgs] = useState<Organization[]>([]);
  // 내가 만들 수 있는 역할 — 나보다 낮은 계층 전부.
  const roleOptions = me ? manageableRoles(me.role) : [];
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [form, setForm] = useState<FormState | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);

  const fail = (err: unknown, fallback: string) =>
    setError(err instanceof ApiError ? err.message : fallback);

  const load = useCallback(async () => {
    try {
      const [u, v, o] = await Promise.all([
        api.users.list(),
        api.villages.list(),
        api.organizations.list(),
      ]);
      setUsers(u);
      setVillages(v);
      setOrgs(o);
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
          village_ids: form.role === 'village_admin' ? form.village_ids : [],
          organization_id: isOrgRole(form.role) && form.organization_id !== '' ? form.organization_id : null,
          valid_days: form.valid_days === '' ? null : form.valid_days,
        });
      } else {
        // 비밀번호는 입력했을 때만 보낸다 — 빈 문자열을 보내면 초기화돼 버린다.
        // 기간은 보낼 때마다 오늘부터 다시 센다 — 수정 화면에서 저장하면 연장이다.
        await api.users.update(editingId, {
          ...(form.password ? { password: form.password } : {}),
          role: form.role,
          village_ids: form.role === 'village_admin' ? form.village_ids : [],
          organization_id: isOrgRole(form.role) && form.organization_id !== '' ? form.organization_id : null,
          valid_days: form.valid_days === '' ? null : form.valid_days,
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
    (form.password === '' || form.password.length >= 8) &&
    // 기관형 역할은 기관이 있어야 범위가 생긴다.
    (!isOrgRole(form.role) || form.organization_id !== '');

  // 역할에 맞는 수준의 기관만 고른다 — 시·도 관리자는 시·도 기관, 시·군 관리자는 시·군 기관.
  const orgOptions = form
    ? orgs.filter((o) => o.level === (form.role === 'sido_admin' ? 'sido' : 'sigungu'))
    : [];

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
                <th>범위</th>
                <th>사용 기간</th>
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
                      {ROLE_LABEL[u.role]}
                    </span>
                  </td>
                  <td>
                    {u.role === 'super_admin' ? (
                      <span className="dim">전체</span>
                    ) : isOrgRole(u.role) ? (
                      u.organization_name ?? <span className="badge badge--warn">소속 기관 없음</span>
                    ) : u.village_ids.length === 0 ? (
                      <span className="badge badge--warn">담당 마을 없음</span>
                    ) : (
                      villageNames(u.village_ids)
                    )}
                  </td>
                  <td>
                    <ExpiryCell at={u.expires_at} />
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
                          organization_id: u.organization_id ?? '',
                          valid_days: u.expires_at === null ? '' : VALID_DAYS_DEFAULT,
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
            <label htmlFor="u-days">사용 기간</label>
            <select
              id="u-days"
              value={form.valid_days}
              onChange={(e) =>
                setForm({
                  ...form,
                  valid_days: e.target.value === '' ? '' : Number(e.target.value),
                })
              }
            >
              {Array.from({ length: VALID_DAYS_MAX - VALID_DAYS_MIN + 1 }, (_, i) => i + VALID_DAYS_MIN).map(
                (d) => (
                  <option key={d} value={d}>
                    {d}일
                  </option>
                ),
              )}
              <option value="">무기한</option>
            </select>
            <p className="hint">
              {form.valid_days === ''
                ? '기간이 끝나지 않습니다. 상시 운영 계정에만 쓰세요.'
                : `오늘부터 ${form.valid_days}일간 쓸 수 있고, 그다음 날부터 계정이 자동으로 삭제됩니다.`}
              {editingId !== null && ' 저장하면 오늘부터 다시 셉니다.'}
            </p>
          </div>

          <div className="field">
            <label htmlFor="u-role">역할</label>
            <select
              id="u-role"
              value={form.role}
              onChange={(e) =>
                // 역할이 바뀌면 소속도 다시 고른다 — 시·도 기관을 시·군 관리자에게 줄 수 없다.
                setForm({ ...form, role: e.target.value as Role, organization_id: '' })
              }
            >
              {roleOptions.map((r) => (
                <option key={r} value={r}>
                  {ROLE_LABEL[r]}
                </option>
              ))}
            </select>
          </div>

          {isOrgRole(form.role) && (
            <div className="field">
              <label htmlFor="u-org">소속 기관</label>
              <select
                id="u-org"
                value={form.organization_id}
                onChange={(e) =>
                  setForm({
                    ...form,
                    organization_id: e.target.value === '' ? '' : Number(e.target.value),
                  })
                }
              >
                <option value="">선택하세요</option>
                {orgOptions.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                    {o.parent_name ? ` (${o.parent_name})` : ''}
                  </option>
                ))}
              </select>
              {orgOptions.length === 0 ? (
                <p className="hint hint--warn">
                  고를 수 있는 {form.role === 'sido_admin' ? '시·도' : '시·군'} 기관이 없습니다. 기관 관리에서 먼저 만드세요.
                </p>
              ) : (
                <p className="hint">
                  {form.role === 'sido_admin'
                    ? '이 시·도 아래 시·군 기관들의 마을을 모두 봅니다.'
                    : '이 기관에 소속된 마을을 모두 봅니다. 주소가 아니라 마을에 지정한 소속 기준입니다.'}
                </p>
              )}
            </div>
          )}

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
