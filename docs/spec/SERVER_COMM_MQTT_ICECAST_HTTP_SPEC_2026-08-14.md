# SERVER COMM MQTT ICECAST HTTP SPEC 2026-08-14

작성: 2026-08-14. 갱신: 2026-08-30.

### 개정 이력

| 날짜 | 바뀐 것 |
|---|---|
| 08-30 | **단말별 계정으로 확정.** username = 단말 MAC, ACL은 `%u` (§2.1, §3.1, §3.2) |
| 08-30 | 계정은 **펌웨어에 없다.** 생산에서 시리얼로 넣는다 (§3.2) |
| 08-14 | 최초 작성 |

---

정리: 2026-08-24 (Claude) — C6 소스코드(`net_client.c`) 재대조. FILE_START `resume_offset`/`autoplay` 기본 동작, FILE_END `fail_reason` 전체 표 보강. 나머지 필드/topic/QoS 정의는 코드와 이미 일치함을 확인.

갱신: 2026-08-24 (Claude) — `LIVE_START.stream_url` 단말 구현 완료에 따라 §6 재작성(필드표, §6.1 처리 규칙, §6.2 mount 구조와 단말의 관계, §6.3 https 지원 시점), §7.1 신설(스트림 실패 시 두 번째 `LIVE_READY status=2` 발행 — 기존 사양서에 누락되어 있던 실제 동작).

갱신: 2026-08-24 (Claude) — §6.2 재작성. mount 개수/경로 구조는 서버가 정할 영역이고 단말 동작과 무관하므로, 이 문서에서 서버 설계를 규정하던 서술을 걷어내고 단말 동작(파싱 안 함, LIVE_STOP 놓친 단말의 잔류, 마을 topic 독립성)만 남겼다. §5 우선순위 문장 정정. `OTA > LIVE > FILE > RF > IDLE`은 STATUS 표시 순서일 뿐인데 명령 우선순위로 읽힐 여지가 있었다. 실제 명령 수락은 선착순이고 선점이 없다는 §5.1(거부 응답표 포함), 서버가 정지→확인→재시작 순서를 만들어야 한다는 §5.2, 정지 직후 명령이 거부되는 이유인 §5.3을 신설했다. 코드(`mqtt_state_name()`, 각 명령의 busy 검사)와 대조해 작성.

갱신: 2026-08-25 (Claude) — **재접속 관련 서술은 이 문서가 최종이다.** 그동안 두 차례 정정이 오갔는데(처음 "3초 주기 재접속" → 이후 "재접속하지 않음"), 이제 단말에 재접속이 구현되어 아래 §6.4가 실제 동작이다. 서버가 할 일은 **방송이 끝날 때까지 `stream_url`을 유지하는 것 하나**이고 그 외 추가 조치는 없다. 재접속이 덮지 않는 경우(MQTT 동반 단절)는 §6.5에 새로 정리했다. 이하 종전 갱신 내용: 자동 재시도 정책 반영. LIVE 스트림 끊김 시 3회/8초 이내 자동 재접속(§6.4), FILE 다운로드 끊김 시 Range 기반 자동 이어받기 3회(§11.1)를 단말에 구현하고 문서화했다. 재접속 중 상태를 알리려고 그동안 항상 0으로만 나가던 STATUS `reason` 필드에 값을 정의했다(§5, 0=정상 / 1=LIVE 재접속 중). 새 메시지 타입은 추가하지 않았다.

ESP32-P4+C6 수신기와 서버 사이의 MQTT, Icecast, HTTP 파일 전송 사양이다.

업데이트 사양은 `spec/update/UPDATE_SPEC_FINAL_2026-08-15.md`를 최종 기준으로 한다.

---

**2026-08-27 — 결과 메시지 형식이 바뀌었다.** `ok` 불리언과 `code` 문자열로 통일하고, `FILE_END`/`FILE_ABORT`/`FILE_STOP_RESULT`를 `FILE_RESULT` 하나로 합쳤다. `LIVE_READY`는 준비 결과만 말하고 종료는 `LIVE_RESULT`가 맡는다. `OTA_STATUS`는 `OTA_PROGRESS`와 `OTA_RESULT`로 나뉘었다. 상세는 §5.4.


## 1. 기본 구성

| 항목 | 운영 (TLS) | 사내 PC 테스트 |
|---|---|---|
| MQTT broker | `mqtts://<host>:8883` | `mqtt://192.168.0.5:1883` |
| Icecast live stream | `https://<host>/live` | `http://192.168.0.5:8000/live` |
| HTTP file server | `https://<host>/file/...` | `http://192.168.0.5:9002/file/...` |
| OTA pkg server | `https://<host>/update/IOT_RADIO.pkg` | `http://192.168.0.5:9002/update/IOT_RADIO.pkg` |

**운영 서버는 평문을 열지 않는다.** 오른쪽 열은 사내 PC로 시험할 때만 쓰고, 그때는 단말도 TLS를 끈 빌드를 쓴다. 이 문서의 예제 payload에 사설 IP가 나오는 것은 그 시험 구성에서 캡처한 것이다.

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

다른 topic은 MAC이 들어가므로 ACL이 **username 치환(`%u`)** 으로 잡을 수 있다 — username이 그 단말의 MAC이다(§3.2). **이 topic만 MAC이 아니라 서버가 배정한 `village_id`가 들어간다.**

#### ACL이 막는 것과 막지 못하는 것

