/**
 * HANNA AirCast 로고.
 *
 * 'H' 자를 세 개의 막대로 세운 마크다. 왼쪽 기둥만 위아래로 갈라
 * 위는 빨강(ON AIR), 아래는 파랑(정상 송출)으로 둔다 — 이 시스템이 다루는
 * 두 가지 상태가 마크 자체에 들어가 있다.
 */

interface LogoProps {
  /** 마크 한 변의 px. 워드마크 크기도 여기에 맞춰 따라간다. */
  size?: number;
  /** HANNA / AIRCAST 워드마크를 함께 그릴지. 아이콘만 필요하면 false. */
  wordmark?: boolean;
}

export function LogoMark({ size = 26 }: { size?: number }) {
  return (
    <svg
      viewBox="0 0 32 32"
      width={size}
      height={size}
      role="img"
      aria-label="HANNA AirCast"
      style={{ flex: 'none' }}
    >
      <rect x="4" y="3" width="7" height="11" fill="var(--brand-red)" />
      <rect x="4" y="18" width="7" height="11" fill="var(--brand-blue)" />
      <rect x="21" y="3" width="7" height="26" fill="var(--text)" />
      <rect x="4" y="14" width="24" height="4" fill="var(--text)" />
    </svg>
  );
}

export function Logo({ size = 26, wordmark = true }: LogoProps) {
  if (!wordmark) return <LogoMark size={size} />;

  return (
    <div className="logo">
      <LogoMark size={size} />
      <div className="logo__text">
        <span className="logo__name">HANNA</span>
        <span className="logo__sub">AIRCAST</span>
      </div>
    </div>
  );
}
