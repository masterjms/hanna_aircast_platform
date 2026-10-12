/**
 * 비밀번호 규칙 — 서버(app/core/security.py password_ok)와 같은 규칙을 화면에서 먼저 본다(문제점 70번 ②).
 * 서버가 최종 판정이고, 이것은 확인 창을 띄우기 전에 알려 주려는 것이다.
 */

export const PASSWORD_RULE_TEXT = '영문 대·소문자·숫자·특수문자 중 3종 이상 8자 이상, 또는 2종 이상 10자 이상';

export function passwordKinds(pw: string): number {
  return [/[A-Z]/, /[a-z]/, /[0-9]/, /[^A-Za-z0-9]/].filter((re) => re.test(pw)).length;
}

export function passwordOk(pw: string): boolean {
  const kinds = passwordKinds(pw);
  return (kinds >= 3 && pw.length >= 8) || (kinds >= 2 && pw.length >= 10);
}