**막는 것: 오발행.** 펌웨어 결함이나 설정 실수로 단말이 남의 topic에 발행하는 것을 브로커가 거절한다. 한 단말의 문제가 다른 마을 방송으로 번지지 않는다.

**막는 것: 사칭.** `%u`는 **비밀번호로 증명된 값**이다. 남의 MAC을 `client_id`로 대도 그 MAC의 password가 없으면 접속 자체가 안 되고, 접속했더라도 ACL은 자기 username 자리만 열어준다.

> **`%c`로 걸면 안 된다.** `client_id`는 접속하는 쪽이 제시하는 문자열일 뿐이라 아무 값이나 댈 수 있다. 공유 계정 시절에는 이것이 사칭을 막지 못하는 원인이었고, 단말별 계정으로 바꾼 이유가 그것이다.

계정과 발행 절차는 `SERVER_DEVICE_CREDENTIAL_SPEC_2026-08-27.md`에 있다.

`%u`로도 잡히지 않으므로 별도 규칙이 필요하다 — 그 자리에 들어가는 것이 MAC이 아니라 서버가 배정한 값이기 때문이다. 빠뜨리면 단말은 정상으로 보이는데 **마을 방송만 안 오는** 상태가 된다.

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

**username과 같은 값이다**(§3.2). ACL은 username(`%u`)으로 판정하므로 권한을 정하는 것은 `client_id`가 아니지만, 브로커 로그와 topic을 대조할 때 둘이 같아야 읽힌다.

값이 topic의 MAC과 다르면 **접속은 되지만 발행과 구독이 조용히 무시된다** — 에러가 없어서 단말은 정상으로 보이는데 서버 화면에는 나타나지 않는다.

이 형식이 사양에 없어서 단말이 `iotradio-<mac>`을 쓰고 있었고, 2026-08-26 운영 서버 전환 때 드러났다.

### 3.2 브로커 인증

운영 브로커는 익명 접속을 받지 않는다. **단말마다 계정이 다르다.**

| | 값 |
|---|---|
| **username** | 콜론 없는 소문자 MAC. `client_id`와 같은 값 |
| **password** | MAC별 랜덤 8자. **서버가 발행하고 DB에 보관** |

**계정은 펌웨어에 들어 있지 않다.** 생산 등록에서 시리얼로 넣는다(`@MQTTID`/`@MQTTPW`). 그래서 펌웨어를 덤프해도 계정이 나오지 않고, 하나가 뚫려도 **그 단말 하나로 끝난다.**

ACL은 **`%u`** 로 건다(§2.1). password 규칙과 발행 절차는 `SERVER_DEVICE_CREDENTIAL_SPEC_2026-08-27.md`에 있다.

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
  "ip": "192.168.0.21",
  "rssi": -36,
  "state": "IDLE",
  "busy": 0,
  "live": "OFF",
  "config_version": 36,
  "p4_fw": "V.260823-1",
  "c6_fw": "V.260823-1"
}
```

#### `wifi` / `mqtt` 삭제 — 2026-08-27

**두 필드는 없앴다. 서버는 파싱하지 않는다.**

정보가 없는 값이었다. 주기 STATUS는 MQTT로 나가므로 발행 시점에는 반드시 연결돼 있고(`mqtt_publish_status_now()`가 연결 안 됐으면 그냥 돌아간다), Wi-Fi가 없으면 발행 자체가 불가능하다. 그래서 단말이 보내는 STATUS에서 두 값은 **항상 1**이었다. 0이 나오는 곳은 LWT뿐이었는데, 거기에는 `state:"OFFLINE"`이 이미 같은 말을 하고 있었다.

연결 상태를 판단하는 방법은 그대로다.

| 알고 싶은 것 | 봐야 할 것 |
|---|---|
| 살아 있나 | STATUS가 주기대로 도착하는지 |
| 끊겼나 | LWT의 `state == "OFFLINE"` |
| 무선 품질 | `rssi` |

#### `p4_fw` / `c6_fw` — 2026-08-27 추가

**OTA가 실제로 적용됐는지 확인하는 값이다.**

| 필드 | 내용 |
|---|---|
| `p4_fw` | P4 펌웨어 버전. HELLO로 C6에 전달된 값 |
| `c6_fw` | C6 펌웨어 버전 |

`spec/update/UPDATE_SPEC_FINAL_2026-08-15.md`는 **재부팅 후 STATUS의 새 펌웨어 버전으로 OTA 최종 성공을 확인**하라고 하는데, 그 필드가 없었다. 이제 있다.

```text
OTA_RESULT ok=true -> 연결 끊김 -> 재부팅 -> STATUS 도착
                                            p4_fw 가 새 버전  -> 성공
                                            p4_fw 가 옛 버전  -> 롤백됨
