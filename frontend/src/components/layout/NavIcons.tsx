/**
 * 왼쪽 메뉴 아이콘 (문제점 85번 ①, 2026-10-12). 선 굵기·크기를 하나로 맞춘 18px 선 아이콘.
 * 외부 아이콘 묶음을 들이지 않는다 — 열 개 남짓이라 직접 그리는 편이 싸다.
 */

const P = {
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
};

function Svg({ children }: { children: React.ReactNode }) {
  return (
    <svg className="navicon" viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
      {children}
    </svg>
  );
}

export const NAV_ICONS: Record<string, () => JSX.Element> = {
  '/': () => (
    <Svg>
      <rect x="4" y="4" width="6" height="6" rx="1.5" {...P} />
      <rect x="14" y="4" width="6" height="6" rx="1.5" {...P} />
      <rect x="4" y="14" width="6" height="6" rx="1.5" {...P} />
      <rect x="14" y="14" width="6" height="6" rx="1.5" {...P} />
    </Svg>
  ),
  '/schedules': () => (
    <Svg>
      <rect x="4" y="5" width="16" height="15" rx="2" {...P} />
      <path d="M8 3v4M16 3v4M4 10h16" {...P} />
    </Svg>
  ),
  '/files': () => (
    <Svg>
      <path d="M4 14l2.5-7h11L20 14" {...P} />
      <rect x="4" y="14" width="16" height="6" rx="1.5" {...P} />
    </Svg>
  ),
  '/events': () => (
    <Svg>
      <circle cx="12" cy="12" r="8" {...P} />
      <path d="M12 8v4l3 2" {...P} />
    </Svg>
  ),
  '/devices': () => (
    <Svg>
      <rect x="4" y="5" width="16" height="11" rx="2" {...P} />
      <path d="M9 20h6M12 16v4" {...P} />
    </Svg>
  ),
  '/ota': () => (
    <Svg>
      <path d="M7 17a4 4 0 0 1-.5-8A6 6 0 0 1 18 9a3.5 3.5 0 0 1-.5 8" {...P} />
      <path d="M12 11v9M9 17l3 3 3-3" {...P} />
    </Svg>
  ),
  '/settings': () => (
    <Svg>
      <circle cx="12" cy="12" r="3" {...P} />
      <path
        d="M12 3v2.5M12 18.5V21M3 12h2.5M18.5 12H21M5.6 5.6l1.8 1.8M16.6 16.6l1.8 1.8M5.6 18.4l1.8-1.8M16.6 7.4l1.8-1.8"
        {...P}
      />
    </Svg>
  ),
  '/regions': () => (
    <Svg>
      <path d="M12 21s-6-6.2-6-11a6 6 0 0 1 12 0c0 4.8-6 11-6 11z" {...P} />
      <circle cx="12" cy="10" r="2.2" {...P} />
    </Svg>
  ),
  '/users': () => (
    <Svg>
      <circle cx="9" cy="8.5" r="3" {...P} />
      <path d="M3.5 19c.6-3 2.8-4.5 5.5-4.5s4.9 1.5 5.5 4.5" {...P} />
      <circle cx="16.5" cy="9.5" r="2.5" {...P} />
      <path d="M15.5 14.6c2.4 0 4.3 1.3 4.9 3.9" {...P} />
    </Svg>
  ),
};
