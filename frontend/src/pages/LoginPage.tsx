import { useState, type FormEvent } from 'react';

import { ApiError } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { Logo } from '../components/Logo';

export function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(username, password);
      // 성공하면 App 의 라우팅이 알아서 대시보드로 바꾼다.
    } catch (err) {
      setError(err instanceof ApiError ? err.message : '로그인 중 문제가 발생했습니다.');
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
          <h1 className="login__title">마을방송 관제 로그인</h1>
          <p className="login__sub">운영서버 관리자 계정으로 접속합니다.</p>

          {error && (
            <div className="alert" style={{ marginBottom: 14 }} role="alert">
              {error}
            </div>
          )}

          <div className="field">
            <label htmlFor="username">아이디</label>
            <input
              id="username"
              type="text"
              autoComplete="username"
              autoFocus
              required
              value={username}
              onChange={(e) => setUsername(e.target.value)}
            />
          </div>

          <div className="field">
            <label htmlFor="password">비밀번호</label>
            <input
              id="password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>

          <button type="submit" className="btn btn--primary btn--block" disabled={busy}>
            {busy ? '확인 중…' : '로그인'}
          </button>

          <div className="login__foot">HANNA ELECTRONICS · 마을방송 운영서버</div>
        </form>
      </div>
    </div>
  );
}