```

**재부팅했다는 것과 새 펌웨어가 올라갔다는 것은 다르다.** 롤백을 구분할 방법이 이 값뿐이다.

OTA 확인 외에도 쓸모가 있다 — 현장에 어떤 버전이 몇 대 있는지 서버가 항상 알게 된다.

**주기 STATUS마다 들어간다.** 부팅 후 한 번만 보내면 서버가 그 한 건을 놓쳤을 때(재시작, retained 유실) 복구할 방법이 없다. payload는 42바이트 늘어난다.

`p4_fw`가 빈 문자열이면 **C6가 아직 P4에게서 값을 못 받은 것**이다(부팅 직후 짧은 구간). 그 STATUS로는 버전을 판정하지 않는다.

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

`live` 값:

| 값 | 의미 |
|---|---|
| `OFF` | 실시간 방송 중이 아니다 |
| `PLAYING` | 실시간 방송을 받아 재생 중이다 |
| `RECONNECTING` | LIVE 스트림이 끊겨 재접속 시도 중. **방송은 아직 살아있지만 스피커로 소리가 나가지 않는 상태**다 |

서버는 `live == "RECONNECTING"`을 "방송 중인데 무음"으로 읽으면 된다. 재접속에 성공하면 즉시 `live="PLAYING"`인 STATUS가 다시 오고, 실패하면 방송이 끝나면서 `state=IDLE`, `live="OFF"`가 온다. 상세는 §6.4.

**예전 숫자형 `reason` 필드는 없다.** 같은 뜻을 문자열 `live`로 바꿨다(0 -> `OFF`/`PLAYING`, 1 -> `RECONNECTING`).

여러 동작이 동시에 걸려 있을 때 STATUS의 `state`에 무엇을 실을지는 `OTA > LIVE > FILE > RF > IDLE` 순으로 정한다.

**이 순서는 STATUS 표시용이며, 명령의 우선순위가 아니다.** 예를 들어 파일 방송 중에 `LIVE_START`를 보내면 "LIVE가 FILE보다 위"라서 선점되는 것이 아니라 거부된다. 실제 명령 수락 규칙은 아래 §5.1을 따른다.

단말은 상태가 변경되면 주기 STATUS를 기다리지 않고 STATUS를 1회 즉시 발행한다.
상태 변경 즉시 STATUS의 QoS는 CONFIG `event_qos`를 따른다.

`OFFLINE`은 단말이 정상 publish로 직접 보내는 상태가 아니다.
단말은 MQTT 연결 시 LWT(Last Will)를 status topic에 등록한다. LWT payload는 `type`, `device`, `village_id`, `state` 넷뿐이다.
전원 차단, Wi-Fi 단절, 비정상 리셋 등으로 MQTT 연결이 끊기면 broker가 LWT payload를 status topic에 publish한다.
서버는 status topic을 구독하고 있다가 이 LWT 메시지를 수신하면 해당 단말을 OFFLINE으로 표시한다.
정상 재부팅/OTA처럼 의도된 종료 흐름에서는 LWT가 발행되지 않을 수 있으므로, `OTA_RESULT`와 재접속 후 STATUS를 함께 기준으로 판단한다.

### 5.1 명령 수락 규칙 — 선착순, 선점 없음

**단말은 이미 진행 중인 동작을 새 명령으로 중단시키지 않는다.** 무엇이든 먼저 시작한 것이 끝날 때까지 단말을 차지하고, 그 사이에 도착한 LIVE/FILE/OTA 명령은 전부 거부된다.

| 진행 중 | `LIVE_START` | `FILE_START` | `OTA_START` |
|---|---|---|---|
| 없음 | 수락 | 수락 | 수락 |
| OTA | `LIVE_READY ok=false code=BUSY` | `FILE_RESULT ok=false code=BUSY` | `OTA_RESULT ok=false code=BUSY` |
| LIVE | `LIVE_READY ok=false code=BUSY` | `FILE_RESULT ok=false code=PREEMPTED_BY_LIVE` | `OTA_RESULT ok=false code=BUSY` |
| FILE | `LIVE_READY ok=false code=BUSY` | `FILE_RESULT ok=false code=BUSY` | `OTA_RESULT ok=false code=BUSY` |

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

## 5.4 명령 결과 — `ok` 하나로 판정한다

**서버가 기억할 것이 없다. 메시지 하나만 보면 끝난다.**

```json
{ "type":"FILE_RESULT", "ver":267, "job_id":201, "ok":true, "code":"OK" }
```

| 필드 | 뜻 |
|---|---|
| `ok` | **성공/실패는 이 값 하나가 정한다.** 표를 안 봐도 된다 |
| `code` | 왜 그렇게 됐는지. 로그에서 그대로 읽힌다 |

### 규칙 넷

1. **한 명령에 결과 메시지 하나.** 타입이 항상 같다
2. **`ok`가 성공/실패를 정한다.** 다른 필드를 보고 뒤집지 않는다
3. **`code`는 상황마다 유일하다.** 같은 값이 두 뜻을 갖지 않는다
4. **진행 알림과 결과는 다른 타입이다**

### 서버 판정 절차

```text
1. *_RESULT 를 받는다   -> 그 job은 끝났다
2. ok 를 본다           -> 성공인가 실패인가
3. 실패면 code 를 남긴다 -> 원인
```

---

### LIVE_START

**`LIVE_READY`(준비)와 `LIVE_RESULT`(종료)로 나뉜다.** 각각 한 번씩 온다.

`LIVE_READY` — 준비 결과만 말한다. **이 시점에는 아직 스트림에 붙기 전이다.**

| ok | code | 뜻 |
|---|---|---|
| `true` | `OK` | P4 오디오 준비 완료 |
| `false` | `BUSY` | 다른 작업(OTA/FILE/LIVE) 진행 중 |
| `false` | `BAD_FIELD` | `stream_url` scheme 오류, `job_id`=0 |
| `false` | `TIMEOUT` | P4가 준비 제한시간을 넘김 |

`LIVE_RESULT` — 종료 결과만 말한다.

| ok | code | 뜻 |
|---|---|---|
| `true` | `STOPPED_BY_SERVER` | 서버 `LIVE_STOP`으로 정상 종료 |
| `false` | `ABORTED` | P4가 방송을 접음 |
| `false` | `TIMEOUT` | 15초 동안 프레임이 없어 정리됨 |

**`ok=true`면 정상이다.** 예전에는 같은 `status=2`가 정상과 실패 양쪽이라 서버가 "내가 STOP을 보냈던가"를 기억해야 했다. 이제 **P4가 구분해서 말한다.**

### LIVE_STOP

`LIVE_RESULT` 하나로 답한다.

| ok | code |
|---|---|
| `true` | `STOPPED_BY_SERVER` |
| `false` | `NOT_ACTIVE` — 정지할 방송이 없었다 |

### FILE_START / FILE_STOP

**`FILE_RESULT` 하나로 답한다.** 성공이든 실패든 같은 타입이다.

| ok | code | 뜻 |
|---|---|---|
| `true` | `OK` | 다운로드·검증·저장 완료 |
| `true` | `STOPPED_BY_SERVER` | 서버 `FILE_STOP`으로 취소됨 |
| `false` | `BAD_FIELD` | URL 누락·scheme 오류, `.mp3` 아닌 확장자, 크기 초과, `job_id`=0 |
| `false` | `BUSY` | 다른 작업 진행 중 |
| `false` | `PREEMPTED_BY_LIVE` | LIVE 방송 중이라 거절 |
| `false` | `NET_ERROR` | 다운로드 실패 |
| `false` | `VERIFY_FAIL` | sha256 불일치 |
| `false` | `STORAGE_FAIL` | flash 저장 실패. **검증은 통과했으므로 재생은 된다** |
| `false` | `NO_MEMORY` | 수신 버퍼 확보 실패 |
| `false` | `CREDIT_TIMEOUT` | P4가 청크를 받아가지 못함 |
| `false` | `NOT_ACTIVE` | `FILE_STOP`에 정지할 대상이 없었다 |

`last_offset`이 함께 온다. 중단된 위치다.

### OTA_START

**`OTA_PROGRESS`(진행)와 `OTA_RESULT`(결과)로 나뉜다.**

`OTA_PROGRESS` — 25% 단위로 온다. **최종이 아니다.**

```json
{ "type":"OTA_PROGRESS", "job_id":20, "state":"DOWNLOADING", "percent":50,
  "received":3600000, "total_size":7297148 }
