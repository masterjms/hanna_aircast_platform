# SERVER COMM MQTT ICECAST HTTP SPEC 2026-08-14

정리: 2026-08-24 (Claude) — C6 소스코드(`net_client.c`) 재대조. FILE_START `resume_offset`/`autoplay` 기본 동작, FILE_END `fail_reason` 전체 표 보강. 나머지 필드/topic/QoS 정의는 코드와 이미 일치함을 확인.

갱신: 2026-08-24 (Claude) — `LIVE_START.stream_url` 단말 구현 완료에 따라 §6 재작성(필드표, §6.1 처리 규칙, §6.2 mount 구조와 단말의 관계, §6.3 https 지원 시점), §7.1 신설(스트림 실패 시 두 번째 `LIVE_READY status=2` 발행 — 기존 사양서에 누락되어 있던 실제 동작).

갱신: 2026-08-24 (Claude) — §6.2 재작성. mount 개수/경로 구조는 서버가 정할 영역이고 단말 동작과 무관하므로, 이 문서에서 서버 설계를 규정하던 서술을 걷어내고 단말 동작(파싱 안 함, LIVE_STOP 놓친 단말의 잔류, 마을 topic 독립성)만 남겼다. §5 우선순위 문장 정정. `OTA > LIVE > FILE > RF > IDLE`은 STATUS 표시 순서일 뿐인데 명령 우선순위로 읽힐 여지가 있었다. 실제 명령 수락은 선착순이고 선점이 없다는 §5.1(거부 응답표 포함), 서버가 정지→확인→재시작 순서를 만들어야 한다는 §5.2, 정지 직후 명령이 거부되는 이유인 §5.3을 신설했다. 코드(`mqtt_state_name()`, 각 명령의 busy 검사)와 대조해 작성.

갱신: 2026-08-25 (Claude) — **재접속 관련 서술은 이 문서가 최종이다.** 그동안 두 차례 정정이 오갔는데(처음 "3초 주기 재접속" → 이후 "재접속하지 않음"), 이제 단말에 재접속이 구현되어 아래 §6.4가 실제 동작이다. 서버가 할 일은 **방송이 끝날 때까지 `stream_url`을 유지하는 것 하나**이고 그 외 추가 조치는 없다. 재접속이 덮지 않는 경우(MQTT 동반 단절)는 §6.5에 새로 정리했다. 이하 종전 갱신 내용: 자동 재시도 정책 반영. LIVE 스트림 끊김 시 3회/8초 이내 자동 재접속(§6.4), FILE 다운로드 끊김 시 Range 기반 자동 이어받기 3회(§11.1)를 단말에 구현하고 문서화했다. 재접속 중 상태를 알리려고 그동안 항상 0으로만 나가던 STATUS `reason` 필드에 값을 정의했다(§5, 0=정상 / 1=LIVE 재접속 중). 새 메시지 타입은 추가하지 않았다.

ESP32-P4+C6 수신기와 서버 사이의 MQTT, Icecast, HTTP 파일 전송 사양이다.

업데이트 사양은 `spec/update/UPDATE_SPEC_FINAL_2026-08-15.md`를 최종 기준으로 한다.

---

## 1. 기본 구성

| 항목 | 1차 테스트 기준 |
|---|---|
| MQTT broker | `mqtt://192.168.0.5:1883` |
| Icecast live stream | `http://192.168.0.5:8000/live` |
| HTTP file server | `http://192.168.0.5:9002/file/...` |
| OTA pkg server | `http://192.168.0.5:9002/update/IOT_RADIO.pkg` |

`iotradio`는 IP나 host명이 아니라 MQTT topic namespace다.

---

## 2. MQTT topic

명령 topic:

```text
iotradio/device/<mac_nocolon>/cmd
iotradio/village/<village_id>/cmd
iotradio/all/cmd
```

상태 topic:

```text
iotradio/device/<mac_nocolon>/status
```

결과 topic:

```text
iotradio/device/<mac_nocolon>/result
```

CONFIG topic:

```text
iotradio/all/config
iotradio/device/<mac_nocolon>/config
```

CONFIG는 QoS 1, retained true로 운용한다. 일반 명령 topic은 QoS 1, retained false로 운용한다.

### 2.1 단말이 쓰는 topic 전체 — 브로커 ACL 기준

브로커가 ACL로 topic을 제한한다면 **아래가 허용 목록이다.** 하나라도 빠지면 그 기능이 동작하지 않는다.

| 방향 | topic | 없으면 |
|---|---|---|
| 구독 | `iotradio/device/<mac_nocolon>/cmd` | 이 단말 지정 방송이 안 옴 |
| 구독 | `iotradio/village/<village_id>/cmd` | **마을 방송이 안 옴** |
| 구독 | `iotradio/all/cmd` | 전체 방송이 안 옴 |
| 구독 | `iotradio/all/config` | 공통 설정을 못 받음 |
| 구독 | `iotradio/device/<mac_nocolon>/config` | 마을 배정을 못 받음 |
| 발행 | `iotradio/device/<mac_nocolon>/status` | **서버 화면에 안 나타남** |
| 발행 | `iotradio/device/<mac_nocolon>/result` | 방송 결과 보고가 안 옴 |

단말은 **`cmd` topic에 발행하지 않는다.** 명령을 내리는 쪽은 서버뿐이므로 단말의 `cmd` 발행은 막아도 된다.

