/**
 * 내 계정 보안 — 왼쪽 아래 계정 칸의 「보안」(문제점 70번 2항, 2026-10-18).
 *
 * 2단계 인증 상태를 보고, 꺼져 있으면 등록, 필수가 아닌 계정은 끌 수 있다(비밀번호 확인).
 * 필수 역할은 끌 수 없다 — 휴대폰을 바꾸면 다른 관리자가 초기화한 뒤 다시 등록한다.
 */

import { useState } from 'react';
import { createPortal } from 'react-dom';

import { ApiError, api } from '../api/client';
import { useAuth } from '../auth/AuthContext';
import { Modal } from './Modal';
import { OtpSetup } from './OtpSetup';

export function SecurityDialog({ onClose }: { onClose: () => void }) {
  const { user, refresh } = useAuth();
  const [setting, setSetting] = useState(false);
  const [pw, setPw] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!user) return null;

  const disable = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.auth.otpDisable(pw);
      await refresh();
      setPw('');
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '끄지 못했습니다.');
    } finally {
      setBusy(false);
    }
  };

  // 메뉴(왼쪽 메뉴·아래 탭바) 안에서 열리므로 body 로 꺼낸다 — 좁은 화면에서 왼쪽 메뉴가 숨으면 같이 숨는다.
  return createPortal(
    <Modal title="내 계정 보안" onClose={onClose} size="mid">
      <h3 className="otp-dialog__title">
        2단계 인증{' '}
        <span className={`badge ${user.otp_enabled ? 'badge--ok' : 'badge--idle'}`}>{user.otp_enabled ? '켜짐' : '꺼짐'}</span>
        {user.otp_required && <span className="dim"> · 이 계정은 필수</span>}
      </h3>
      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}
      {user.otp_enabled ? (
        user.otp_required ? (
          <p className="hint">
            로그인할 때 휴대폰 OTP 앱의 숫자를 함께 넣습니다. 휴대폰을 바꾸거나 잃었으면 복구 코드로 들어오거나, 다른 최고
            관리자에게 계정 관리의 「2단계 인증 초기화」를 부탁하세요.
          </p>
        ) : (
          <div className="field">
            <label htmlFor="otp-off-pw">끄려면 내 비밀번호</label>
            <div className="field-inline">
              <input id="otp-off-pw" type="password" value={pw} onChange={(e) => setPw(e.target.value)} />
              <button type="button" className="btn btn--danger" onClick={() => void disable()} disabled={busy || !pw}>
                2단계 인증 끄기
              </button>
            </div>
          </div>
        )
      ) : setting ? (
        <OtpSetup
          onDone={() => {
            setSetting(false);
            void refresh();
          }}
        />
      ) : (
        <>
          <p>비밀번호가 새어도 내 휴대폰 없이는 들어올 수 없게 합니다. 로그인할 때 휴대폰 OTP 앱의 6자리를 함께 넣습니다.</p>
          <button type="button" className="btn btn--primary" onClick={() => setSetting(true)}>
            2단계 인증 켜기
          </button>
        </>
      )}
    </Modal>,
    document.body,
  );
}