```

`state`: `ACCEPTED` `PREPARE` `DOWNLOADING` `VERIFYING`

`OTA_RESULT` — **job당 한 번만** 온다. QoS 1이다.

| ok | code | 뜻 |
|---|---|---|
| `true` | `OK` | 적용 완료. 곧 연결이 끊기고 재부팅한다 |
| `false` | `BAD_FIELD` | `url` scheme 오류, `job_id`/`size`=0 |
| `false` | `BUSY` | 다른 작업 진행 중 |
| `false` | `NO_MEMORY` | PSRAM 확보 실패 |
| `false` | `DL_FAIL` | 다운로드 실패 |
| `false` | `VERIFY_FAIL` | sha256 불일치 |
| `false` | `APPLY_FAIL` | flash 적용 실패 |

`ok=true` 뒤 연결이 끊기는 것은 정상이다. **성공 확인은 재부팅 후 `STATUS`의 `p4_fw`/`c6_fw`로 한다**(§5).

### STATUS의 `live`

명령 결과가 아니라 **현재 상태**다.

| 값 | 뜻 |
|---|---|
| `OFF` | 방송 중이 아니다 |
| `PLAYING` | 방송 중 |
| `RECONNECTING` | **스트림 재접속 중. 방송은 살아 있고 스피커만 무음이다** |

`RECONNECTING`은 실패가 아니다. 복구하면 `PLAYING`, 복구하지 못하면 `OFF`가 되면서 `LIVE_RESULT`가 온다.

---

### code를 만드는 쪽

**한 code는 한 곳에서만 나온다.** 로그에서 code를 보면 어느 칩을 열지 정해진다.

| code | 만드는 쪽 | 왜 |
|---|---|---|
| `STOPPED_BY_SERVER` `TIMEOUT` `ABORTED` `VERIFY_FAIL` `STORAGE_FAIL` `NO_MEMORY` `CREDIT_TIMEOUT` | **P4** | 오디오·검증·저장은 P4가 한다 |
| `NET_ERROR` `DL_FAIL` `PREEMPTED_BY_LIVE` | **C6** | 네트워크와 스트림은 C6만 안다 |
| `BUSY` `BAD_FIELD` `NOT_ACTIVE` | **먼저 본 쪽** (대개 C6) | 명령을 먼저 받는 쪽이 거절한다 |

**C6는 P4가 준 값을 다시 판단하지 않는다.** 어느 메시지로 옮길지만 고른다.

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
| `record_flash` | 선택 | 기본 0. **10분을 넘기는 방송은 `0`으로 둔다** (§11.2) |
| `file_name` | 선택 | 녹음 저장 파일명. 규칙은 LIVE/FILE 공통이다 — §11.3 참고 |
| `ready_timeout_sec` | 선택 | 기본 30, 1~60으로 보정 |

### 6.1 stream_url

서버는 이번 방송을 수신할 Icecast 주소를 **완성된 URL 문자열**로 `stream_url`에 넣어 내려준다. 단말은 이 값을 그대로 쓴다.

```text
https://<host>/live/<job_id>
```

단말 처리 규칙:

- 단말은 이 문자열을 **파싱하거나 재조립하지 않고 그대로 HTTP GET 대상으로 사용**한다. `village_id`나 `job_id`로 경로를 직접 만들지 않으므로, 서버가 경로 구조를 바꿔도 단말 펌웨어를 고칠 필요가 없다.
- `stream_url`이 없거나 빈 문자열이면 펌웨어 컴파일타임 기본 mount로 접속한다(구버전 서버 호환).
- 최대 길이는 **512바이트**(NUL 포함)다. 이를 넘으면 단말은 URL을 잘라 쓰지 않고 방송 시작을 거절한다. MQTT 명령 payload 전체 상한(1024B)도 함께 지켜야 하므로 서버가 발행 전에 검사한다.
- **스트림이 끊기면 단말이 같은 `stream_url`로 자동 재접속을 시도한다**(§6.4). 복구하지 못하면 그때 방송을 끝내고 `LIVE_RESULT ok=false`를 발행한다(§7.1). 서버는 방송이 끝날 때까지 mount와 source를 유지해야 한다.
- `https://`(포트 생략 포함) 형태는 TLS 전환 이후에 지원된다. 아래 §6.3 참고.

