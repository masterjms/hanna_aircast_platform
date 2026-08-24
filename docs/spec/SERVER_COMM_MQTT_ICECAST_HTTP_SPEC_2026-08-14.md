# SERVER COMM MQTT ICECAST HTTP SPEC 2026-08-14

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

---

## 3. 공통 ID 정책

서버가 단말에 지시하는 작업 번호는 `job_id`로 통일한다.

LIVE, FILE, OTA 모두 외부 MQTT payload에는 `job_id`만 사용한다.

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
| `village_id` | `"00000000"` | 8자리 문자열 | 마을 ID |

`village_id`가 `"00000000"`이면 마을 topic을 구독하지 않는다. CONFIG로 실제 마을 ID를 받으면 해당 마을 topic을 구독한다.

배정 해제(빈 payload retain)는 기존 retain 삭제 규약과 동일하게 처리한다.

### 4.3 부팅 시 대기 정책

MQTT 연결 직후 단말은 `all/config`와 `device/<mac_nocolon>/config`를 함께 구독하고, 둘 중 하나라도 retained CONFIG를 받으면 그 시점에 적용 후 첫 STATUS를 QoS 1로 발행한다.
10초 동안 아무 CONFIG도 오지 않으면 기본값(`village_id="00000000"`, `config_version=1`)으로 첫 STATUS를 QoS 1로 발행하고, 이후 어느 topic으로든 CONFIG가 도착하면 그때 다시 적용/보고한다.

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

상태 우선순위는 `OTA > LIVE > FILE > RF > IDLE`이다.
단말은 상태가 변경되면 주기 STATUS를 기다리지 않고 STATUS를 1회 즉시 발행한다.
상태 변경 즉시 STATUS의 QoS는 CONFIG `event_qos`를 따른다.

`OFFLINE`은 단말이 정상 publish로 직접 보내는 상태가 아니다.
단말은 MQTT 연결 시 LWT(Last Will)를 status topic에 등록한다.
전원 차단, Wi-Fi 단절, 비정상 리셋 등으로 MQTT 연결이 끊기면 broker가 LWT payload를 status topic에 publish한다.
서버는 status topic을 구독하고 있다가 이 LWT 메시지를 수신하면 해당 단말을 OFFLINE으로 표시한다.
정상 재부팅/OTA처럼 의도된 종료 흐름에서는 LWT가 발행되지 않을 수 있으므로, OTA_STATUS와 재접속 후 STATUS를 함께 기준으로 판단한다.

---

## 6. LIVE_START

payload:

```json
{
  "type": "LIVE_START",
  "job_id": 101,
  "codec": "opus",
  "frame_ms": 40,
  "sample_rate": 16000,
  "record_flash": 0,
  "file_name": "live-demo.lopus",
  "ready_timeout_sec": 30
}
```

동작:

1. C6가 MQTT 명령 수신
2. C6가 P4에 live 준비 요청
3. P4가 intro 출력 후 live 준비
4. P4 준비 완료 후 C6가 `LIVE_READY` publish
5. 준비 성공이면 C6가 Icecast stream 연결

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
  "file_name": "notice-1780000001-W.mp3",
  "store_flash": 1,
  "autoplay": 1
}
```

필수 조건:

- `https_url`은 비어 있으면 안 된다.
- `size`는 0이면 안 된다.
- `sha256`은 실제 파일 bytes 기준 64 hex 문자열을 권장한다.
- 1차 테스트에서는 `https_url` 필드명에 `http://` URL 사용 가능하다.

---

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
