# 서버 전달용: 마을 ID 및 LIVE stream_url 정책 (검토 완료본)

원본: ESP32측 저장소 `D:\xWIFI_Radio\iot_radio\spec\esp32_p4_c6_only_Icecast작업\SERVER_VILLAGE_STREAM_URL_POLICY_2026-08-21.md`, 검토본 `...-1.md`(2026-08-21, Claude 검토 — 포트 8100→8000 정정, §7 검토 의견 추가, 전체 방송 URL에 TLS 예시 보강)를 이 프로젝트 spec 폴더로도 보관한 사본이다. 내용은 두 저장소에서 동일하게 유지한다 — 향후 한쪽만 고치고 다른 쪽을 안 맞추지 않도록 주의.

`xWIFI_통신_사양_최종_260813.md` §8(2차 예정)에서 이 문서를 참조한다. **현재 통신 사양의 LIVE_START에는 아직 `stream_url` 필드가 없다** — 이 문서는 단말 펌웨어 반영 후 적용될 다음 단계 정책이다.

## 1. 목적

MQTT + Icecast 연동에서 단말의 마을 배정 방식과 LIVE 방송 URL 정책을 서버 구현자가 동일하게 이해하기 위한 문서다.

현재 1차 테스트는 기존 고정 Icecast URL 구조로 진행한다.
마을별 Icecast mount 분리(`stream_url`)는 다음 단계에서 단말 펌웨어 반영 후 적용한다.

## 2. 마을 ID 정책

단말은 자체적으로 마을 ID를 갖고 있지 않다.
단말의 고유 식별자는 MAC 주소다.

서버는 단말 MAC 주소를 기준으로 해당 단말이 속한 마을을 판단하고, MQTT CONFIG retained 메시지로 `village_id` 8자리 문자열을 내려준다.

예:

```json
{
  "config_version": 50,
  "status_interval_sec": 30,
  "live_stats_interval_sec": 10,
  "event_qos": 0,
  "village_id": "00000001"
}
```

## 3. CONFIG 운용

CONFIG topic:

```text
iotradio/all/config
```

운용 조건:

| 항목 | 정책 |
|---|---|
| QoS | 1 |
| retain | true |
| `village_id` | 8자리 숫자 문자열 |
| 미배정 값 | `"00000000"` |

단말 동작:

1. 부팅 후 MQTT 연결
2. `iotradio/all/config` 구독
3. retained CONFIG를 최대 10초 대기
4. CONFIG 수신 시 `village_id` 적용
5. `village_id`가 `"00000000"`이 아니면 `iotradio/village/<village_id>/cmd` 구독
6. 적용된 `village_id`는 STATUS에 echo

`village_id`는 단말 NVS에 저장하지 않는다.
브로커가 재시작되어 retained CONFIG가 사라질 수 있으므로, 서버는 브로커 재시작 시 CONFIG retained 값을 다시 publish해야 한다.

## 4. 현재 테스트 URL 정책

현재 테스트 펌웨어는 `LIVE_START.stream_url`을 읽지 않는다.

현재 Icecast 접속 URL은 C6 펌웨어 고정 설정을 사용한다.

```text
http://192.168.0.5:8000/live
```

따라서 현재 테스트에서는 서버가 `LIVE_START`에 `stream_url`을 넣어도 단말은 무시한다.
마을별 동시 방송 테스트는 아직 이 구조로 진행하면 안 된다.

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

## 5. 다음 단계 stream_url 정책

마을별 Icecast mount를 분리하려면 `LIVE_START.stream_url` 필드를 사용한다.

단말 펌웨어 반영 후 정책:

1. `LIVE_START.stream_url`이 있으면 해당 URL로 접속한다.
2. `stream_url`이 없으면 기존 고정 URL(`/live`)로 fallback한다.
3. 서버는 마을 방송과 전체 방송을 서로 다른 mount로 제공한다.

마을 방송 URL 형식:

```text
http://<icecast_host>:<icecast_port>/live/<village_id>/<job_id>
```

현재 내부 테스트 예:

```text
http://192.168.0.5:8000/live/00000001/9
```

데모/운영 도메인 적용 예:

```text
http://xxxxxxx.co.kr:8000/live/00000001/9
```

TLS/프록시(Nginx 등) 적용 후 예:

```text
https://xxxxxxx.co.kr/live/00000001/9
```

전체 방송 URL 형식:

```text
http://<icecast_host>:<icecast_port>/live/all/<job_id>
```

현재 내부 테스트 예:

```text
http://192.168.0.5:8000/live/all/9
```

