/**
 * 트리 아이콘 — 지역 관리 탐색기의 폴더(기관)·핀(마을) 그림을 방송하기·OTA·마을 현황·계정의 트리도
 * 같이 쓴다(문제점 69번, 2026-10-09). 한 그림이면 어디서든 「기관은 폴더, 마을은 핀」으로 읽힌다.
 */

export function FolderIcon({ open }: { open: boolean }) {
  return (
    <svg className="trow__svg" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      {open ? (
        <path
          d="M1.5 3.5A1 1 0 0 1 2.5 2.5h3.2l1.3 1.5h5.5a1 1 0 0 1 1 1V6H3.4a1 1 0 0 0-.95.68L1.5 9.5v-6zm.4 9.5 1.4-5.2a.5.5 0 0 1 .48-.36h11.1l-1.5 5.2a.5.5 0 0 1-.48.36H1.9z"
          fill="currentColor"
        />
      ) : (
        <path
          d="M1.5 3.5A1 1 0 0 1 2.5 2.5h3.2l1.3 1.5h5.5a1 1 0 0 1 1 1v7.5a1 1 0 0 1-1 1h-10a1 1 0 0 1-1-1v-9z"
          fill="currentColor"
        />
      )}
    </svg>
  );
}

export function VillageIcon() {
  return (
    <svg className="trow__svg" viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path
        d="M8 1.5c-2.5 0-4.5 2-4.5 4.5 0 3.2 4.5 8.5 4.5 8.5s4.5-5.3 4.5-8.5c0-2.5-2-4.5-4.5-4.5zm0 6.2a1.7 1.7 0 1 1 0-3.4 1.7 1.7 0 0 1 0 3.4z"
        fill="currentColor"
      />
    </svg>
  );
}

export function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      className={`trow__chev${open ? ' is-open' : ''}`}
      viewBox="0 0 16 16"
      width="12"
      height="12"
      aria-hidden="true"
    >
      <path d="M6 3.5 10.5 8 6 12.5" fill="none" stroke="currentColor" strokeWidth="1.8" />
    </svg>
  );
}

/** 「전체 펼치기 · 전체 접기」 — 모든 트리 위에 같은 모양으로(문제점 69번). */
export function TreeExpandButtons({ onExpand, onCollapse }: { onExpand: () => void; onCollapse: () => void }) {
  return (
    <span className="tree-tools" role="group" aria-label="트리 펼치기·접기">
      <button type="button" className="btn btn--sm btn--ghost" onClick={onExpand}>
        전체 펼치기
      </button>
      <button type="button" className="btn btn--sm btn--ghost" onClick={onCollapse}>
        전체 접기
      </button>
    </span>
  );
}
