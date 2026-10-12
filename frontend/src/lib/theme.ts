/**
 * 화면 테마 — 어두운(기본) · 밝은 (문제점 73번, 2026-10-12).
 *
 * <html data-theme="light"> 하나로 바뀐다. 색은 전부 global.css 의 토큰이라 여기서는 속성만 만진다.
 * 브라우저에 기억한다(사람마다 다르게 쓴다). 저장소를 못 쓰는 브라우저는 매번 어두운 테마로 연다.
 */

export type Theme = 'dark' | 'light';

const KEY = 'xwifi.theme';

export function readTheme(): Theme {
  try {
    return localStorage.getItem(KEY) === 'light' ? 'light' : 'dark';
  } catch {
    return 'dark';
  }
}

export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  try {
    localStorage.setItem(KEY, theme);
  } catch {
    /* 기억만 못 할 뿐 바뀌기는 한다 */
  }
}
