# 서버 전달용: 마을 ID 및 LIVE stream_url 정책 (검토 완료본)

원본: `SERVER_VILLAGE_STREAM_URL_POLICY_2026-08-21.md`
검토/정리: 2026-08-21 (Claude) — 내용 검토 후 정리. §5 stream_url 예시 포트를 8100→8000으로 정정(작성자 확인 완료), §7에 검토 의견 추가.
재정리: 2026-08-24 (Claude) — §2/§3을 2026-08-24 CONFIG topic 분리(`SERVER_COMM_MQTT_ICECAST_HTTP_SPEC_2026-08-14.md` §4 참고) 반영해서 갱신. `village_id`는 더 이상 `iotradio/all/config`로 내려주지 않고 `iotradio/device/<mac_nocolon>/config`로 단말별로 내려준다 — 미배정 단말이 `all/config` 구독만으로 남의 마을 `village_id`를 받아버리는 문제가 있었기 때문이다.
재정리: 2026-08-24 (Claude) — §4/§5를 stream_url 단말 구현 완료 기준으로 갱신. mount 개수/경로 구조는 서버가 정할 영역이므로 이 문서에서 서버 설계를 규정하던 서술을 걷어내고 단말 동작만 남겼다. 중복되던 URL 예시는 `SERVER_COMM_MQTT_ICECAST_HTTP_SPEC_2026-08-14.md` §6 참조로 정리.

## 1. 목적

MQTT + Icecast 연동에서 단말의 마을 배정 방식과 LIVE 방송 URL 정책을 서버 구현자가 동일하게 이해하기 위한 문서다.

마을별 Icecast mount 분리(`stream_url`)는 2026-08-24에 단말 펌웨어에 반영 완료되었다. 서버가 `stream_url`을 내려주면 단말이 그 주소로 접속하고, 안 내려주면 기존 고정 mount로 fallback한다.

## 2. 마을 ID 정책

단말은 자체적으로 마을 ID를 갖고 있지 않다.
단말의 고유 식별자는 MAC 주소다.

서버는 단말 MAC 주소를 기준으로 해당 단말이 속한 마을을 판단하고, MQTT CONFIG retained 메시지로 `village_id`를 단말별 topic(`iotradio/device/<mac_nocolon>/config`)으로 내려준다. 전체 단말 공통 설정(`status_interval_sec`/`live_stats_interval_sec`/`event_qos`)은 별개 topic(`iotradio/all/config`)으로 내려준다 — 두 topic을 나누는 이유는 §3 참고.

공통 설정 예 (`iotradio/all/config`):

```json
{
  "config_version": 50,
  "status_interval_sec": 30,
  "live_stats_interval_sec": 10,
  "event_qos": 0
}
```

마을 배정 예 (`iotradio/device/<mac_nocolon>/config`):

```json
{
  "config_version": 50,
  "village_id": "00000001"
}
```

## 3. CONFIG 운용

CONFIG topic:

```text
iotradio/all/config                       (공통 설정)
iotradio/device/<mac_nocolon>/config      (마을 배정, village_id)
```

`village_id`를 `all/config`에 같이 넣으면 서버에 아직 등록되지 않은 단말도 `all/config` 구독만으로 남의 마을 `village_id`를 받아버리는 문제가 있어(2026-08-24 발견) 분리했다. 두 topic의 `config_version`은 서버가 항상 같은 값으로 동기화해서 재발행해야 한다 — 단말은 topic별로 버전을 따로 추적하지 않고 마지막으로 받은 값을 그대로 쓰는 단일 카운터 구조다.

운용 조건:

| 항목 | 정책 |
|---|---|
| QoS | 1 |
| retain | true |
| `village_id` | 숫자 문자열. **자리수는 서버가 정한다** |
| 미배정 값 | 전부 `0` |

`village_id`의 형식과 배정 규칙은 이 문서에서 다루지 않는다 — `SERVER_DEVICE_REGISTRY_2026-08-27.md`가 그쪽 담당이다. 이 문서는 그 값이 정해진 뒤의 **전달 경로와 stream_url 정책**만 본다.