#### `village/<village_id>/cmd`를 특히 확인할 것

다른 topic은 MAC이 들어가므로 ACL이 `client_id` 치환(`%c`)으로 잡을 수 있다. **이 topic만 MAC이 아니라 서버가 배정한 `village_id`가 들어간다.**

`%c`로는 잡히지 않으므로 별도 규칙이 필요하다. 빠뜨리면 단말은 정상으로 보이는데 **마을 방송만 안 오는** 상태가 된다.

#### ACL 거부는 에러가 아니다

MQTT는 ACL로 막힌 발행·구독을 **조용히 버린다.** 단말은 성공한 것으로 알고 계속 동작한다.

그래서 ACL을 조일 때는 위 목록과 대조해야 한다. 2026-08-26에 `client_id` 형식이 어긋나 모든 발행이 버려진 일이 있었는데, 단말 로그에는 아무 이상이 없었다(§3.1).

---

## 3. 공통 ID 정책

서버가 단말에 지시하는 작업 번호는 `job_id`로 통일한다.

LIVE, FILE, OTA 모두 외부 MQTT payload에는 `job_id`만 사용한다.

### 3.1 MQTT client_id

**콜론 없는 소문자 MAC 그대로다. 접두사를 붙이지 않는다.**

```text
58e6c5f2cc74
```

브로커 ACL이 client_id를 토픽의 MAC 자리에 치환해서 "각 단말은 자기 토픽에만 발행"을 강제한다. 값이 토픽의 MAC과 다르면 **접속은 되지만 발행과 구독이 조용히 무시된다** — 에러가 없어서 단말은 정상으로 보이는데 서버 화면에는 나타나지 않는다.

이 형식이 사양에 없어서 단말이 `iotradio-<mac>`을 쓰고 있었고, 2026-08-26 운영 서버 전환 때 드러났다.

### 3.2 브로커 인증

운영 브로커는 익명 접속을 받지 않는다. 전 단말이 **같은 계정 하나**를 쓰고, 단말별 권한은 위 client_id로 ACL이 판정한다.

계정 값은 서버 운영자가 정해 단말 펌웨어에 넣는다. 펌웨어에 박히는 값이라 기기를 가진 사람은 꺼낼 수 있고, 그 전제로 ACL이 걸려 있다 — 계정이 새어도 남의 MAC으로는 발행하지 못한다.

---

## 4. CONFIG

CONFIG는 두 topic으로 나뉜다. `all/config`는 전체 단말 공통 설정, `device/<mac_nocolon>/config`는 그 단말 하나의 마을 배정이다. `village_id`를 `all/config`에 넣으면 미배정 단말까지 남의 마을을 구독하게 되는 문제가 있어(2026-08-24 발견) 분리했다.

### 4.1 공통 설정 — `iotradio/all/config`

payload 예:

```json
{
  "config_version": 36,
  "status_interval_sec": 30,
  "live_stats_interval_sec": 10,
  "event_qos": 1
}
```

| 필드 | 기본값 | 범위 | 의미 |
|---|---:|---|---|
| `config_version` | 1 | 제한 없음 | 서버 설정 버전 |
| `status_interval_sec` | 30 | 10~3600 | 주기 STATUS 발행 간격 |
| `live_stats_interval_sec` | 10 | 1~60 | LIVE_STATS 발행 간격 |
| `event_qos` | 0 | 0~1 | 주기 STATUS/LIVE_STATS 및 OTA 중간 상태 QoS |

### 4.2 마을 배정 — `iotradio/device/<mac_nocolon>/config`

payload 예:

```json
{
  "config_version": 36,
  "village_id": "00000001"
}
```

| 필드 | 기본값 | 범위 | 의미 |
|---|---:|---|---|
| `config_version` | 1 | 제한 없음 | 서버 설정 버전(all/config와 같은 카운터를 쓴다) |
| `village_id` | 전부 `0` | 숫자 문자열 | 마을 ID |

`village_id`가 **전부 `0`이면** 마을 topic을 구독하지 않는다. CONFIG로 실제 마을 ID를 받으면 해당 마을 topic을 구독한다.

**자리수는 서버가 정한다.** 단말은 이 값을 해석하지 않고 topic에 그대로 끼워넣기만 한다. 형식과 배정 규칙은 `SERVER_DEVICE_REGISTRY_2026-08-27.md`에 있다.

단말은 **숫자 8~16자리**를 받는다(2026-08-27 반영). 숫자가 아니거나 범위를 벗어난 값은 로그를 남기고 버리며 이전 값을 유지한다. 예제의 `"00000001"`은 8자리인 경우다.

배정 해제(빈 payload retain)는 기존 retain 삭제 규약과 동일하게 처리한다.

### 4.3 부팅 시 대기 정책

MQTT 연결 직후 단말은 `all/config`와 `device/<mac_nocolon>/config`를 함께 구독하고, 둘 중 하나라도 retained CONFIG를 받으면 그 시점에 적용 후 첫 STATUS를 QoS 1로 발행한다.
10초 동안 아무 CONFIG도 오지 않으면 기본값(`village_id` 전부 `0`, `config_version=1`)으로 첫 STATUS를 QoS 1로 발행하고, 이후 어느 topic으로든 CONFIG가 도착하면 그때 다시 적용/보고한다.

