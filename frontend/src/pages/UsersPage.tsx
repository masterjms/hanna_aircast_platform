/**
 * 계정 관리 (기관 관리자 이상) — 관리자 계층 설계 §5·§9 (v2 2026-09-12).
 *
 * 나보다 아래의 계정만 보이고 만들 수 있다. 기관 관리자는 기관(과 그 아래 전부)으로,
 * 이장은 담당 마을로 범위가 정해진다. 기관 관리자끼리는 트리 위치가 위아래다 — 같은
 * 마디의 관리자는 동료라서 소속 기관 드롭다운에 내 기관은 나오지 않는다.
 *
 * village_admin 은 담당 마을을 반드시 지정해야 의미가 있다 — 비워두면
 * 로그인은 되지만 아무것도 못 보는 계정이 된다. 화면에서 경고로 알려준다.
 */

import { useCallback, useEffect, useState } from 'react';

import { ApiError, api } from '../api/client';
import type { Organization, Role, User, Village } from '../api/types';
import { useAuth } from '../auth/AuthContext';
import { Modal } from '../components/Modal';
import { buildForest, flattenForest, pathLabel } from '../lib/orgtree';
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
  const { user: me, isSuperAdmin } = useAuth();
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
  // 방금 발급한 임시 비밀번호. 수정 창을 닫으면 버린다 — 다시 볼 방법은 없다.
  const [tempPw, setTempPw] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const closeForm = () => {
    setForm(null);
    setEditingId(null);
    setTempPw(null);
    setCopied(false);
  };

  /**
   * 임시 비밀번호 발급(향후검토 10번). 비밀번호는 해시로만 저장돼 원래 값을 보여줄 수
   * 없으므로, 새 값을 만들어 한 번만 보여주고 그 계정이 다음 로그인에서 바꾸게 한다.
   */
  const issueTempPassword = async () => {
    if (editingId === null || !form) return;
    if (
      !window.confirm(
        `${form.username} 의 비밀번호를 임시 비밀번호로 바꿉니다.\n` +
          '지금 비밀번호는 바로 쓸 수 없게 되고, 그 계정은 다음 로그인에서 새 비밀번호를 정해야 합니다.\n계속할까요?',
      )
    )
      return;
    setBusy(true);
    setError(null);
    try {
      const { password } = await api.users.tempPassword(editingId);
      setTempPw(password);
      setCopied(false);
      await load();
    } catch (err) {
      fail(err, '임시 비밀번호를 발급하지 못했습니다.');
    } finally {
      setBusy(false);
    }
  };

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
      closeForm();
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

  // 기관 드롭다운은 트리 순서(들여쓰기)로. 기관 관리자에게 자기 마디는 빼 준다 — 같은
  // 마디의 관리자를 만들면 관할이 옆으로 새서 백엔드가 거절한다(TIER_TOO_LOW).
  const orgOptions = flattenForest(buildForest(orgs, villages)).filter(
    (n) => isSuperAdmin || n.org.id !== me?.organization_id,
  );

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
                    {u.must_change_password && (
                      <span className="tag" title="임시 비밀번호를 받고 아직 바꾸지 않았습니다">
                        임시 비밀번호
                      </span>
                    )}
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
          onClose={closeForm}
          footer={
            <>
              <button type="button" className="btn" onClick={closeForm}>
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
            {editingId !== null && editingId !== me?.id && (
              <div className="temp-pw">
                {tempPw === null ? (
                  <>
                    <button
                      type="button"
                      className="btn btn--ghost btn--sm"
                      onClick={() => void issueTempPassword()}
                      disabled={busy}
                    >
                      임시 비밀번호 발급
                    </button>
                    <span className="hint">
                      비밀번호는 암호화돼 저장되어 확인할 수 없습니다. 잊었다면 임시 비밀번호를
                      발급해 전달하세요.
                    </span>
                  </>
                ) : (
                  <>
                    <div className="temp-pw__value">
                      <span className="mono">{tempPw}</span>
                      <button
                        type="button"
                        className="btn btn--ghost btn--sm"
                        onClick={() => {
                          void navigator.clipboard?.writeText(tempPw).then(() => setCopied(true));
                        }}
                      >
                        {copied ? '복사됨' : '복사'}
                      </button>
                    </div>
                    <span className="hint hint--warn">
                      이 창을 닫으면 다시 볼 수 없습니다. 담당자에게 전달하면 다음 로그인에서 새
                      비밀번호를 정하게 됩니다.
                    </span>
                  </>
                )}
              </div>
            )}
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
              onChange={(e) => setForm({ ...form, role: e.target.value as Role, organization_id: '' })}
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
                {orgOptions.map((n) => (
                  <option key={n.org.id} value={n.org.id} title={pathLabel(n)}>
                    {'\u00a0\u00a0'.repeat(n.depth)}
                    {n.depth > 0 ? '└ ' : ''}
                    {n.org.name}
                  </option>
                ))}
              </select>
              {orgOptions.length === 0 ? (
                <p className="hint hint--warn">
                  고를 수 있는 기관이 없습니다. 지역 관리에서 내 기관 아래에 기관을 먼저 만드세요.
                </p>
              ) : (
                <p className="hint">
                  이 기관과 그 아래 모든 기관의 마을을 봅니다. 나중에 붙이는 마을·기관도 자동으로
                  들어갑니다. 주소가 아니라 지역 관리 트리 기준입니다.
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