### 6.2 mount 구조는 단말과 무관하다

**mount를 몇 개로 나누든 경로를 어떻게 잡든 단말 동작에는 영향이 없다.** 단말은 받은 `stream_url`로 접속만 하고, 그 주소가 마을 전용 mount인지 여러 마을이 함께 쓰는 mount인지 알지 못한다.

따라서 mount 구성은 서버가 정한다. 이 문서는 단말이 그 값을 어떻게 처리하는지만 규정한다.

관련해서 알아 둘 단말 동작:

- 단말은 `stream_url`을 **파싱하지 않는다.** 경로 구조가 바뀌어도 펌웨어 변경이 필요 없다.
- 단말이 `LIVE_STOP`을 받지 못하면 **접속해 있던 mount에 계속 붙어 있는다.** 그 mount에 다음 방송 오디오가 흐르면 정지 명령을 놓친 단말도 새 방송을 듣게 된다. 방송마다 경로가 달라지는 구성에서는 그런 단말이 새 방송을 받지 못하고 끊긴다.
- 여러 마을에 동시에 방송할 때, 각 단말은 **자기 마을 topic 하나만** 구독하므로 서로 간섭하지 않는다.

### 6.3 stream_url은 https로 발행한다

단말의 Icecast 클라이언트에도 **TLS 인증서 검증(`crt_bundle_attach`)이 붙어 있다.** MQTT·파일 다운로드와 같은 커스텀 번들을 쓰므로 인증서 요건도 같다(`SERVER_TLS_HOST_HANDOFF_2026-08-23.md` §3).

서버는 `stream_url`을 **`https://`로 발행한다.** 평문은 사내 PC 테스트에서만 쓰고 운영 서버는 열지 않는다.

단말이 `stream_url` 없이 host만으로 조립하는 fallback도 **`https://<host>/live`** 다. 이 값은 구버전 서버 호환으로 남겨 둔 것이고, 서버가 매번 `stream_url`을 내려주므로 실제로 쓰이지 않는다.

동작:

1. C6가 MQTT 명령 수신
2. C6가 P4에 live 준비 요청
3. P4가 intro 출력 후 live 준비
4. P4 준비 완료 후 C6가 `LIVE_READY ok=true` publish
5. C6가 `stream_url`(없으면 기본 mount)로 Icecast stream 연결

4번과 5번의 순서에 주의한다. `LIVE_READY ok=true`는 **P4 오디오 출력 준비가 끝났다는 뜻이지 스트림 수신이 시작됐다는 뜻이 아니다.** 5번이 실패하면 §7.1의 `LIVE_RESULT ok=false`가 뒤따른다.

### 6.4 스트림이 끊겼을 때 — 자동 재접속 3회

방송 도중 Icecast 스트림이 끊기면 단말이 **먼저 스스로 3회 재접속을 시도**한다. 서버가 개입할 필요는 없다.

```text
스트림 끊김
  → STATUS reason=1 즉시 발행 (방송 유지, 스피커 무음)
  → 같은 stream_url로 재접속 (0.5초 → 2초 → 4초 간격, 최대 3회)
  ├ 성공 → STATUS reason=0 즉시 발행, 방송 계속
  └ 실패 → LIVE_RESULT ok=false + STATUS state=IDLE live=OFF, 방송 종료
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
  "ok": true,
  "code": "OK"
}
```

`code`:

| ok | code | 의미 |
|---|---|---|
| `true` | `OK` | P4 오디오 준비 완료 |
| `false` | `BUSY` | 다른 작업 진행 중 |
| `false` | `BAD_FIELD` | `stream_url`이 이 빌드가 받지 않는 scheme이거나 `job_id`가 0 |
| `false` | `TIMEOUT` | P4가 준비 제한시간을 넘김 |

`BAD_FIELD`는 **Icecast에 붙기 전에 거절하므로 방송이 시작되지 않는다.**

`LIVE_READY ok=true`는 P4의 실제 오디오 출력 준비가 완료되었다는 의미다.
외부 AMP 사용 모델은 `LIVE_START` 수신 후 P4가 AMP 전원 ON 및 안정 대기 시간을 먼저 처리하고, 준비가 끝난 뒤에만 `LIVE_READY ok=true`를 반환한다.
AMP 안정 대기 중에는 `LIVE_READY`를 보내지 않으며, 서버는 `STATUS state=LIVE, busy=1` 상태로 준비 중임을 판단한다.
외부 AMP 안정 시간이 긴 모델은 서버가 보내는 `ready_timeout_sec`를 AMP 안정 시간보다 충분히 크게 설정해야 한다.

