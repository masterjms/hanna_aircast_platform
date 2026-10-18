/**
 * 2단계 인증 필수 계정의 첫 등록(문제점 70번 2항, 2026-10-18).
 *
 * 역할상 필수(TOTP_REQUIRED_ROLES, 기본 최고 관리자)인데 아직 등록하지 않은 계정은 이 화면만 쓴다.
 * 서버도 등록 API 말고는 막는다(OTP_ENROLL_REQUIRED) — 화면만의 제약이 아니다.
 */

import { useAuth } from '../auth/AuthContext';
import { Logo } from '../components/Logo';
import { OtpSetup } from '../components/OtpSetup';

export function OtpEnrollPage() {
  const { user, refresh, logout } = useAuth();
  return (
    <div className="login">
      <div className="login__inner login__inner--wide">
        <div className="login__brand">
          <Logo size={30} />
        </div>
        <div className="login__card">
          <h1 className="login__title">2단계 인증 등록</h1>
          <p className="login__sub">
            {user?.username} 계정은 2단계 인증이 필수입니다. 한 번만 등록하면 다음부터는 로그인할 때 휴대폰 앱의 숫자를 함께
            넣습니다.
          </p>
          <OtpSetup onDone={() => void refresh()} />
          <button
            type="button"
            className="btn btn--ghost btn--block"
            style={{ marginTop: 14 }}
            onClick={() => void logout()}
          >
            로그아웃
          </button>
        </div>
      </div>
    </div>
  );
}
