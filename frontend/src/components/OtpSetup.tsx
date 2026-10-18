/**
 * 2단계 인증(OTP) 등록 (문제점 70번 2항, 2026-10-18).
 *
 * ① 「등록 시작」 → 서버가 비밀을 만들고 QR 주소를 준다 → OTP 앱으로 QR 을 찍는다(못 찍으면 키를 직접 입력)
 * ② 앱에 뜬 6자리를 넣어 확인 → 켜짐
 * ③ 복구 코드 10개를 한 번만 보여 준다 — 휴대폰을 잃었을 때 6자리 대신 한 번씩 쓴다
 *
 * QR 은 qrcode-generator 로 그린다(외부 서비스에 비밀을 보내지 않는다 — 비밀이 담긴 주소라서).
 */

import qrcode from 'qrcode-generator';
import { useMemo, useState } from 'react';

import { ApiError, api } from '../api/client';

function QrSvg({ text }: { text: string }) {
  const cells = useMemo(() => {
    const q = qrcode(0, 'M');
    q.addData(text);
    q.make();
    const n = q.getModuleCount();
    const dark: string[] = [];
    for (let r = 0; r < n; r++) for (let c = 0; c < n; c++) if (q.isDark(r, c)) dark.push(`M${c} ${r}h1v1h-1z`);
    return { n, d: dark.join('') };
  }, [text]);
  const pad = 4;
  return (
    <svg
      className="otp-qr"
      viewBox={`${-pad} ${-pad} ${cells.n + pad * 2} ${cells.n + pad * 2}`}
      role="img"
      aria-label="OTP 앱 등록용 QR 코드"
      shapeRendering="crispEdges"
    >
      <rect x={-pad} y={-pad} width={cells.n + pad * 2} height={cells.n + pad * 2} fill="#fff" />
      <path d={cells.d} fill="#000" />
    </svg>
  );
}

export function OtpSetup({ onDone }: { onDone: () => void }) {
  const [setup, setSetup] = useState<{ secret: string; otpauth_uri: string } | null>(null);
  const [code, setCode] = useState('');
  const [codes, setCodes] = useState<string[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const start = async () => {
    setBusy(true);
    setError(null);
    try {
      setSetup(await api.auth.otpSetup());
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '등록을 시작하지 못했습니다.');
    } finally {
      setBusy(false);
    }
  };

  const enable = async () => {
    setBusy(true);
    setError(null);
    try {
      const r = await api.auth.otpEnable(code.trim());
      setCodes(r.recovery_codes);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '확인하지 못했습니다.');
    } finally {
      setBusy(false);
    }
  };

  if (codes) {
    return (
      <div className="otp-setup">
        <p className="otp-setup__ok">✓ 2단계 인증을 켰습니다. 다음 로그인부터 앱의 숫자를 함께 넣습니다.</p>
        <p>
          <strong>복구 코드 10개</strong> — 휴대폰을 잃었을 때 인증 번호 대신 한 번씩 씁니다. 지금 한 번만 보이니 종이에 적거나
          안전한 곳에 보관하세요.
        </p>
        <ol className="otp-codes mono">
          {codes.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ol>
        <div className="otp-setup__row">
          <button
            type="button"
            className="btn"
            onClick={() => {
              void navigator.clipboard?.writeText(codes.join('\n')).catch(() => undefined);
              setSaved(true);
            }}
          >
            {saved ? '복사됨 ✓' : '복사'}
          </button>
          <button type="button" className="btn btn--primary" onClick={onDone}>
            다 적었습니다
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="otp-setup">
      {error && (
        <div className="alert" role="alert">
          {error}
        </div>
      )}
      {!setup ? (
        <>
          <p>
            휴대폰에 <strong>Google Authenticator</strong> 또는 <strong>Microsoft Authenticator</strong> 같은 OTP 앱(무료)을 설치한
            뒤 「등록 시작」을 누르세요. 앱은 30초마다 바뀌는 6자리를 만들고, 휴대폰 인터넷이 꺼져 있어도 됩니다.
          </p>
          <button type="button" className="btn btn--primary" onClick={() => void start()} disabled={busy}>
            {busy ? '준비 중…' : '등록 시작'}
          </button>
        </>
      ) : (
        <>
          <div className="otp-setup__grid">
            <QrSvg text={setup.otpauth_uri} />
            <div>
              <p style={{ marginTop: 0 }}>
                1. OTP 앱에서 「+」 → 「QR 코드 스캔」으로 왼쪽 그림을 찍습니다.
                <br />
                QR 을 못 찍으면 「설정 키 입력」에 아래 키를 넣습니다(시간 기준).
              </p>
              <p className="mono otp-secret">{setup.secret.replace(/(.{4})/g, '$1 ').trim()}</p>
              <p>2. 앱에 뜬 6자리를 넣고 「확인」을 누릅니다.</p>
              <div className="field-inline">
                <input
                  className="mono"
                  inputMode="numeric"
                  autoComplete="one-time-code"
                  maxLength={7}
                  placeholder="123456"
                  value={code}
                  aria-label="인증 번호 6자리"
                  onChange={(e) => setCode(e.target.value.replace(/[^\d ]/g, ''))}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && code.replace(/\D/g, '').length === 6) void enable();
                  }}
                  autoFocus
                />
                <button
                  type="button"
                  className="btn btn--primary"
                  onClick={() => void enable()}
                  disabled={busy || code.replace(/\D/g, '').length !== 6}
                >
                  {busy ? '확인 중…' : '확인'}
                </button>
              </div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