서버는 두 topic의 `config_version`을 항상 같은 값으로 동기화해서 재발행해야 한다 — 단말은 topic별로 버전을 따로 추적하지 않고 마지막으로 받은 값을 그대로 쓰는 단일 카운터 구조다.

권장: 서버는 단말의 주기 STATUS에 echo되는 `village_id`/`config_version`이 서버 DB와 다르면 해당 단말의 `device/config`를 재발행해서 자동 복구한다(브로커 retained 유실, 배정 시점의 연결 끊김 등에 대한 안전장치).

---

## 5. STATUS

topic:

```text
iotradio/device/<mac_nocolon>/status
```

payload 예:

```json
{
  "type": "STATUS",
  "device": "58:e6:c5:f2:cc:74",
  "village_id": "00000001",
  "wifi": 1,
  "mqtt": 1,
  "ip": "192.168.0.21",
  "rssi": -36,
  "state": "IDLE",
  "busy": 0,
  "reason": 0,
  "config_version": 36
}
```

주기 STATUS QoS는 CONFIG `event_qos`를 따른다.
첫 STATUS는 연결 직후 바로 보내지 않고 retained CONFIG 수신 또는 10초 대기 후 QoS 1로 보낸다.
LWT는 QoS 1을 사용한다.

`state` 값:

| state | 의미 |
|---|---|
| `IDLE` | 대기 상태 |
| `LIVE` | Icecast 실시간 방송 준비/재생 중 |
| `FILE` | HTTP 파일 다운로드/전송 중 또는 P4 파일 방송 재생 중 |
| `RF` | P4 RF 방송 수신/재생 중 |
| `OTA` | 온라인 OTA 진행 중 |
| `OFFLINE` | LWT 또는 네트워크 단절 상태 |

`reason` 값:

| 값 | 의미 |
|---:|---|
| 0 | 정상 |
| 1 | LIVE 스트림이 끊겨 재접속 시도 중. **방송은 아직 살아있지만 스피커로 소리가 나가지 않는 상태**다 |

서버는 `reason != 0`을 "방송 중인데 무음"으로 읽으면 된다. 재접속에 성공하면 즉시 `reason=0`인 STATUS가 다시 오고, 실패하면 방송이 끝나면서 `state=IDLE`이 온다. 상세는 §6.4.

여러 동작이 동시에 걸려 있을 때 STATUS의 `state`에 무엇을 실을지는 `OTA > LIVE > FILE > RF > IDLE` 순으로 정한다.

**이 순서는 STATUS 표시용이며, 명령의 우선순위가 아니다.** 예를 들어 파일 방송 중에 `LIVE_START`를 보내면 "LIVE가 FILE보다 위"라서 선점되는 것이 아니라 거부된다. 실제 명령 수락 규칙은 아래 §5.1을 따른다.

단말은 상태가 변경되면 주기 STATUS를 기다리지 않고 STATUS를 1회 즉시 발행한다.
상태 변경 즉시 STATUS의 QoS는 CONFIG `event_qos`를 따른다.

`OFFLINE`은 단말이 정상 publish로 직접 보내는 상태가 아니다.
단말은 MQTT 연결 시 LWT(Last Will)를 status topic에 등록한다.
전원 차단, Wi-Fi 단절, 비정상 리셋 등으로 MQTT 연결이 끊기면 broker가 LWT payload를 status topic에 publish한다.
서버는 status topic을 구독하고 있다가 이 LWT 메시지를 수신하면 해당 단말을 OFFLINE으로 표시한다.
정상 재부팅/OTA처럼 의도된 종료 흐름에서는 LWT가 발행되지 않을 수 있으므로, OTA_STATUS와 재접속 후 STATUS를 함께 기준으로 판단한다.

### 5.1 명령 수락 규칙 — 선착순, 선점 없음

**단말은 이미 진행 중인 동작을 새 명령으로 중단시키지 않는다.** 무엇이든 먼저 시작한 것이 끝날 때까지 단말을 차지하고, 그 사이에 도착한 LIVE/FILE/OTA 명령은 전부 거부된다.

| 진행 중 | `LIVE_START` | `FILE_START` | `OTA_START` |
|---|---|---|---|
| 없음 | 수락 | 수락 | 수락 |
| OTA | `LIVE_READY status=3 reason=0x08` | `FILE_ABORT reason=BUSY` | `OTA_STATUS FAIL reason=BUSY` |
| LIVE | `LIVE_READY status=3 reason=0x08` | `FILE_ABORT reason=PREEMPTED_BY_LIVE` | `OTA_STATUS FAIL reason=BUSY` |
| FILE | `LIVE_READY status=3 reason=0x08` | `FILE_ABORT reason=BUSY` | `OTA_STATUS FAIL reason=BUSY` |

`PREEMPTED_BY_LIVE`라는 이름 때문에 라이브가 파일을 밀어낸 것처럼 읽히지만, 실제로는 **라이브가 이미 단말을 쓰고 있어서 파일 방송이 거부된 것**이다. 밀려난 쪽은 새로 온 파일 방송이다.

즉 **파일 다운로드가 진행 중이면 그 사이에 온 실시간 방송도 거부된다.** 긴급 방송이라도 예외가 없다.

### 5.2 그래서 서버가 해야 할 것

단말에 우선순위 판단 기능이 없으므로, **어느 방송이 더 중요한지는 서버가 정하고 순서를 만들어 준다.**

