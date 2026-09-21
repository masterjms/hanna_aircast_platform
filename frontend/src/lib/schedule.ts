/**
 * 스케줄 문장 조립.
 *
 * 확인 화면의 「앞으로 매주 월·수 오전 9:00마다 안내.mp3을 계곡마을에 방송합니다」와
 * 목록의 한 줄 요약이 같은 함수를 쓴다. 규칙을 사람 말로 옮기는 자리는 여기 하나다.
 */

import type { Repeat, YearDate } from '../api/types';

export const WEEKDAY_LABELS = ['일', '월', '화', '수', '목', '금', '토'] as const;

export const REPEAT_LABEL: Record<Repeat, string> = {
  once: '한 번',
  daily: '매일',
  weekly: '매주',
  monthly: '매월',
  yearly: '매년',
};

export interface RuleLike {
  repeat: Repeat;
  weekdays: number[] | null;
  month_days: number[] | null;
  year_dates: YearDate[] | null;
  /** once 일 때 "2026-09-22" */
  once_date?: string | null;
  /** "09:00" 또는 "09:00:00" */
  fire_time: string;
}

/** "2026-09-22" → "9월 22일(월)". 요일까지 붙여야 「내일인지 모레인지」 헷갈리지 않는다. */
export function dateLabel(iso: string): string {
  const [y, m, d] = iso.split('-').map(Number);
  const wd = WEEKDAY_LABELS[new Date(y, m - 1, d).getDay()];
  return `${m}월 ${d}일(${wd})`;
}

/** "09:05:00" → "오전 9:05". 이장님 화면이라 24시간 표기를 쓰지 않는다. */
export function formatTime(hhmm: string): string {
  const [h, m] = hhmm.split(':').map(Number);
  const ampm = h < 12 ? '오전' : '오후';
  const hour12 = h % 12 === 0 ? 12 : h % 12;
  return `${ampm} ${hour12}:${String(m).padStart(2, '0')}`;
}

/** 반복 부분만 — "매주 월·수", "매월 2일·15일", "매년 3월 1일", "매일". */
export function repeatLabel(r: RuleLike): string {
  switch (r.repeat) {
    case 'once':
      return r.once_date ? dateLabel(r.once_date) : '한 번';
    case 'daily':
      return '매일';
    case 'weekly':
      return `매주 ${(r.weekdays ?? []).map((d) => WEEKDAY_LABELS[d]).join('·')}`;
    case 'monthly':
      return `매월 ${(r.month_days ?? []).map((d) => `${d}일`).join('·')}`;
    case 'yearly':
      return `매년 ${(r.year_dates ?? []).map((d) => `${d.month}월 ${d.day}일`).join('·')}`;
  }
}

/** 반복 + 시각 — "매주 월·수 오전 9:00", 한 번짜리는 "9월 22일(월) 오전 7:00 한 번". */
export function whenLabel(r: RuleLike): string {
  const base = `${repeatLabel(r)} ${formatTime(r.fire_time)}`;
  return r.repeat === 'once' ? `${base} 한 번` : base;
}

/** 확인 화면 문장. 목업 3 그대로. */
export function sentence(r: RuleLike, targetLabel: string, fileName: string): string {
  if (r.repeat === 'once') {
    return `${repeatLabel(r)} ${formatTime(r.fire_time)}에 한 번 ${fileName}을(를) ${targetLabel}에 방송합니다.`;
  }
  return `앞으로 ${whenLabel(r)}마다 ${fileName}을(를) ${targetLabel}에 방송합니다.`;
}

/** 오늘(KST) "2026-09-21". 날짜 입력칸의 최솟값·기본값에 쓴다. */
export function kstToday(offsetDays = 0): string {
  const d = new Date(Date.now() + offsetDays * 86_400_000);
  return d.toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' });
}

/** KST 기준 날짜 키 "2026-09-09". 예정표를 날짜별로 묶을 때 쓴다. */
export function kstDateKey(iso: string): string {
  const d = new Date(iso);
  // sv-SE 로케일은 ISO 와 같은 YYYY-MM-DD 를 준다.
  return d.toLocaleDateString('sv-SE', { timeZone: 'Asia/Seoul' });
}

/** "9/9 (수)" */
export function kstDayHeader(date: Date): string {
  const mm = date.toLocaleDateString('ko-KR', { timeZone: 'Asia/Seoul', month: 'numeric' });
  const dd = date.toLocaleDateString('ko-KR', { timeZone: 'Asia/Seoul', day: 'numeric' });
  const wd = date.toLocaleDateString('ko-KR', { timeZone: 'Asia/Seoul', weekday: 'short' });
  return `${mm.replace('월', '').trim()}/${dd.replace('일', '').trim()} (${wd})`;
}

/** "오전 9:00" 만 — 예정표 칸에 쓴다. */
export function kstTime(iso: string): string {
  const d = new Date(iso);
  const h = Number(d.toLocaleTimeString('en-GB', { timeZone: 'Asia/Seoul', hour: '2-digit', hour12: false }));
  const m = d.toLocaleTimeString('en-GB', { timeZone: 'Asia/Seoul', minute: '2-digit' });
  return formatTime(`${h}:${m.padStart(2, '0')}`);
}