### 7.1 `LIVE_READY ok=true`만으로 방송 성공을 판단하면 안 된다

§6 동작 순서대로 `LIVE_READY`는 Icecast에 붙기 **전에** 나간다. 준비가 됐다는 뜻이지 소리가 나간다는 뜻이 아니다.

그 뒤 스트림 연결이 실패하면(mount 없음, 네트워크 오류) **같은 `job_id`로 `LIVE_RESULT ok=false`가 온다.**

```json
{"type":"LIVE_READY","ver":267,"job_id":101,"device":"58:e6:c5:f2:cc:74","ok":true,"code":"OK"}
{"type":"LIVE_RESULT","ver":267,"job_id":101,"device":"58:e6:c5:f2:cc:74","ok":false,"code":"ABORTED"}
```

`STATUS`도 `state=IDLE, busy=0, live=OFF`로 돌아온다.

**방송이 실제로 나갔는지는 `LIVE_RESULT`로 판단한다.** `ok=true`면 서버가 정지시킨 것이고, `ok=false`면 실패다. **타입과 `ok`만 보면 되고 서버가 기억할 것은 없다.**

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

### 응답

`LIVE_RESULT` 하나로 답한다.

```json
{"type":"LIVE_RESULT","ver":267,"job_id":101,"device":"58:e6:c5:f2:cc:74","ok":true,"code":"STOPPED_BY_SERVER"}
```

**정상 종료는 `ok=true`, `code=STOPPED_BY_SERVER`다.** 정지할 방송이 없었으면 `ok=false, code=NOT_ACTIVE`다.

정상 종료와 실패 종료를 **P4가 구분해서 보낸다.** 서버는 자기가 STOP을 보냈는지 기억할 필요가 없다.

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
- **`job_id`는 0이면 안 된다.** 단말은 0을 "세션 없음"으로 다루므로, 0으로 시작한 방송은 정지 통지가 P4까지 가지 않는다. LIVE/FILE/OTA 모두 같다 — 0으로 오면 거절한다(2026-08-27 반영).
- **`size` 상한은 2,621,440 B (2.5 MiB)다.** 넘으면 `FILE_RESULT ok=false code=BAD_FIELD`로 거절하고, **다운로드는 시작되지도 않는다** — 단말은 `FILE_META`를 P4에 넘긴 뒤 거절 여부를 먼저 확인한다.

  재생 시간이 아니라 **바이트 상한**이다. 몇 분이 들어가는지는 서버가 어떤 비트레이트로 인코딩하는지에 달렸다.

  현재 사양은 **16 kHz 모노 24 kbps**다.

  | MP3 비트레이트 | 2.5 MiB로 담기는 길이 | 10분에 필요한 크기 |
  |---:|---|---|
  | 16 kbps | 21분 50초 | 1.14 MiB |
  | **24 kbps (현재)** | **14분 33초** | **1.72 MiB** |
  | 32 kbps | 10분 55초 | 2.29 MiB |

  **현재 24kbps 기준으로 10분 방송에 1.7배 여유가 있다.** 비트레이트를 24kbps보다 올리기로 하면 알려달라 — 단말의 계산 기준도 같이 올려야 한다.

##### 방송은 10분을 넘기지 않는다

크기와 별개로 **길이 약속이 있다. 단말은 10분을 넘겨 재생하지 않고, 서버도 10분을 넘겨 방송하지 않는다.**

메모리는 그보다 넉넉하지만, 단말의 재생 워치독이 **10분 30초**에서 재생을 끊는다. **크기가 상한 안에 있어도 10분을 넘는 파일은 뒷부분이 나오지 않는다.**

늘리려면 단말 펌웨어를 고쳐야 한다(장치 사양서 §11.1). 필요하면 먼저 얘기해달라.
- `sha256`은 실제 파일 bytes 기준 64 hex 문자열을 권장한다.
- 1차 테스트에서는 `https_url` 필드명에 `http://` URL 사용 가능하다.
- `resume_offset`은 현재 단말에서 지원하지 않는다. 0이 아닌 값을 보내면 단말은 `FILE_RESULT ok=false code=BAD_FIELD`로 즉시 중단한다. 다운로드가 중간에 끊겼을 때의 이어받기는 **단말이 알아서 처리**하므로 서버가 `resume_offset`으로 지시할 필요가 없다 — §11.1 참고.
- `autoplay`를 생략하면 단말은 기본값 `true`로 간주한다(자동재생 안 함을 명시하려면 `false`를 반드시 넣어야 한다).
- `file_name`은 그대로 저장되지 않는다. 규칙은 §11.3에 있다.
- `https_url`의 파일명과 `file_name`은 **서로 달라도 된다.** `https_url`은 받아올 주소이고 `file_name`은 단말에 저장할 이름이라 쓰임이 다르다. 다만 굳이 다르게 할 이유가 없으면 같게 두는 편이 추적하기 쉽다.
- `file_name`을 생략하면 단말은 `file.mp3`로 저장한다. 모든 파일이 같은 이름이 되어 구분이 epoch뿐이므로 **넣어 주는 것을 권장한다.**

### 11.1 다운로드가 끊기면 — 자동 이어받기 3회