```text
1. STATUS로 대상 단말이 방송 중인지 확인한다.
2. 방송 중이면 먼저 정지 명령(LIVE_STOP / FILE_STOP)을 보낸다.
3. STATUS가 state=IDLE, busy=0으로 돌아온 것을 확인한다.
4. 그 다음에 새 LIVE_START / FILE_START를 보낸다.
```

3번을 건너뛰고 곧바로 새 명령을 보내면 거부될 수 있다. 정지 처리에는 시간이 걸리기 때문이다 — 자세한 내용은 §5.3.

3번이 정해둔 시간(권장 10초) 안에 오지 않으면 그 단말은 이상이 있는 것으로 보고 **건너뛰고 나머지 단말/마을 방송을 계속 진행한다.** 단말 한 대 때문에 다른 마을 방송이 막히면 안 된다.

상태 변경 시 단말은 주기 STATUS를 기다리지 않고 즉시 발행하므로(§5), 고정 시간을 추측하지 말고 이 STATUS를 기다리는 편이 정확하다.

### 5.3 정지 직후 새 명령을 보내면 거부되는 이유

`LIVE_STOP`을 받으면 단말은 내부 상태를 바로 내리지만, **Icecast 수신 태스크가 실제로 끝나기까지는 시간이 더 걸린다.** 그 태스크는 HTTP 읽기에 최대 5초까지 묶여 있을 수 있고, P4 쪽 오디오 정리에도 약 3초가 든다. 그 사이에 도착한 `LIVE_START`는 거부된다.

데이터가 정상적으로 흐르던 방송을 정지할 때는 읽기가 곧바로 반환되어 빠르게 끝난다. **가장 오래 걸리는 경우는 이미 끊긴 스트림을 정지할 때다** — 공교롭게도 서버가 "왜 안 멈추지" 하고 재시도하기 쉬운 상황이다.

그래서 §5.2의 3번(STATUS `IDLE` 확인)이 필요하다.

---

## 6. LIVE_START

payload:

```json
{
  "type": "LIVE_START",
  "job_id": 101,
  "stream_url": "http://192.168.0.41:8000/live/101",
  "codec": "opus",
  "frame_ms": 40,
  "sample_rate": 16000,
  "record_flash": 0,
  "file_name": "live-demo.lopus",
  "ready_timeout_sec": 30
}
```

| 필드 | 필수 | 의미 |
|---|---|---|
| `job_id` | 필수 | 방송 세션 ID |
| `stream_url` | 권장 | 이번 세션의 Icecast mount 주소(완성된 URL 문자열). 생략/빈 문자열이면 단말 펌웨어 기본 mount로 접속 |
| `codec` | 선택 | 기본 `"opus"` |
| `frame_ms` | 선택 | 기본 40. 0이거나 2000 초과면 40으로 보정 |
| `sample_rate` | 선택 | 기본 16000 |
| `record_flash` | 선택 | 기본 0 |
| `file_name` | 선택 | 녹음 저장 파일명. 규칙은 LIVE/FILE 공통이다 — §11.2 참고 |
| `ready_timeout_sec` | 선택 | 기본 30, 1~60으로 보정 |

### 6.1 stream_url

서버는 이번 방송을 수신할 Icecast 주소를 **완성된 URL 문자열**로 `stream_url`에 넣어 내려준다. 단말은 이 값을 그대로 쓴다.

```text
http://<host>:<port>/live/<job_id>
```

단말 처리 규칙:

- 단말은 이 문자열을 **파싱하거나 재조립하지 않고 그대로 HTTP GET 대상으로 사용**한다. `village_id`나 `job_id`로 경로를 직접 만들지 않으므로, 서버가 경로 구조를 바꿔도 단말 펌웨어를 고칠 필요가 없다.
- `stream_url`이 없거나 빈 문자열이면 펌웨어 컴파일타임 기본 mount로 접속한다(구버전 서버 호환).
- 최대 길이는 **512바이트**(NUL 포함)다. 이를 넘으면 단말은 URL을 잘라 쓰지 않고 방송 시작을 거절한다. MQTT 명령 payload 전체 상한(1024B)도 함께 지켜야 하므로 서버가 발행 전에 검사한다.
- **스트림이 끊기면 단말이 같은 `stream_url`로 자동 재접속을 시도한다**(§6.4). 복구하지 못하면 그때 방송을 끝내고 `LIVE_READY status=2`를 발행한다(§7.1). 서버는 방송이 끝날 때까지 mount와 source를 유지해야 한다.
- `https://`(포트 생략 포함) 형태는 TLS 전환 이후에 지원된다. 아래 §6.3 참고.

### 6.2 mount 구조는 단말과 무관하다

**mount를 몇 개로 나누든 경로를 어떻게 잡든 단말 동작에는 영향이 없다.** 단말은 받은 `stream_url`로 접속만 하고, 그 주소가 마을 전용 mount인지 여러 마을이 함께 쓰는 mount인지 알지 못한다.

따라서 mount 구성은 서버가 정한다. 이 문서는 단말이 그 값을 어떻게 처리하는지만 규정한다.

관련해서 알아 둘 단말 동작:

