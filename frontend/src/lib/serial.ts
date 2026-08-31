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
 * @SERVER=hanna-aircast.co.kr\n@MQTTID=58e6c5f2cc74\n@MQTTPW=tA$UAcG2@END\n
 * ```
 *
 * ⚠ **비밀번호와 `@END` 사이에는 개행을 넣지 않는다.** 파서는 `@` 로 명령을
 *   구분하는데, `@MQTTPW` 와 `@PASSWORD` 만은 값의 앞뒤 공백을 다듬지 않는다
 *   (사양 §4.4 「값 안의 공백」 — 공백으로 끝나는 Wi-Fi 비밀번호를 지키려는 것).
 *   그래서 `@MQTTPW=<값>\n@END` 로 보내면 그 `\n` 까지 비밀번호가 되어 버리고,
 *   단말은 브로커 인증에 조용히 실패한다. `@END` 를 값에 바로 붙여야 `@` 가
 *   값의 끝을 끊는다.
 *
 *   값이 다듬어지는 다른 키(@SERVER, @MQTTID)는 개행으로 끊어도 안전하다.
 */
export function provisioningFrame(opts: {
  serverHost: string;
  mac: string;
  password: string;
}): string {
  const { serverHost, mac, password } = opts;
  return `@SERVER=${serverHost}${LF}@MQTTID=${mac}${LF}@MQTTPW=${password}@END${LF}`;
}

/** 저장한 값을 적용하려고 재부팅한다 (사양 §4.7).
 *
 * `@OFF` 는 값이 없어서 개행으로 끊어도 안전하다.
 */
export function rebootFrame(): string {
  return `@OFF${LF}@END${LF}`;
}
