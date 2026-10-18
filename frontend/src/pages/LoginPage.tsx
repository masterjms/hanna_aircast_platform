/**
 * 로그인(문제점 89번, 2026-10-15) — 태양광 관제 로그인과 같은 꾸밈: 그림 바탕 위 유리 상자,
 * 칸 안에서 떠오르는 이름표, 비밀번호 보기, 틀리면 상자 흔들기, 아래 서버 상태 한 줄.
 * 바탕은 IoT 지역 방송 그림(login/LoginBackdrop).
 *
 * 「로그인 유지」는 넣지 않는다 — 공공 납품 요건으로 30분 미사용 시 자동 로그아웃한다(문제점 70번 ③).
 */

import { useEffect, useRef, useState, type FormEvent } from 'react';

import { ApiError } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { Logo } from '../components/Logo';
import { LegalLinks } from './legal/LegalLayout';
import { LoginBackdrop } from './login/LoginBackdrop';

export function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [showPw, setShowPw] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hint, setHint] = useState(false);
  const card = useRef<HTMLFormElement | null>(null);
  const [notice] = useState<string | null>(() => {
    // 자동 로그아웃 사유(문제점 70번 ③). 지우기는 아래 effect 에서 — 초기화 함수에서 지우면 개발 모드의
    // 두 번 호출에 메시지가 사라진다.
    try {
      return sessionStorage.getItem('xwifi.logoutReason');
    } catch {
      return null;
    }
  });
  useEffect(() => {
    try {
      sessionStorage.removeItem('xwifi.logoutReason');
    } catch {
      /* 무시 */
    }
  }, []);

  // 서버 상태 한 줄 — 로그인 전에도 열린 /health. DB·MQTT 중 하나라도 죽으면 degraded.
  const [srv, setSrv] = useState<'ok' | 'degraded' | 'down' | null>(null);
  useEffect(() => {
    let alive = true;
    const check = () =>
      fetch('/health', { cache: 'no-store' })
        .then((r) => (r.ok ? r.json() : Promise.reject(r)))
        .then((b: { status?: string }) => alive && setSrv(b.status === 'ok' ? 'ok' : 'degraded'))
        .catch(() => alive && setSrv('down'));
    void check();
    const t = window.setInterval(check, 30_000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);

  // 틀리면 상자를 흔든다 — 다시 그리지 않고 애니메이션만 다시 건다(입력 칸 포커스 유지).
  const fail = (m: string) => {
    setError(m);
    const el = card.current;
    if (!el) return;
    el.classList.remove('lg-shake');
    void el.offsetWidth;
    el.classList.add('lg-shake');
  };

  // 2단계 인증(문제점 70번 2항) — 비밀번호가 맞고 그 계정이 OTP 를 켰으면 서버가 OTP_REQUIRED 로 답한다.
  // 그때 6자리 칸을 열고, 같은 아이디·비밀번호에 숫자를 붙여 다시 보낸다.
  const [otpStep, setOtpStep] = useState(false);
  const [otp, setOtp] = useState('');
  const [useRecovery, setUseRecovery] = useState(false);
  const otpRef = useRef<HTMLInputElement | null>(null);

  const onSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return fail('아이디와 비밀번호를 넣으세요.');
    if (otpStep && !otp.trim()) return fail(useRecovery ? '복구 코드를 넣으세요.' : '인증 번호 6자리를 넣으세요.');
    setBusy(true);
    setError(null);
    try {
      await login(username.trim(), password, otpStep ? otp.trim() : undefined);
      // 성공하면 App 의 라우팅이 알아서 대시보드로 바꾼다.
    } catch (err) {
      if (err instanceof ApiError && err.code === 'OTP_REQUIRED') {
        setOtpStep(true);
        setBusy(false);
        setTimeout(() => otpRef.current?.focus(), 50);
        return;
      }
      if (err instanceof ApiError && err.code === 'OTP_INVALID') setOtp('');
      fail(
        err instanceof ApiError
          ? err.status === 429
            ? '시도가 너무 많습니다 — 1분 뒤 다시 하세요.'
            : err.message
          : '서버에 연결할 수 없습니다 — 잠시 뒤 다시 시도하세요.',
      );
      setBusy(false);
    }
  };

  const status =
    srv === 'down'
      ? '서버에 연결할 수 없습니다 — 잠시 뒤 다시 시도하세요'
      : srv === 'degraded'
        ? '서버 일부 기능 점검 중 — 방송이 늦을 수 있습니다'
        : srv === null
          ? '서버 확인 중…'
          : '시스템 정상 운영 중';

  return (
    <div className="lg-page">
      <LoginBackdrop />
      <main className="lg-main">
        <form ref={card} className="lg-card" onSubmit={onSubmit} noValidate>
          <div className="lg-brand">
            <Logo size={34} />
            <h1>IoT 지역 방송 관제 시스템</h1>
            <p>VILLAGE BROADCAST CONTROL CENTER</p>
          </div>

          {notice && !error && <div className="lg-note">{notice}</div>}

          {otpStep ? (
            <>
              <div className="lg-note">
                <strong>{username.trim()}</strong> — 휴대폰 OTP 앱의 {useRecovery ? '대신 복구 코드(XXXX-XXXX)를' : '인증 번호 6자리를'}{' '}
                넣어 주세요.
              </div>
              <div className="lg-field">
                <input
                  id="lg-otp"
                  ref={otpRef}
                  value={otp}
                  placeholder=" "
                  inputMode={useRecovery ? 'text' : 'numeric'}
                  autoComplete="one-time-code"
                  maxLength={useRecovery ? 9 : 7}
                  onChange={(e) => setOtp(useRecovery ? e.target.value.toUpperCase() : e.target.value.replace(/[^\d ]/g, ''))}
                />
                <label htmlFor="lg-otp">{useRecovery ? '복구 코드' : '인증 번호'}</label>
                <IconLock />
              </div>
              <div className="lg-row">
                <button
                  type="button"
                  className="lg-link"
                  onClick={() => {
                    setUseRecovery((v) => !v);
                    setOtp('');
                    setTimeout(() => otpRef.current?.focus(), 50);
                  }}
                >
                  {useRecovery ? '인증 번호로 넣기' : '휴대폰이 없어요(복구 코드)'}
                </button>
                <button
                  type="button"
                  className="lg-link"
                  onClick={() => {
                    setOtpStep(false);
                    setOtp('');
                    setUseRecovery(false);
                    setPassword('');
                    setError(null);
                  }}
                >
                  다른 아이디로
                </button>
              </div>
            </>
          ) : (
          <>
          <div className="lg-field">
            <input
              id="lg-uid"
              value={username}
              placeholder=" "
              autoComplete="username"
              autoCapitalize="none"
              autoCorrect="off"
              spellCheck={false}
              autoFocus
              onChange={(e) => setUsername(e.target.value)}
            />
            <label htmlFor="lg-uid">아이디</label>
            <IconUser />
          </div>
          <div className="lg-field">
            <input
              id="lg-pw"
              type={showPw ? 'text' : 'password'}
              value={password}
              placeholder=" "
              autoComplete="current-password"
              onChange={(e) => setPassword(e.target.value)}
            />
            <label htmlFor="lg-pw">비밀번호</label>
            <IconLock />
            <button
              type="button"
              className="lg-toggle"
              onClick={() => setShowPw((v) => !v)}
              aria-label={showPw ? '비밀번호 숨기기' : '비밀번호 보기'}
            >
              {showPw ? <IconEyeOff /> : <IconEye />}
            </button>
          </div>

          <div className="lg-row">
            <span>5회 틀리면 30분 잠깁니다</span>
            <button type="button" className="lg-link" onClick={() => setHint((v) => !v)} aria-expanded={hint}>
              비밀번호 찾기
            </button>
          </div>
          {hint && (
            <div className="lg-hint">
              비밀번호는 상위 관리자가 계정 관리에서 임시 비밀번호로 다시 발급합니다 — 관리자에게 문의하세요.
            </div>
          )}
          </>
          )}
          {error && (
            <div className="lg-err" role="alert">
              {error}
            </div>
          )}

          <button className={`lg-btn${busy ? ' loading' : ''}`} type="submit" disabled={busy}>
            <span className="label">{otpStep ? '확인' : '로그인'}</span>
            <span className="spin" />
          </button>

          <div className="lg-status">
            <span className={`lg-dot${srv === 'ok' ? '' : srv === null ? ' wait' : srv === 'degraded' ? ' warn' : ' down'}`} />
            <span>{status}</span>
          </div>
        </form>
      </main>
      <footer className="lg-foot">
        <LegalLinks className="legal-links--login" />
      </footer>
    </div>
  );
}

const svg = {
  width: 18,
  height: 18,
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true,
};
const IconUser = () => (
  <svg {...svg}>
    <circle cx="12" cy="8" r="4" />
    <path d="M4 21c0-4 3.6-7 8-7s8 3 8 7" />
  </svg>
);
const IconLock = () => (
  <svg {...svg}>
    <rect x="5" y="11" width="14" height="10" rx="2" />
    <path d="M8 11V8a4 4 0 0 1 8 0v3" />
  </svg>
);
const IconEye = () => (
  <svg {...svg}>
    <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7S2 12 2 12z" />
    <circle cx="12" cy="12" r="3" />
  </svg>
);
const IconEyeOff = () => (
  <svg {...svg}>
    <path d="M3 3l18 18" />
    <path d="M10.6 5.1A10.8 10.8 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.2 4.2M6.6 6.6C3.9 8.4 2 12 2 12s3.6 7 10 7c1.7 0 3.2-.4 4.5-1.1" />
    <path d="M9.9 9.9a3 3 0 0 0 4.2 4.2" />
  </svg>
);