- 단말은 `stream_url`을 **파싱하지 않는다.** 경로 구조가 바뀌어도 펌웨어 변경이 필요 없다.
- 단말이 `LIVE_STOP`을 받지 못하면 **접속해 있던 mount에 계속 붙어 있는다.** 그 mount에 다음 방송 오디오가 흐르면 정지 명령을 놓친 단말도 새 방송을 듣게 된다. 방송마다 경로가 달라지는 구성에서는 그런 단말이 새 방송을 받지 못하고 끊긴다.
- 여러 마을에 동시에 방송할 때, 각 단말은 **자기 마을 topic 하나만** 구독하므로 서로 간섭하지 않는다.

### 6.3 https 지원 시점

현재 단말의 Icecast 클라이언트에는 TLS 인증서 검증 설정(`crt_bundle_attach`)이 없어 **`https://` URL로는 접속하지 못한다.** `http://`만 동작한다.

`https://`는 TLS 전환 작업(`HOST_CONFIRMED_TLS_ROLLOUT_CHECKLIST_2026-08-23.md`)에서 Icecast 클라이언트에 인증서 검증을 추가한 뒤 지원한다. 그때까지 서버는 `stream_url`을 `http://`로 발행해야 한다.

동작:

1. C6가 MQTT 명령 수신
2. C6가 P4에 live 준비 요청
3. P4가 intro 출력 후 live 준비
4. P4 준비 완료 후 C6가 `LIVE_READY status=0` publish
5. C6가 `stream_url`(없으면 기본 mount)로 Icecast stream 연결

4번과 5번의 순서에 주의한다. `LIVE_READY status=0`은 **P4 오디오 출력 준비가 끝났다는 뜻이지 스트림 수신이 시작됐다는 뜻이 아니다.** 5번이 실패하면 §7의 두 번째 `LIVE_READY`가 뒤따른다.

### 6.4 스트림이 끊겼을 때 — 자동 재접속 3회

방송 도중 Icecast 스트림이 끊기면 단말이 **먼저 스스로 3회 재접속을 시도**한다. 서버가 개입할 필요는 없다.

```text
스트림 끊김
  → STATUS reason=1 즉시 발행 (방송 유지, 스피커 무음)
  → 같은 stream_url로 재접속 (0.5초 → 2초 → 4초 간격, 최대 3회)
  ├ 성공 → STATUS reason=0 즉시 발행, 방송 계속
  └ 실패 → LIVE_READY status=2 + STATUS state=IDLE, 방송 종료
```

**서버가 기댈 수 있는 복구 허용 시간**은 끊긴 양상에 따라 다르다.

| 끊긴 양상 | 단말이 겪는 것 | 허용 시간 |
|---|---|---|
| source 재시작 (Icecast가 연결을 닫음) | 즉시 끊김을 인지 | **약 6.5초** |
| mount만 사라짐 | 재접속 시 즉시 404 | **약 6.5초** |
| 서버/네트워크 무응답 | 재접속이 타임아웃(5초)까지 대기 | **약 5.5초** |

즉 **방송 중 source를 재시작하려면 6초 안에 같은 mount로 돌아와야** 단말이 방송을 이어간다. 그보다 오래 걸리면 그 방송은 종료되고, 서버가 새 `job_id`로 다시 시작해야 한다.

- 재접속은 **같은 `stream_url`로** 시도한다. 서버는 방송이 끝날 때까지 그 주소를 유지해야 한다.
- 재시도 중에도 P4는 LIVE 상태를 유지하므로 방송 세션은 끊기지 않는다. 다만 그동안 **스피커는 무음**이다.
- 총 재시도 시간이 P4의 프레임 무수신 정리 시점(15초)을 넘지 않도록 잡혀 있다. 단말이 먼저 포기한다.
- 서버가 연결을 **정상 종료**하면(source가 빠져 Icecast가 청취자를 끊는 경우) 단말은 이를 **즉시** 인지하고 재접속에 들어간다. 데이터만 멈추고 연결이 살아 있는 경우에는 읽기 타임아웃 5초 뒤에 인지한다.
- 재시도 중에 새 `LIVE_START`를 보내면 **거부된다**(BUSY). 방송이 아직 끝나지 않은 상태이기 때문이다. 새 방송을 세우려면 `LIVE_STOP`을 먼저 보내야 한다 — §5.1 참고.

### 6.5 재접속이 덮지 않는 경우 — MQTT가 함께 끊길 때

§6.4의 재접속은 **Icecast 연결만** 다룬다. **MQTT 브로커 연결이 끊기면 단말은 진행 중인 LIVE와 FILE을 즉시 중단한다.** 스트림 재접속은 시도하지 않는다 — 명령 채널이 끊긴 이상 방송 세션 자체를 유지할 근거가 없기 때문이다.

**연결을 되살리는 것과 방송을 되살리는 것은 별개다.** 단말은 끊긴 연결은 어느 쪽이든 스스로 다시 붙지만, 한번 끝난 방송을 스스로 다시 트는 일은 없다.

| 끊긴 것 | 진행 중이던 방송 | 연결 복구 |
|---|---|---|
| Icecast만 | §6.4의 재접속 3회, 성공하면 **그대로 이어짐** | 단말이 같은 `stream_url`로 재접속 |
| MQTT만 | **즉시 종료** | 단말이 브로커에 자동 재접속 |
| 둘 다 | **즉시 종료** | MQTT는 자동 재접속. 스트림은 방송이 끝났으므로 재접속하지 않음 |

