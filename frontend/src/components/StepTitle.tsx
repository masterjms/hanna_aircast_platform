/**
 * 단계 제목 — 방송하기의 「① 어떻게 방송할까요?」 모양. OTA 관리도 같은 모양을 쓴다(문제점 53번).
 * 숫자 동그라미는 끝난 단계면 ✓ 로 바뀐다.
 */

import type { ReactNode } from 'react';

export function StepTitle({ n, done, children }: { n: number; done?: boolean; children: ReactNode }) {
  return (
    <h2 className="bc-step__title">
      <span className={`bc-step__num${done ? ' is-done' : ''}`} aria-hidden="true">
        {done ? '✓' : n}
      </span>
      {children}
    </h2>
  );
}
