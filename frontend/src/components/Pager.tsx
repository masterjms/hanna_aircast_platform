/**
 * 목록 공통 부품 — 「10·20·50건씩」 선택과 「처음·이전·다음·끝」 (문제점 64번, 2026-10-09).
 *
 * 방송 기록이 먼저 썼고, 마을 현황 이상 단말·방송 자료·단말 관리·OTA 진행·이력이 같은 부품을 쓴다.
 * 서버가 쪽을 나누는 화면(방송 기록·로그인 기록)은 PagerBar/PageSizeSelect 만, 한 번에 다 받아
 * 화면에서 나누는 목록은 usePager 까지 쓴다.
 */

import { useMemo, useState } from 'react';

export const PAGE_SIZES = [10, 20, 50] as const;
export type PageSize = (typeof PAGE_SIZES)[number];

export function PageSizeSelect({ value, onChange }: { value: PageSize; onChange: (s: PageSize) => void }) {
  return (
    <select value={value} onChange={(e) => onChange(Number(e.target.value) as PageSize)} aria-label="한 쪽에 보일 줄 수">
      {PAGE_SIZES.map((s) => (
        <option key={s} value={s}>
          {s}건씩
        </option>
      ))}
    </select>
  );
}

export function PagerBar({
  total,
  page,
  pages,
  onPage,
  unit = '건',
}: {
  total: number;
  page: number;
  pages: number;
  onPage: (p: number) => void;
  unit?: string;
}) {
  return (
    <div className="pager">
      <span className="dim">
        총 {total}
        {unit} · {page}/{pages}쪽
      </span>
      <span className="filters__spacer" />
      <button type="button" className="btn btn--sm" disabled={page <= 1} onClick={() => onPage(1)}>
        처음
      </button>
      <button type="button" className="btn btn--sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        이전
      </button>
      <button type="button" className="btn btn--sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        다음
      </button>
      <button type="button" className="btn btn--sm" disabled={page >= pages} onClick={() => onPage(pages)}>
        끝
      </button>
    </div>
  );
}

/** 화면에서 나누는 목록. 항목 수가 줄어 쪽이 사라지면 마지막 쪽으로 간다. */
export function usePager<T>(items: readonly T[], initialSize: PageSize = 10) {
  const [size, setSizeState] = useState<PageSize>(initialSize);
  const [page, setPage] = useState(1);
  const pages = Math.max(1, Math.ceil(items.length / size));
  const safePage = Math.min(page, pages);
  const rows = useMemo(() => items.slice((safePage - 1) * size, safePage * size), [items, safePage, size]);
  const setSize = (s: PageSize) => {
    setSizeState(s);
    setPage(1);
  };
  return { size, setSize, page: safePage, setPage, pages, rows, total: items.length };
}