**Icecast와 MQTT 브로커가 같은 호스트에 있으면 이 차이가 드러난다.** 그 호스트가 잠깐 끊기면 §6.4의 재접속은 소용이 없고, 방송은 그 자리에서 끝난다. 두 서비스를 분리해 두면 Icecast 순단은 재접속으로 흡수된다. **단말 쪽에서 선택할 수 있는 사항이 아니라 서버 구성에 달린 문제**라 여기 적어 둔다.

MQTT 재접속은 1초에서 시작해 실패할 때마다 간격을 늘리며 최대 60초까지 간다(keepalive 20초). 포기하지 않고 계속 시도하므로 **브로커가 돌아오면 단말도 돌아온다.** 그때 `STATUS`를 다시 올리니 서버는 복귀를 알 수 있다.

**그러나 돌아온 단말은 조용하다.** 끊길 때 종료된 방송을 스스로 다시 틀지 않는다. 다시 내보내려면 서버가 새 `job_id`로 `LIVE_START`를 걸어야 한다.

---

## 7. LIVE_READY

topic:

```text
iotradio/device/<mac_nocolon>/result
```

payload:

```json
{
  "type": "LIVE_READY",
  "ver": 267,
  "job_id": 101,
  "device": "58:e6:c5:f2:cc:74",
  "status": 0,
  "reason": 0
}
```

status:

| 값 | 의미 |
|---:|---|
| 0 | READY |
| 1 | TIMEOUT |
| 2 | ABORT |
| 3 | FAIL 또는 BUSY |

reason:

| 값 | 의미 |
|---:|---|
| 0x00 | OK |
| 0x01 | FAIL/ABORT |
| 0x03 | TIMEOUT |
| 0x08 | BUSY |

`LIVE_READY status=0`은 P4의 실제 오디오 출력 준비가 완료되었다는 의미다.
외부 AMP 사용 모델은 `LIVE_START` 수신 후 P4가 AMP 전원 ON 및 안정 대기 시간을 먼저 처리하고, 준비가 끝난 뒤에만 `LIVE_READY status=0`을 반환한다.
AMP 안정 대기 중에는 `LIVE_READY`를 보내지 않으며, 서버는 `STATUS state=LIVE, busy=1` 상태로 준비 중임을 판단한다.
외부 AMP 안정 시간이 긴 모델은 서버가 보내는 `ready_timeout_sec`를 AMP 안정 시간보다 충분히 크게 설정해야 한다.

### 7.1 스트림 연결 실패 시 두 번째 LIVE_READY

**`LIVE_READY status=0`만으로 방송 성공을 판단하면 안 된다.**

§6 동작 순서대로 `LIVE_READY status=0`은 Icecast 접속을 시도하기 **전에** 발행된다. 그 뒤 스트림 연결이 실패하면(mount 없음, 네트워크 오류 등) 단말은 같은 `job_id`로 **두 번째 `LIVE_READY`를 `status=2`(ABORT), `reason=0x01`로 발행**한다.

```json
{"type":"LIVE_READY","ver":267,"job_id":101,"device":"58:e6:c5:f2:cc:74","status":0,"reason":0}
{"type":"LIVE_READY","ver":267,"job_id":101,"device":"58:e6:c5:f2:cc:74","status":2,"reason":1}
```

서버는 이 두 번째 메시지를 방송 실패 신호로 처리한다. 함께 `STATUS`도 `state=IDLE, busy=0`으로 돌아온다.

실측 예(2026-08-24, mount 미생성 상태에서 `LIVE_START`):

```text
LIVE_CTRL_READY session=161 status=0 reason=0x00     <- LIVE_READY status=0 발행
icecast: connect url=http://192.168.0.41:8000/live
icecast: HTTP status=404
[ICECAST] stream inactive, clear live state
STATUS ... "state":"IDLE","busy":0
LIVE_CTRL_READY session=161 status=2 reason=0x01     <- LIVE_READY status=2 발행
```

이 실패를 줄이려면 서버가 `LIVE_START`를 발행하기 **전에** 해당 mount에 source를 붙여 두어야 한다(§6.2).

---

## 8. LIVE_STOP

payload:

```json
{
  "type": "LIVE_STOP",
  "job_id": 101
}
```

동작:

- C6가 Icecast stream stop
- C6가 P4에 live stop 전달
- P4가 live audio 정리 및 outro 처리

---

## 9. LIVE_STATS

topic:

```text
iotradio/device/<mac_nocolon>/status
```

payload:

```json
{
  "type": "LIVE_STATS",
  "ver": 267,
  "job_id": 101,
  "p4_buffer_ms": 2080,
  "underrun_count": 1,
  "decode_error_count": 0,
  "rx_seq_last": 1062,
  "rec_overflow": 0
}
```

LIVE_STATS 발행 주기는 CONFIG `live_stats_interval_sec`를 따른다.

---

## 10. Icecast Opus 조건

권장 FFmpeg 송출 조건:

```text
-c:a libopus
-b:a 24k
-ac 1
-ar 16000
-application voip
-frame_duration 40
-page_duration 40000
-flush_packets 1
-content_type application/ogg
```

`-frame_duration 40`은 P4/C6 `frame_ms=40`과 맞추기 위한 조건이다.

`-page_duration 40000`은 Ogg page burst를 줄이기 위한 조건이다.

---

## 11. FILE_START

파일 방송은 MQTT로 명령만 전달하고, 실제 파일은 HTTP(S) URL로 다운로드한다.

payload:

