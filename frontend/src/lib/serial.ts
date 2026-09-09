/**
 * 단말 시리얼 주입 프레임 (생산 사양 §4.4).
 *
 * 등록 화면과 계정 모달이 같은 문자열을 만들도록 여기 한 곳에 둔다 — 두 곳에
 * 흩어져 있으면 한쪽만 고쳐져서 라인에서만 드러나는 차이가 생긴다.
 */

/** 줄 끝 개행. LF(0x0A) 고정 — CRLF 로 보내면 `\r` 이 값에 붙는다. */
const LF = '\n';

/**
 * `@SSID`·`@PASSWORD`(선택) + `@SERVER` + `@MQTTID` + `@MQTTPW` 를 한 프레임으로 만든다.
 *
 * ```text
 * @SSID=LINE_AP\n@PASSWORD=1234\n@SERVER=hanna-aircast.co.kr\n@MQTTID=58e6c5f2cc74\n@MQTTPW=tA$UAcG2\n@END\n
 * ```
 *
 * **현장 Wi-Fi 는 값이 있을 때만 넣는다 (문제점 35번, 2026-09-08).** 생산 라인에서
 * 실 테스트를 하려면 단말이 인터넷에 붙어야 하는데, 그걸 사람이 따로 넣으면 손이 간다.
 * `@SSID`·`@PASSWORD` 는 생산 serial protocol 에 원래 있는 키라 단말 쪽 변경이 없다 —
 * 등록 화면이 안 보내던 것을 보내는 것뿐이다. 비어 있으면 그 줄 자체를 넣지 않는다
 * (빈 값을 보내면 단말에 저장된 기존 Wi-Fi 설정을 지운다).
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
  /** 생산 라인 공유기. 둘 다 채워졌을 때만 프레임에 들어간다. */
  ssid?: string;
  wifiPassword?: string;
}): string {
  const { serverHost, mac, password, ssid, wifiPassword } = opts;
  const lines: string[] = [];
  // 네트워크 먼저, 그다음 서버·계정 — 생산 사양 §4.4 의 나열 순서다.
  if (ssid && wifiPassword) {
    lines.push(`@SSID=${ssid}`, `@PASSWORD=${wifiPassword}`);
  }
  lines.push(`@SERVER=${serverHost}`, `@MQTTID=${mac}`, `@MQTTPW=${password}`, '@END');
  return lines.map((l) => l + LF).join('');
}

/** 저장한 값을 적용하려고 재부팅한다 (사양 §4.7). */
export function rebootFrame(): string {
  return `@OFF${LF}@END${LF}`;
}