다운로드 중 네트워크가 끊기면 단말이 **처음부터 다시 받지 않고 끊긴 지점부터 이어받는다.** 서버는 아무것도 하지 않아도 된다.

```text
전송 중 끊김
  → 같은 URL에 Range: bytes=<받은지점>- 으로 재요청
  → 0.5초 → 2초 → 4초 간격으로 최대 3회
  ├ 성공 → 이어받아 완료, FILE_RESULT ok=true 발행
  └ 실패 → FILE_RESULT ok=false 발행 (code 는 마지막 실패 원인)
```

서버측 요구사항:

- **Range 요청(206 Partial Content)을 지원해야 이어받기가 동작한다.** 지원하지 않아 `200`으로 전체를 다시 주면 단말이 그것을 감지해 처음부터 다시 받는다(느려질 뿐 파일은 정상). 지원 여부와 무관하게 파일이 깨지지는 않는다.
- 재시도 동안 URL이 유효해야 한다. 일회용 토큰 URL이면 첫 실패 후 재시도가 전부 실패한다.

단말 동작 참고:

- 재시도는 **3회, 각 시도의 연결 타임아웃 5초, 간격 0.5/2/4초**다. 파일서버가 접속을 즉시 거부하는 경우 **약 6.5초**짜리 끊김까지 버티고, 매번 타임아웃까지 가는 최악의 경우에도 26.5초로 P4의 파일 수신 무응답 타임아웃(30초)보다 먼저 끝난다.
- HTTP 상태 코드를 확인한다. `4xx`는 재시도해도 소용없으므로 즉시 `FILE_RESULT ok=false code=BAD_FIELD`로 끝낸다. `5xx`와 연결 실패는 재시도 대상이다.
- 본문이 `size`보다 짧게 끝나면(중간 절단) 완료로 보지 않고 이어받기를 시도한다.

진행 중 재시도는 서버에 따로 보고하지 않는다. **최종 결과(`FILE_RESULT`)만 보면 된다.**

---

### 11.2 10분을 넘기는 방송은 저장하지 않는다

앞의 크기 상한(§11)과 길이 약속은 **받는 쪽**을 지킨다. 이 항은 **저장하는 쪽**이다.

| 명령 | 필드 | 10분을 넘길 때 |
|---|---|---|
| `FILE_START` | `store_flash` | **`0`으로 보낸다** |
| `LIVE_START` | `record_flash` | **`0`으로 보낸다** |

`0`이면 단말은 **재생만 하고 flash에 쓰지 않는다.** 방송은 정상으로 나가고 저장만 건너뛴다.

#### 왜 서버가 판단해야 하나

**단말은 길이를 모른다.**

- `FILE_START`에서 오는 것은 `size`(바이트)뿐이다. 몇 분인지 알려면 MP3를 파싱해 프레임을 세야 하는데, 받기 전에는 파일이 없다.
- `LIVE_START` 시점에는 방송이 얼마나 이어질지 아무도 모른다. 끝은 서버가 정한다.

**길이를 아는 쪽은 서버뿐이다.** 그래서 이 판단은 서버에 둔다.

#### 이렇게 하면 무엇이 안전해지나

저장 공간은 유한하고(가용 약 7.7MB, 목록 5개), 긴 방송일수록 한 건이 크게 차지한다. 자리가 모자라면 단말은 **오래된 것부터 지우고** 저장한다(§8.1).

즉 긴 방송 하나가 들어오면 **그 전에 저장해 둔 짧은 방송 여러 건이 밀려난다.** 긴 방송은 대개 한 번 듣고 마는 안내인데, 그것 때문에 다시 들을 만한 방송이 사라지는 것은 손해다.

**10분을 넘기는 것은 저장하지 않는다**로 정해두면 그 교환이 일어나지 않는다.

#### 단말은 이 값을 그대로 따른다

`store_flash=0`, `record_flash=0`을 받으면 단말은 저장을 시도조차 하지 않는다. **길이를 다시 재서 뒤집지 않는다** — 서버가 정한 것을 그대로 실행한다.

라이브의 `record_flash`는 이미 기본값이 `0`이다(§9). **명시적으로 `1`을 보낼 때만 저장하므로**, 긴 방송에서 그 필드를 빼는 것만으로 이 정책이 지켜진다.

### 11.3 `file_name` 규칙 — LIVE 녹음과 FILE 다운로드 공통

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

#### 확장자는 FILE `.mp3`, LIVE `.lopus` 고정이다

**FILE은 `.mp3`가 아니면 거절한다.** `FILE_META` 단계에서 `FILE_RESULT ok=false code=BAD_FIELD`로 돌려보내므로 다운로드도, flash 쓰기도 일어나지 않는다. 대소문자는 가리지 않아 `.MP3`도 통과한다.

**LIVE는 무엇을 보내든 `.lopus`로 저장한다.** 녹음 내용은 단말이 만드는 것이라 서버가 정한 확장자를 따를 이유가 없다. 파일명 때문에 방송을 끊는 것이 손해가 더 크므로 거절하지 않고 덮어쓴다.

**확장자를 아예 빼도 된다.** 형식을 틀리게 지정한 것이 아니라 지정하지 않은 것이므로 단말이 기본값을 붙인다. FILE에서 확장자를 생략하는 것이 가장 안전하다.

##### `.lopus`는 표준 `.opus`가 아니다

이름이 비슷하지만 다른 형식이다. 표준 `.opus`(RFC 7845)는 Ogg 컨테이너에 담긴 Opus인데, 단말이 만드는 파일은 그렇지 않다.

