/**
 * 단말 시리얼 주입 프레임 (생산 사양 §4.4).
 *
 * 등록 화면과 계정 모달이 같은 문자열을 만들도록 여기 한 곳에 둔다 — 두 곳에
 * 흩어져 있으면 한쪽만 고쳐져서 라인에서만 드러나는 차이가 생긴다.
 */

/** 줄 끝 개행. LF(0x0A) 고정 — CRLF 로 보내면 `\r` 이 값에 붙는다. */
const LF = '\n';

/**
 * `@SERVER` + `@MQTTID` + `@MQTTPW` 를 한 프레임으로 만든다.
 *
 * ```text
 * @SERVER=hanna-aircast.co.kr\n@MQTTID=58e6c5f2cc74\n@MQTTPW=tA$UAcG2\n@END\n
 * ```
 *
 * **모든 명령은 개행(LF)으로 끝난다 — `@END` 앞에도 개행이 있어야 한다.**
 * 단말 파서는 줄 단위로 먼저 자르고 그다음 `@KEY=VALUE` 를 읽는다
 * (2026-08-31 단말 로그로 확정). `@MQTTPW=<값>@END` 처럼 한 줄에 붙여 보내면
 * `@END` 까지 통째로 비밀번호에 들어가 브로커 인증이 조용히 실패한다 —
 * 실물 신규 등록에서 실제로 겪은 사고다. 값 안의 개행 걱정은 필요 없다:
 * 줄을 먼저 자르므로 줄 끝 LF 는 값에 포함되지 않는다.
 */
export function provisioningFrame(opts: {
  serverHost: string;
  mac: string;
  password: string;
}): string {
  const { serverHost, mac, password } = opts;
  return `@SERVER=${serverHost}${LF}@MQTTID=${mac}${LF}@MQTTPW=${password}${LF}@END${LF}`;
}

/** 저장한 값을 적용하려고 재부팅한다 (사양 §4.7). */
export function rebootFrame(): string {
  return `@OFF${LF}@END${LF}`;
}
