/**
 * 되돌릴 수 없는 일의 마지막 확인 (문제점 83·84번, 2026-10-12).
 *
 * 단말 삭제 창(끝 4자리 입력)과 같은 모양으로 — 무엇이 없어지는지 한 줄, 경고 한 줄, 그리고
 *   · requireCode: 화면에 띄운 네 자리 숫자를 그대로 입력(실수로 누르는 것을 막는다)
 *   · 내 비밀번호: 지금 로그인한 계정의 비밀번호(자리를 비운 사이 다른 사람이 누르는 것을 막는다)
 * 둘 다 맞아야 버튼이 눌린다. 비밀번호는 서버가 확인한다(X-Confirm-Password). 연속 실패 제한은 없다.
 *
 * 파일 삭제·계정 추가·계정 삭제가 같이 쓴다. 새 확인이 필요하면 새 창을 만들지 말고 이것을 쓴다.
 */

import { useMemo, useState, type ReactNode } from 'react';

import { ApiError } from '../api/client';
import { Modal } from './Modal';

export function ConfirmDialog({
  title,
  target,
  warning,
  requireCode = false,
  confirmLabel,
  danger = false,
  onConfirm,
  onClose,
}: {
  title: string;
  /** 무엇에 대한 확인인지 — 파일 이름, 계정 아이디 등 */
  target: ReactNode;
  /** 결과 안내(되돌릴 수 없음 등) */
  warning?: ReactNode;
  requireCode?: boolean;
  confirmLabel: string;
  danger?: boolean;
  /** 비밀번호를 받아 실제 일을 한다. 실패하면 던진다 — 창이 사유를 보여 주고 열린 채 남는다. */
  onConfirm: (password: string) => Promise<void>;
  onClose: () => void;
}) {
  const code = useMemo(() => String(Math.floor(1000 + Math.random() * 9000)), []);
  const [typed, setTyped] = useState('');
  const [password, setPassword] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const ready = (!requireCode || typed.trim() === code) && password.length > 0 && !busy;

  const run = async () => {
    if (!ready) return;
    setBusy(true);
    setError(null);
    try {
      await onConfirm(password);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError || e instanceof Error ? e.message : '처리하지 못했습니다.');
      setBusy(false);
    }
  };

  return (
    <Modal
      title={title}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn btn--ghost" onClick={onClose} disabled={busy}>
            취소
          </button>
          <button
            type="button"
            className={`btn ${danger ? 'btn--danger' : 'btn--primary'}`}
            onClick={() => void run()}
            disabled={!ready}
          >
            {busy ? '처리 중…' : confirmLabel}
          </button>
        </>
      }
    >
      <p className="strong" style={{ marginTop: 0, wordBreak: 'break-all' }}>
        {target}
      </p>
      {warning && <p className="hint hint--warn">{warning}</p>}
      {requireCode && (
        <div className="field">
          <label htmlFor="cf-code">
            확인을 위해 숫자 <span className="confirm-code">{code}</span> 를 입력하세요
          </label>
          <input
            id="cf-code"
            inputMode="numeric"
            autoComplete="off"
            maxLength={4}
            value={typed}
            onChange={(e) => setTyped(e.target.value.replace(/[^0-9]/g, ''))}
          />
        </div>
      )}
      <div className="field">
        <label htmlFor="cf-pw">내 비밀번호 (지금 로그인한 계정)</label>
        <input
          id="cf-pw"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && void run()}
        />
      </div>
      {error && <p className="hint hint--warn">{error}</p>}
    </Modal>
  );
}
