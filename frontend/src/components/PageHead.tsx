/**
 * 화면 머리(문제점 96~100번, 2026-10-17) — 모든 관리 화면이 같은 자리에 같은 모양.
 *
 *   [제목 ?] [주 버튼들]        ← 버튼(추가·등록·저장)은 늘 제목 바로 옆, 왼쪽부터
 *   설명 한 줄(개수 등)
 *
 * 버튼 글자에 「+」 같은 기호를 붙이지 않는다(96번 ④). 방송하기·전체 현황처럼 한 화면에 맞춘 운영 화면은 쓰지 않는다.
 */

import type { ReactNode } from 'react';

export function PageHead({
  title,
  help,
  actions,
  children,
}: {
  title: ReactNode;
  /** 제목 옆 「?」 도움말(HelpTip) */
  help?: ReactNode;
  /** 제목 바로 옆에 놓을 버튼들 */
  actions?: ReactNode;
  /** 제목 아래 설명 한 줄 */
  children?: ReactNode;
}) {
  return (
    <div className="page-head">
      <div className="page-head__titlerow">
        <h1>
          {title}
          {help}
        </h1>
        {actions}
      </div>
      {children && <p>{children}</p>}
    </div>
  );
}
