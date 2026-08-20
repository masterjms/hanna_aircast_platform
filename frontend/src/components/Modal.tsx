/**
 * 모달.
 *
 * 라이브러리를 쓰지 않는다. 필요한 건 배경 클릭 · ESC 닫기 · 포커스 이동 정도라
 * 직접 쓰는 편이 의존성보다 싸다.
 */

import { useEffect, useRef, type ReactNode } from 'react';

interface ModalProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** 하단 버튼 영역. 없으면 렌더링하지 않는다. */
  footer?: ReactNode;
}

export function Modal({ title, onClose, children, footer }: ModalProps) {
  const cardRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    // 열릴 때 첫 입력으로 커서를 보낸다. 바로 타이핑할 수 있어야 한다.
    cardRef.current?.querySelector<HTMLElement>('input, select, textarea, button')?.focus();
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div
      className="modal__backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} ref={cardRef}>
        <div className="modal__head">
          <h2>{title}</h2>
          <button type="button" className="btn btn--ghost" onClick={onClose} aria-label="닫기">
            닫기
          </button>
        </div>
        <div className="modal__body">{children}</div>
        {footer && <div className="modal__foot">{footer}</div>}
      </div>
    </div>
  );
}
