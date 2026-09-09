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
  /**
   * 폭. 기본은 460px 로 폼 한 줄짜리에 맞춘다.
   *   mid   620px — 문장·확인처럼 조금 넓어야 읽히는 것
   *   wide  920px — 2단 위자드
   * 내용이 2단인데 좁은 모달에 넣으면 각 칸이 200px 밑으로 떨어져 전부 줄바꿈된다.
   */
  size?: 'default' | 'mid' | 'wide';
}

const SIZE_CLASS: Record<NonNullable<ModalProps['size']>, string> = {
  default: '',
  mid: ' modal--mid',
  wide: ' modal--wide',
};

export function Modal({ title, onClose, children, footer, size = 'default' }: ModalProps) {
  const cardRef = useRef<HTMLDivElement>(null);

  // onClose 는 호출부에서 인라인 화살표로 넘어와 렌더링마다 새 함수다.
  // effect 의존성에 그대로 두면 글자를 칠 때마다 effect 가 다시 돌아서
  // 아래 포커스 이동이 매번 실행된다 — 입력 중 커서를 빼앗긴다.
  // ref 에 담아 두고 effect 는 마운트에 한 번만 걸리게 한다.
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCloseRef.current();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  useEffect(() => {
    // 열릴 때 첫 입력으로 커서를 보낸다. 바로 타이핑할 수 있어야 한다.
    //
    // 입력 요소를 먼저 찾는다. button 까지 한 번에 찾으면 DOM 순서상 머리말의
    // '닫기' 버튼이 잡혀서, 이어지는 스페이스·엔터가 모달을 닫아버린다.
    const card = cardRef.current;
    const target =
      card?.querySelector<HTMLElement>('input, select, textarea') ??
      card?.querySelector<HTMLElement>('button');
    target?.focus();
  }, []);

  return (
    <div
      className="modal__backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className={`modal${SIZE_CLASS[size]}`}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        ref={cardRef}
      >
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