데모/운영 도메인 적용 예:

```text
http://xxxxxxx.co.kr:8000/live/all/9
```

TLS/프록시(Nginx 등) 적용 후 예:

```text
https://xxxxxxx.co.kr/live/all/9
```

`stream_url`은 단말이 해석해서 조립하는 값이 아니라 서버가 완성된 URL 문자열로 내려주는 값이다.
따라서 `http/https`, 도메인, 포트, 경로 구조는 서버/인프라 구성에 따라 최종 확정한다.
ESP32 단말은 최종 확정된 `stream_url` 문자열을 그대로 사용한다.

다음 단계 LIVE_START 예:

```json
{
  "type": "LIVE_START",
  "job_id": 9,
  "stream_url": "http://xxxxxxx.co.kr:8000/live/00000001/9",
  "codec": "opus",
  "frame_ms": 40,
  "sample_rate": 16000,
  "record_flash": 0,
  "ready_timeout_sec": 30
}
```

## 6. 서버 구현 주의사항

- 서버는 단말 MAC 주소와 마을 ID 매핑 테이블을 관리한다.
- 서버는 브로커 시작/재시작 시 `iotradio/all/config` retained CONFIG를 다시 publish해야 한다.
- 현재 테스트 단계에서는 `stream_url`을 넣어도 단말이 사용하지 않는다.
- `stream_url` 적용 펌웨어가 배포되기 전까지는 마을별 동시 LIVE 방송을 검증 대상으로 잡지 않는다.
- `stream_url` 적용 후에는 마을 방송과 전체 방송이 서로 다른 Icecast mount를 사용해야 한다.
- `stream_url`의 최종 scheme(`http`/`https`), host, port, path 정책은 서버/인프라 담당자가 확정한다.
- `stream_url` 적용 후에는 서버가 LIVE_START를 발행하기 *전에* 해당 mount(`/live/<village_id>/<job_id>` 또는 `/live/all/<job_id>`)가 Icecast 상에 실제로 열려 있어야 한다 — 순서가 뒤바뀌면 단말이 접속을 시도했을 때 mount가 없어 실패한다.

## 7. 검토 의견 (Claude, 2026-08-21)

ESP32측 저장소의 `SERVER_COMM_MQTT_ICECAST_HTTP_SPEC_2026-08-14.md`, `ESP32_P4_C6_ICECAST_MQTT_DEVICE_SPEC_2026-08-14.md`, `spec/update/UPDATE_SPEC_FINAL_2026-08-15.md`와 교차 확인했다. CONFIG/village_id 정책(§2~3)은 세 문서 및 이 프로젝트의 `xWIFI_통신_사양_최종_260813.md` §3.5와 전부 정확히 일치한다. job_id 통일, OTA state 목록(ACCEPTED/PREPARE/DOWNLOADING/VERIFYING/COMPLETED/FAIL), OTA_START에 targets/reboot 필드가 없는 점도 전부 교차 확인됨.

확인된 것:

1. **포트는 8000이 맞다** — §5의 `stream_url` 예시 포트를 8100에서 8000으로 정정(2026-08-21 확인). 기존 고정 Icecast(8000)와 같은 포트를 그대로 쓰고, 경로(`/live/<village_id>/<job_id>`)로 마을/세션을 구분하는 것으로 확정.

남은 확인 필요 사항:

1. **서버측 동적 mount 생성 로직은 이 문서의 범위 밖** — 이 문서는 "단말이 `stream_url`을 어떻게 처리하는가"(단말 동작)만 정의한다. 실제로 LIVE_START 발행 시점마다 서버가 `/live/<village_id>/<job_id>` mount를 Icecast에 동적으로 만들고 FFmpeg 오디오 소스를 그 mount로 붙이는 방법은 별도 설계가 필요하다 — 지금 PC측 구현(`Icecast_시작.bat`)은 고정 mount 하나에 상시 소스가 붙어있는 구조라 그대로는 안 맞는다. 백엔드/인프라 담당자가 이 부분을 별도로 설계해야 한다.

부수적으로, 이 문서들을 교차 확인하는 과정에서 이 프로젝트의 `xWIFI_통신_사양_최종_260813.md`에 있던 오류 하나를 발견해 정정했다: LIVE_STATS의 QoS가 "QoS0 고정"으로 잘못 기재되어 있었는데, ESP32측 3개 문서 전부 "LIVE_STATS도 CONFIG `event_qos`를 따른다"고 명시하고 있어 그에 맞춰 수정함(event_qos 기본값이 0이라 지금까지 테스트에선 차이가 드러나지 않았을 뿐).