```text
[28B] magic "OPUSREC1", version, session_id, frame_ms, sample_rate
[2B 길이][Opus 패킷]  ... 반복
```

일반 재생기로는 열리지 않는다. **서버가 이 파일을 열어야 한다면 위 구조를 직접 읽어야 한다.**

#### 서버가 보낼 형식

```text
FILE : notice.mp3         notice-1780000001-W.mp3 처럼 만들 필요 없다
LIVE : 생략하거나 live.lopus
```

- **ASCII 영숫자와 `-` `_`만** 쓴다.
- **epoch와 `-W`는 붙이지 않는다.** 단말이 알아서 붙인다. (이미 붙여 보내도 단말이 중복을 걸러내므로 기존 형식도 그대로 동작한다.)
- 확장자는 FILE `.mp3` 또는 생략, LIVE는 생략. **FILE에 다른 확장자를 넣으면 거절된다.**

## 12. FILE_RESULT

FILE 명령의 최종 결과다. **`FILE_START`와 `FILE_STOP` 모두 이 하나로 답한다.**

```json
{
  "type": "FILE_RESULT",
  "ver": 267,
  "job_id": 201,
  "ok": true,
  "code": "OK",
  "last_offset": 0
}
```

| 필드 | 뜻 |
|---|---|
| `ok` | 성공/실패 |
| `code` | 원인. 전체 목록은 §5.4 |
| `last_offset` | 중단된 위치. 성공이면 0 |

**한 `job_id`에 한 번만 온다.** 진행 중 재시도(§11.1)는 보고하지 않는다.

### 예전 형식에서 바뀐 것 (2026-08-27)

`FILE_END` `FILE_ABORT` `FILE_STOP_RESULT` **세 타입이 이것 하나로 합쳐졌다.**

셋으로 나뉘어 있을 때는 어느 것이 실패인지 타입으로 알 수 없었다. `FILE_ABORT reason=USER_CANCEL`은 정상 종료였고, 성공한 `FILE_STOP`은 `FILE_ABORT`로 왔으며, `FILE_STOP_RESULT status=0`은 아예 발행되지 않았다. **서버가 세 타입을 다 감시하고 내용까지 봐야 판정이 됐다.**

```text
지금: FILE_RESULT 하나를 받고 ok 를 본다. 끝.
```

---

## 13. FILE_STOP

payload:

```json
{
  "type": "FILE_STOP",
  "job_id": 201
}
```

동작:

- 진행 중인 HTTP 다운로드 중지
- 다운로드 완료 후 P4가 자동재생 중인 파일 방송 중지

응답은 `FILE_RESULT` 하나다.

| ok | code |
|---|---|
| `true` | `STOPPED_BY_SERVER` |
| `false` | `NOT_ACTIVE` — 취소할 것이 없거나 `job_id`가 현재 작업과 다르다 |

---

## 15. ONLINE OTA

ONLINE OTA 명령과 상태 사양은 아래 최종 문서를 기준으로 한다.

```text
spec/update/UPDATE_SPEC_FINAL_2026-08-15.md
```

서버는 `OTA_START`만 전송한다. 단말은 다운로드, 검증, 적용, 재부팅까지 자동 진행한다.
pkg 다운로드와 검증이 끝나면 C6가 `OTA_RESULT ok=true`를 QoS 1로 보고한다.
이후 P4가 C6 네트워크 종료를 지시하고, SDIO OTA 경로로 C6/P4 flash 적용을 진행한다.
서버는 `OTA_RESULT ok=true` 이후 MQTT 연결 종료를 정상 OTA 적용 흐름으로 본다. **최종 성공은 재부팅 후 STATUS의 `p4_fw`/`c6_fw`로 확인한다**(§5).

---

## 16. QoS 권장

| 메시지 | 방향 | 권장 QoS |
|---|---|---:|
| OTA_START | 서버 -> 단말 | 1, **retain 금지** |
| LIVE_START | 서버 -> 단말 | 1 |
| LIVE_STOP | 서버 -> 단말 | 1 |
| FILE_START | 서버 -> 단말 | 1 |
| FILE_STOP | 서버 -> 단말 | 1 |
| CONFIG | 서버 -> 단말 | 1, retained |
| LIVE_READY | 단말 -> 서버 | 1 |
| LIVE_RESULT | 단말 -> 서버 | 1 |
| FILE_RESULT | 단말 -> 서버 | 1 |
| STATUS 주기 | 단말 -> 서버 | CONFIG `event_qos` |
| LIVE_STATS | 단말 -> 서버 | CONFIG `event_qos` |
| OTA_PROGRESS | 단말 -> 서버 | CONFIG `event_qos` |
| OTA_RESULT | 단말 -> 서버 | 1 |

QoS 1 명령은 중복 전달 가능성이 있으므로 서버와 단말 모두 `job_id` 기준 중복 처리 정책을 둔다.

**`OTA_START`만 `retain=true`를 금지한다.** retain으로 남겨두면 단말이 재접속할 때마다 과거 OTA가 다시 실행된다. 상세는 `spec/update/UPDATE_SPEC_FINAL_2026-08-15.md`.

---

## 17. 2차 검토 항목

- MQTTS/TLS
- MQTT ACL
- HTTP(S) 파일 서버 인증
- Nginx 또는 reverse proxy 구성
- 펌웨어 서명 검증
- 운영 서버 CONFIG retained 백업/복원