```json
{
  "type": "FILE_START",
  "job_id": 201,
  "size": 73990,
  "resume_offset": 0,
  "sha256": "071e5e40ad46b332a4f6d013625dcccd522ecc5f72336a088789bd3d4ad3d556",
  "https_url": "http://192.168.0.5:9002/file/notice.mp3",
  "file_name": "notice.mp3",
  "store_flash": 1,
  "autoplay": 1
}
```

필수 조건:

- `https_url`은 비어 있으면 안 된다.
- `size`는 0이면 안 된다.
- `sha256`은 실제 파일 bytes 기준 64 hex 문자열을 권장한다.
- 1차 테스트에서는 `https_url` 필드명에 `http://` URL 사용 가능하다.
- `resume_offset`은 현재 단말에서 지원하지 않는다. 0이 아닌 값을 보내면 단말은 `FILE_ABORT reason=BAD_FIELD`로 즉시 중단한다. 다운로드가 중간에 끊겼을 때의 이어받기는 **단말이 알아서 처리**하므로 서버가 `resume_offset`으로 지시할 필요가 없다 — §11.1 참고.
- `autoplay`를 생략하면 단말은 기본값 `true`로 간주한다(자동재생 안 함을 명시하려면 `false`를 반드시 넣어야 한다).
- `file_name`은 그대로 저장되지 않는다. 규칙은 §11.2에 있다.
- `https_url`의 파일명과 `file_name`은 **서로 달라도 된다.** `https_url`은 받아올 주소이고 `file_name`은 단말에 저장할 이름이라 쓰임이 다르다. 다만 굳이 다르게 할 이유가 없으면 같게 두는 편이 추적하기 쉽다.
- `file_name`을 생략하면 단말은 `file.mp3`로 저장한다. 모든 파일이 같은 이름이 되어 구분이 epoch뿐이므로 **넣어 주는 것을 권장한다.**

### 11.1 다운로드가 끊기면 — 자동 이어받기 3회

다운로드 중 네트워크가 끊기면 단말이 **처음부터 다시 받지 않고 끊긴 지점부터 이어받는다.** 서버는 아무것도 하지 않아도 된다.

```text
전송 중 끊김
  → 같은 URL에 Range: bytes=<받은지점>- 으로 재요청
  → 0.5초 → 2초 → 4초 간격으로 최대 3회
  ├ 성공 → 이어받아 완료, FILE_END 발행
  └ 실패 → FILE_ABORT 발행 (reason 은 마지막 실패 원인)
```

서버측 요구사항:

- **Range 요청(206 Partial Content)을 지원해야 이어받기가 동작한다.** 지원하지 않아 `200`으로 전체를 다시 주면 단말이 그것을 감지해 처음부터 다시 받는다(느려질 뿐 파일은 정상). 지원 여부와 무관하게 파일이 깨지지는 않는다.
- 재시도 동안 URL이 유효해야 한다. 일회용 토큰 URL이면 첫 실패 후 재시도가 전부 실패한다.

단말 동작 참고:

- 재시도는 **3회, 각 시도의 연결 타임아웃 5초, 간격 0.5/2/4초**다. 파일서버가 접속을 즉시 거부하는 경우 **약 6.5초**짜리 끊김까지 버티고, 매번 타임아웃까지 가는 최악의 경우에도 26.5초로 P4의 파일 수신 무응답 타임아웃(30초)보다 먼저 끝난다.
- HTTP 상태 코드를 확인한다. `4xx`는 재시도해도 소용없으므로 즉시 `FILE_ABORT reason=BAD_FIELD`로 끝낸다. `5xx`와 연결 실패는 재시도 대상이다.
- 본문이 `size`보다 짧게 끝나면(중간 절단) 완료로 보지 않고 이어받기를 시도한다.

진행 중 재시도는 서버에 따로 보고하지 않는다. **최종 결과(`FILE_END` 또는 `FILE_ABORT`)만 보면 된다.**

---

### 11.2 `file_name` 규칙 — LIVE 녹음과 FILE 다운로드 공통

**단말은 받은 이름을 그대로 저장하지 않는다.** LIVE의 `record_flash`와 FILE의 `store_flash` 둘 다 아래 같은 변환을 거친다.