단말이 받는 자리수는 **숫자 8~16자리**다(같은 문서 §2.2).

단말 동작:

1. 부팅 후 MQTT 연결
2. `iotradio/all/config`와 `iotradio/device/<mac_nocolon>/config`를 함께 구독
3. 둘 중 하나라도 retained CONFIG를 받으면 그 시점에 적용, 둘 다 10초 동안 오지 않으면 기본값으로 적용
4. `device/config` CONFIG 수신 시 `village_id` 적용
5. `village_id`가 전부 `0`이 아니면 `iotradio/village/<village_id>/cmd` 구독
6. 적용된 `village_id`는 STATUS에 echo

`village_id`는 단말 NVS에 저장하지 않는다.
브로커가 재시작되어 retained CONFIG가 사라질 수 있으므로, 서버는 브로커 재시작 시 두 topic의 CONFIG retained 값을 모두 다시 publish해야 한다.

권장: 서버는 단말 STATUS에 echo되는 `village_id`/`config_version`이 서버 DB와 다르면 해당 단말의 `device/config`를 재발행해서 자동 복구한다(retained 유실, 배정 시점의 연결 끊김 등에 대한 안전장치).

## 4. URL 정책 (2026-08-24 갱신: stream_url 지원됨)

**단말은 이제 `LIVE_START.stream_url`을 읽어 그 주소로 접속한다.** 아래 "현재"는 2026-08-24 이전 상태 기록이다.

- `stream_url`이 있으면 그 문자열로 접속한다.
- 없거나 빈 문자열이면 펌웨어 컴파일타임 기본 mount로 접속한다(구버전 서버 호환).

상세 처리 규칙(길이 제한 512B, 파싱 금지, 스트림 끊김 시 동작, https 지원 시점)은 `SERVER_COMM_MQTT_ICECAST_HTTP_SPEC_2026-08-14.md` §6.1~6.3을 기준으로 한다.

### 4.1 (이력) 2026-08-24 이전 상태

당시 펌웨어는 `stream_url`을 읽지 않고 C6 고정 설정(`http://<host>:8000/live`)만 사용했다. 그래서 서버가 `stream_url`을 넣어도 무시됐고, 서버가 세션별 mount를 만들어도 단말이 그 주소로 붙지 못했다.

현재 테스트용 LIVE_START 예:

```json
{
  "type": "LIVE_START",
  "job_id": 9,
  "codec": "opus",
  "frame_ms": 40,
  "sample_rate": 16000,
  "record_flash": 0,
  "ready_timeout_sec": 30
}
```

## 5. stream_url 정책 (단말 반영 완료)

마을별 방송을 하려면 `LIVE_START.stream_url`에 이번 방송의 Icecast mount 주소를 넣어 보낸다.

**상세 규칙(길이 제한, 파싱 금지, 스트림 끊김 시 동작, https 지원 시점)은 `SERVER_COMM_MQTT_ICECAST_HTTP_SPEC_2026-08-14.md` §6을 기준으로 한다.** 여기서는 이 문서 주제(마을 배정)와 직접 관련된 것만 적는다.

핵심만:

1. `stream_url`이 있으면 단말은 그 URL로 접속한다. 문자열을 파싱하지 않고 그대로 쓴다.
2. 없거나 비어 있으면 펌웨어 컴파일타임 기본 mount로 접속한다.
3. **mount를 몇 개로 나누든 경로를 어떻게 잡든 단말 동작에는 영향이 없다.** mount 구성은 서버가 정한다.
4. 여러 마을에 같은 방송을 보낼 때는 같은 `stream_url`을 각 마을 topic에 발행하면 된다. 단말은 자기 마을 topic 하나만 구독하므로 마을끼리 간섭하지 않는다.

URL 형식:

```text
http://<icecast_host>:<icecast_port>/live/<job_id>
```

예:

```text
http://192.168.0.5:8000/live/9
```

TLS 전환 후에는 `https://<도메인>/live/9` 형태가 되지만, **단말 Icecast 클라이언트의 TLS 지원이 아직 없어 그때까지는 `http://`로 발행해야 한다**(SERVER_COMM §6.3).

LIVE_START 예:

```json
{
  "type": "LIVE_START",
  "job_id": 9,
  "stream_url": "http://192.168.0.5:8000/live/9",
  "codec": "opus",
  "frame_ms": 40,
  "sample_rate": 16000,
  "record_flash": 0,
  "ready_timeout_sec": 30
}
```

## 6. 서버 구현 주의사항

- 서버는 단말 MAC 주소와 마을 ID 매핑 테이블을 관리한다.
- 서버는 브로커 시작/재시작 시 `iotradio/all/config`와 `iotradio/device/<mac_nocolon>/config` 양쪽 retained CONFIG를 모두 다시 publish해야 한다.
- mount 개수와 경로 구조는 서버가 정한다. 단말은 받은 `stream_url`로 접속만 하므로 어떤 구성이든 동작한다. 단말 쪽 관련 동작은 `SERVER_COMM_MQTT_ICECAST_HTTP_SPEC_2026-08-14.md` §6.2 참고.
- 참고: 단말이 `LIVE_STOP`을 놓치면 접속해 있던 mount에 계속 붙어 있는다. 그 mount에 다음 방송이 흐르면 정지 명령을 놓친 단말도 새 방송을 듣게 된다.
- `stream_url`의 최종 scheme(`http`/`https`), host, port, path 정책은 서버/인프라 담당자가 확정한다.
- `stream_url` 적용 후에는 서버가 LIVE_START를 발행하기 *전에* 해당 mount(`/live/<job_id>`)가 Icecast 상에 실제로 열려 있어야 한다 — 순서가 뒤바뀌면 단말이 접속을 시도했을 때 mount가 없어 실패한다.

## 7. 검토 의견 (Claude, 2026-08-21)

같은 폴더의 `SERVER_COMM_MQTT_ICECAST_HTTP_SPEC_2026-08-14.md`, `ESP32_P4_C6_DEVICE_SPEC.md`, `spec/update/UPDATE_SPEC_FINAL_2026-08-15.md`와 교차 확인했다. CONFIG/village_id 정책(§2~3)은 세 문서 및 xWIFI측 `xWIFI_통신_사양_최종_260813.md` §3.5와 전부 정확히 일치한다. job_id 통일, OTA state 목록(ACCEPTED/PREPARE/DOWNLOADING/VERIFYING/COMPLETED/FAIL), OTA_START에 targets/reboot 필드가 없는 점도 전부 교차 확인됨.

확인된 것:

1. **포트는 8000이 맞다** — §5의 `stream_url` 예시 포트를 8100에서 8000으로 정정(2026-08-21 확인). 기존 고정 Icecast(8000)와 같은 포트를 그대로 쓰고, 경로로 세션을 구분하는 것으로 확정. (경로 형식은 2026-08-24에 `/live/<job_id>`로 단순화 — §5 참고.)

남은 확인 필요 사항:

1. **(해소됨 - 단말측) 서버측 동적 mount 생성 로직은 이 문서의 범위 밖** — 이 문서는 "단말이 `stream_url`을 어떻게 처리하는가"(단말 동작)만 정의한다. 실제로 LIVE_START 발행 시점마다 서버가 `/live/<job_id>` mount를 Icecast에 동적으로 만들고 FFmpeg 오디오 소스를 그 mount로 붙이는 방법은 별도 설계가 필요하다 — 지금 PC측 구현(`Icecast_시작.bat`)은 고정 mount 하나에 상시 소스가 붙어있는 구조라 그대로는 안 맞는다. 백엔드/인프라 담당자가 이 부분을 별도로 설계해야 한다.

부수적으로, 이 문서들을 교차 확인하는 과정에서 xWIFI측 `xWIFI_통신_사양_최종_260813.md`에 있던 오류 하나를 발견해 정정했다: LIVE_STATS의 QoS가 "QoS0 고정"으로 잘못 기재되어 있었는데, ESP32측 3개 문서 전부 "LIVE_STATS도 CONFIG `event_qos`를 따른다"고 명시하고 있어 그에 맞춰 수정함(event_qos 기본값이 0이라 지금까지 테스트에선 차이가 드러나지 않았을 뿐).
