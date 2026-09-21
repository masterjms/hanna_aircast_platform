/**
 * 회사·서비스 표기. 푸터와 약관·개인정보 처리방침이 같이 쓴다.
 *
 * ⚠ 빈 값은 화면에서 그 줄이 빠진다. 개인정보 보호책임자와 연락처는 개인정보 보호법 제30조의
 *   필수 기재사항이므로 운영 전에 반드시 채운다. 이 저장소는 공개라 실제 값은 커밋 전에
 *   회사 공개 정보(홈페이지에 이미 실린 대표번호·주소)만 넣는다.
 */
export const COMPANY = {
  nameEn: 'Hanna Electronics',
  /** 법인 등기상 상호(한글). 예: '주식회사 ○○' */
  nameKo: '한나전자',
  service: 'HANNA AirCast',
  /** 본점 주소 */
  address: '',
  /** 대표 전화 */
  phone: '',
  /** 개인정보 보호책임자 — 성명 또는 담당 부서 */
  privacyOfficer: '',
  /** 개인정보 문의 연락처(전화 또는 전자우편) */
  privacyContact: '',
  /** 약관·방침 시행일 */
  effectiveDate: '2026년 9월 21일',
  copyrightYear: 2026,
} as const;

export const COPYRIGHT = `© ${COMPANY.copyrightYear}, ${COMPANY.nameEn}. All rights reserved.`;
/** 본문에서 회사를 처음 부를 때 */
export const COMPANY_FULL = COMPANY.nameKo ? `${COMPANY.nameKo}(${COMPANY.nameEn})` : COMPANY.nameEn;
