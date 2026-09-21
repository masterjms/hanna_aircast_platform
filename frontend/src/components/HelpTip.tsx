/**
 * 「?」 도움말 — 긴 설명을 화면에서 걷어내고 필요할 때만 보인다(향후검토 4번).
 *
 * 마우스를 올리면 열리고 벗어나면 닫힌다. 태블릿에는 올리기가 없으므로 **눌러도** 열리고
 * 다시 누르거나 바깥을 누르면 닫힌다. 키보드는 Tab 으로 가서 Enter·Space, Esc 로 닫는다.
 */

import { useEffect, useId, useRef, useState, type ReactNode } from 'react';

export function HelpTip({ children, label = '도움말' }: { children: ReactNode; label?: string }) {
  const [open, setOpen] = useState(false);
  // 눌러서 연 것은 마우스가 벗어나도 닫지 않는다 — 읽는 중에 사라지면 안 된다.
  const [pinned, setPinned] = useState(false);
  const ref = useRef<HTMLSpanElement>(null);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setPinned(false);
      }
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setOpen(false);
        setPinned(false);
      }
    };
    document.addEventListener('pointerdown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('pointerdown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <span
      ref={ref}
      className="helptip"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => {
        if (!pinned) setOpen(false);
      }}
    >
      <button
        type="button"
        className="helptip__btn"
        aria-label={label}
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onClick={() => {
          const next = !(open && pinned);
          setOpen(next);
          setPinned(next);
        }}
      >
        ?
      </button>
      {open && (
        <span role="tooltip" id={id} className="helptip__pop">
          {children}
        </span>
      )}
    </span>
  );
}
