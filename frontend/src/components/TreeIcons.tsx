/**
 * 트리 공통 그림·부품 (문제점 69·80번).
 *
 * 방송하기·전체 현황·OTA·예약·지역 관리·계정 관리 — 모든 트리가 같은 그림과 같은 간격을 쓴다.
 *   · 펼침 표시: 글자(▸ ▾ >)가 아니라 SVG 꺾쇠. 14px 라 점과 헷갈리지 않는다. 펼치면 90° 돈다.
 *   · 기관 = 폴더(호박색), 지역 = 핀(파랑). 그림과 이름 사이 간격은 CSS(.tnode)가 한 값으로 정한다.
 *   · 「전체 펼치기 / 전체 접기」는 버튼 하나 — 지금 상태의 반대를 보여 준다(문제점 85번 ③).
 * 쓰지 않는 그림은 빼도 된다(사양에 따라). 쓰면 반드시 이 파일의 것을 쓴다.
 */

export function FolderIcon({ open }: { open: boolean }) {
  return (
    <svg className="trow__svg" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
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
    <svg className="trow__svg" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true">
      <path
        d="M8 1.5c-2.5 0-4.5 2-4.5 4.5 0 3.2 4.5 8.5 4.5 8.5s4.5-5.3 4.5-8.5c0-2.5-2-4.5-4.5-4.5zm0 6.2a1.7 1.7 0 1 1 0-3.4 1.7 1.7 0 0 1 0 3.4z"
        fill="currentColor"
      />
    </svg>
  );
}

/** 펼침 꺾쇠. 접힘 = ›, 펼침 = ⌄ (같은 그림을 90° 돌린다). */
export function Chevron({ open }: { open: boolean }) {
  return (
    <svg className={`trow__chev${open ? ' is-open' : ''}`} viewBox="0 0 16 16" width="14" height="14" aria-hidden="true">
      <path d="M5.5 2.5 11 8l-5.5 5.5" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/**
 * 트리 한 줄 앞의 펼침 칸. onClick 이 있으면 누르는 버튼, 없으면 같은 폭의 빈칸(세로줄 맞춤).
 * 모든 트리가 이 칸 하나로 같은 폭·같은 그림을 쓴다(문제점 80번).
 */
export function TreeCaret({
  open = false,
  onClick,
  label,
}: {
  open?: boolean;
  onClick?: () => void;
  label?: string;
}) {
  if (!onClick) return <span className="tcaret tcaret--none" aria-hidden="true" />;
  return (
    <button
      type="button"
      className="tcaret"
      aria-label={label ?? (open ? '접기' : '펼치기')}
      aria-expanded={open}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
    >
      <Chevron open={open} />
    </button>
  );
}

/**
 * 「전체 펼치기」 또는 「전체 접기」 — 버튼 하나(문제점 85번 ③). 전부 펼쳐져 있으면 접기, 아니면 펼치기.
 */
export function TreeToggleButton({
  allOpen,
  onExpand,
  onCollapse,
}: {
  allOpen: boolean;
  onExpand: () => void;
  onCollapse: () => void;
}) {
  return (
    <button
      type="button"
      className="btn btn--sm btn--ghost tree-toggle"
      onClick={allOpen ? onCollapse : onExpand}
      aria-label={allOpen ? '전체 접기' : '전체 펼치기'}
    >
      <Chevron open={!allOpen} />
      {allOpen ? '전체 접기' : '전체 펼치기'}
    </button>
  );
}