| 단계 | 처리 |
|---|---|
| 1 | 경로 구분자(`/` `\` `:`) 앞을 버리고 파일명만 남긴다 |
| 2 | **`0-9 A-Z a-z - _` 외의 모든 문자를 `_`로 바꾼다** |
| 3 | 확장자가 없으면 기본값을 붙인다 (LIVE `.lopus` / FILE `.mp3`) |
| 4 | 이름에 epoch가 없으면 `-<epoch>`를 붙인다 |
| 5 | 끝에 `-W`를 붙인다 (서버에서 온 파일이라는 표식) |

전체 64바이트를 넘는 부분은 잘린다.

#### 한글을 보내면 안 된다

2단계는 **바이트 단위**로 동작한다. UTF-8 한글은 모든 바이트가 걸리므로 글자마다 `_` 세 개가 된다.

| 보낸 이름 | 저장되는 이름 |
|---|---|
| `산불방재 안내.mp3` | `___________________-<epoch>-W.mp3` |
| `마을회의.mp3` | `____________-<epoch>-W.mp3` |

읽을 수 없을 뿐 아니라, **바이트 길이가 같은 다른 한글 이름은 구분이 사라진다**(epoch만 다르다).

**한글 제목은 서버가 가지고 있으면 된다.** 단말은 `job_id`/`file_id`로 응답하므로 서버가 그것으로 제목을 이어 붙이면 된다. 단말에 한글을 보낼 이유가 없다.

#### 확장자는 `.mp3`와 `.lopus`만 인정된다

단말의 재생목록은 `-W.mp3`와 `-W.lopus`로 끝나는 파일만 잡는다. `.wav`, `.ogg`, `.m4a`로 보내면 **저장은 되지만 재생목록에서 사라진다.**

#### 서버가 보낼 형식

```text
FILE : notice.mp3         notice-1780000001-W.mp3 처럼 만들 필요 없다
LIVE : 생략하거나 live.lopus
```

- **ASCII 영숫자와 `-` `_`만** 쓴다.
- **epoch와 `-W`는 붙이지 않는다.** 단말이 알아서 붙인다. (이미 붙여 보내도 단말이 중복을 걸러내므로 기존 형식도 그대로 동작한다.)
- 확장자는 FILE `.mp3`, LIVE는 생략(`.lopus`가 기본).

## 12. FILE_END

topic:

```text
iotradio/device/<mac_nocolon>/result
```

성공:

```json
{
  "type": "FILE_END",
  "ver": 267,
  "job_id": 201,
  "verify_ok": true
}
```

실패:

```json
{
  "type": "FILE_END",
  "ver": 267,
  "job_id": 201,
  "verify_ok": false,
  "fail_reason": "SHA256_FAIL"
}
```

`fail_reason`:

| 값 | 의미 |
|---|---|
| `SHA256_FAIL` | sha256 불일치 |
| `STORAGE_FAIL` | flash 저장 실패 |
| `BAD_FIELD` | 필수 필드 오류 |
| `NO_PSRAM` | PSRAM 부족 |
| `DL_FAIL` | 위 항목에 해당하지 않는 그 외 다운로드/전달 실패 (기본값) |

---

## 13. FILE_ABORT

payload:

```json
{
  "type": "FILE_ABORT",
  "ver": 267,
  "job_id": 201,
  "last_offset": 0,
  "reason": "USER_CANCEL"
}
```

reason:

```text
STORAGE_FAIL
PREEMPTED_BY_LIVE
CREDIT_TIMEOUT
NET_ERROR
BAD_FIELD
USER_CANCEL
NO_PSRAM
BUSY
```

`CREDIT_TIMEOUT`은 C6가 받은 HTTP file data를 P4로 넘기는 과정에서 SDIO 흐름 제어 응답이 제한 시간 안에 오지 않았다는 의미다.

---

## 14. FILE_STOP

payload:

```json
{
  "type": "FILE_STOP",
  "job_id": 201
}
```

동작:

- 진행 중인 HTTP 파일 다운로드 중지
- 다운로드 완료 후 P4 자동재생 중인 파일 방송 중지
- 실제 파일 방송이 취소되면 `FILE_ABORT reason=USER_CANCEL` publish
- 취소할 파일 방송이 없으면 `FILE_STOP_RESULT reason=NOT_ACTIVE` publish

FILE_STOP_RESULT 예:

```json
{
  "type": "FILE_STOP_RESULT",
  "ver": 267,
  "job_id": 201,
  "status": 1,
  "reason": "NOT_ACTIVE"
}
```

---

## 15. ONLINE OTA

ONLINE OTA 명령과 상태 사양은 아래 최종 문서를 기준으로 한다.

```text
spec/update/UPDATE_SPEC_FINAL_2026-08-15.md
```

서버는 `OTA_START`만 전송한다. 단말은 다운로드, 검증, 적용, 재부팅까지 자동 진행한다.
pkg 다운로드와 검증이 끝나면 C6가 `OTA_STATUS state=COMPLETED`를 QoS 1로 보고한다.
이후 P4가 C6 네트워크 종료를 지시하고, SDIO OTA 경로로 C6/P4 flash 적용을 진행한다.
서버는 `COMPLETED` 이후 MQTT 연결 종료를 정상 OTA 적용 흐름으로 본다.

---

## 16. QoS 권장

| 메시지 | 방향 | 권장 QoS |
|---|---|---:|
| LIVE_START | 서버 -> 단말 | 1 |
| LIVE_STOP | 서버 -> 단말 | 1 |
| FILE_START | 서버 -> 단말 | 1 |
| FILE_STOP | 서버 -> 단말 | 1 |
| CONFIG | 서버 -> 단말 | 1, retained |
| LIVE_READY | 단말 -> 서버 | 1 |
| FILE_END | 단말 -> 서버 | 1 |
| FILE_ABORT | 단말 -> 서버 | 1 |
| FILE_STOP_RESULT | 단말 -> 서버 | 1 |
| STATUS 주기 | 단말 -> 서버 | CONFIG `event_qos` |
| LIVE_STATS | 단말 -> 서버 | CONFIG `event_qos` |
| OTA_STATUS 최종 상태 | 단말 -> 서버 | 1 |

QoS 1 명령은 중복 전달 가능성이 있으므로 서버와 단말 모두 `job_id` 기준 중복 처리 정책을 둔다.

---

## 17. 2차 검토 항목

- MQTTS/TLS
- MQTT ACL
- HTTP(S) 파일 서버 인증
- Nginx 또는 reverse proxy 구성
- 펌웨어 서명 검증
- 운영 서버 CONFIG retained 백업/복원
