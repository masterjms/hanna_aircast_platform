/**
 * 임시 비밀번호로 들어온 계정의 비밀번호 변경(향후검토 10번).
 *
 * 관리자가 임시 비밀번호를 발급하면 그 계정은 새 비밀번호를 정하기 전까지 이 화면만
 * 쓴다. 서버도 다른 API 를 막는다(PASSWORD_CHANGE_REQUIRED) — 화면만의 제약이 아니다.
 */

import { useState, type FormEvent } from 'react';

import { ApiError, api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { Logo } from '../components/Logo';

export function ChangePasswordPage() {
  const { user, refresh, logout } = useAuth();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const mismatch = confirm !== '' && next !== confirm;
  const ready = current !== '' && next.length >= 8 && next.length <= 64 && next === confirm;

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      await api.auth.changePassword(current, next);
      await refresh(); // must_change_password 가 풀리면 App 이 원래 화면으로 보낸다
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '비밀번호를 바꾸지 못했습니다.');
      setBusy(false);
    }
  };

  return (
    <div className="login">
      <div className="login__inner">
        <div className="login__brand">
          <Logo size={30} />
        </div>

        <form className="login__card" onSubmit={onSubmit}>
          <h1 className="login__title">새 비밀번호 정하기</h1>
          <p className="login__sub">
            {user?.username} 계정은 임시 비밀번호로 들어왔습니다. 계속하려면 새 비밀번호를
            정해 주세요.
          </p>

          {error && (
            <div className="alert" style={{ marginBottom: 14 }} role="alert">
              {error}
            </div>
          )}

          <div className="field">
            <label htmlFor="cp-current">임시 비밀번호</label>
            <input
              id="cp-current"
              type="password"
              autoComplete="current-password"
              autoFocus
              required
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
            />
          </div>

          <div className="field">
            <label htmlFor="cp-new">새 비밀번호</label>
            <input
              id="cp-new"
              type="password"
              autoComplete="new-password"
              required
              value={next}
              onChange={(e) => setNext(e.target.value)}
            />
            <p className="hint">8자 이상 64자 이하</p>
          </div>

          <div className="field">
            <label htmlFor="cp-confirm">새 비밀번호 확인</label>
            <input
              id="cp-confirm"
              type="password"
              autoComplete="new-password"
              required
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
            {mismatch && <p className="hint hint--warn">새 비밀번호와 다릅니다.</p>}
          </div>

          <button type="submit" className="btn btn--primary btn--block" disabled={busy || !ready}>
            {busy ? '바꾸는 중…' : '비밀번호 바꾸기'}
          </button>
          <button
            type="button"
            className="btn btn--ghost btn--block"
            style={{ marginTop: 8 }}
            onClick={() => void logout()}
          >
            로그아웃
          </button>
        </form>
      </div>
    </div>
  );
}
