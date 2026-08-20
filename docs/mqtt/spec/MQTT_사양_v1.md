# xWIFI iotradio MQTT 사양 v1 (확정본)

> **이 문서는 더 이상 갱신하지 않음.** MQTT + Icecast + HTTP 파일 다운로드를 합친 최신 통합본은
> `작업지시서/spec/xWIFI_통신_사양_최종_260813.md` 참고. 이 파일은 MQTT 단독 상세 이력용으로 보관.

**상태:** 확정 (v1) — PC(Claude) 초안 + Codex 검토(현재 ESP32-C6 코드 기준) 병합
**대상:** PC 서버 ↔ ESP32-C6(P4 중계) 간 MQTT 통신
**범위:** 1차 평문 MQTT(1883), 익명 허용, TLS/인증 없음 — 2차 실서버에서 강화

---

## 1. 접속 정보

```
broker URI : mqtt://<PC_LAN_IP>:1883   (예: mqtt://192.168.0.5:1883)
client-id  : iotradio-<mac_nocolon>    (예: iotradio-58e6c5f2cc74)
village_id : 00000001 (현재 고정값)
```

현재 C6 측 define (참고):

```c
#define APP_RUNTIME_CMD_TRANSPORT_MQTT 1
#define APP_RUNTIME_MQTT_BROKER_URI "mqtt://192.168.0.5:1883"
#define APP_RUNTIME_MQTT_VILLAGE_ID "00000001"
#define APP_RUNTIME_MQTT_STATUS_INTERVAL_MS 30000
#define APP_RUNTIME_MQTT_SUBSCRIBE_ALL_TOPIC 1
#define MQTT_CMD_PAYLOAD_MAX 1024
```

**PC(서버)가 보내는 모든 CMD payload는 1024 bytes 미만이어야 함.** (현재 사용 중인 필드 기준으로는 여유 있음. 향후 필드 추가 시 재확인 필요 — 아래 §9 참고)

---

## 2. Topic 구조 (확정)

| 방향 | Topic | 용도 | QoS | Retain |
|---|---|---:|---:|---:|
| 서버→단말 | `iotradio/device/<mac_nocolon>/cmd` | 개별 단말 명령 (LIVE_START/STOP, FILE_START/STOP) | 1 | false |
| 서버→단말 | `iotradio/village/<village_id>/cmd` | 마을/그룹 단위 명령 | 1 | false |
| 서버→단말 | `iotradio/all/cmd` | 전체 단말 명령 | 1 | false |
| 단말→서버 | `iotradio/device/<mac_nocolon>/result` | 명령 처리 결과 (LIVE_READY, FILE_END, FILE_ABORT) | 1 | false |
| 단말→서버 | `iotradio/device/<mac_nocolon>/status` | 일반 STATUS + LIVE_STATS + LWT (전부 이 토픽 하나로 옴, `type` 필드로 구분) | STATUS 연결직후 1/주기 0, LIVE_STATS 0 | false |

> **LIVE_STATS는 별도 토픽이 아니라 `status` 토픽으로 옵니다.** 서버는 수신 시 `type` 필드(`"STATUS"` vs `"LIVE_STATS"`)로 구분해야 합니다. (`monitor.bat`/`control.bat`은 이미 `.../status` 토픽을 구독 중이라 추가 수정 불필요.)

> CONFIG 토픽(`iotradio/all/config`)은 **2차 예정**입니다 — §9 참고.

---

## 3. CMD 메시지 (서버 → 단말)

### 3.1 LIVE_START

```json
{
  "type": "LIVE_START",
  "session_id": 13,
  "codec": "opus",
  "frame_ms": 40,
  "sample_rate": 16000,
  "record_flash": 0,
  "file_name": "live-demo.lopus",
  "ready_timeout_sec": 30
}
```

| 필드 | 타입 | 필수 | 비고 |
|---|---:|---:|---|
| `type` | string | 예 | `"LIVE_START"` |
| `session_id` | number | 예 | P4 라이브 세션 ID |
| `codec` | string | 권장 | 현재 `"opus"` 고정 |
| `frame_ms` | number | 권장 | 0/과도한 값이면 40으로 보정. **FFmpeg `-frame_duration`과 반드시 일치** |
| `sample_rate` | number | 권장 | 0이면 16000으로 보정 |
| `record_flash` | number/bool | 권장 | 1이면 P4 녹음 저장 |
| `file_name` | string | 권장 | 녹음 저장 파일명 |
| `ready_timeout_sec` | number | 권장 | 1~60 범위 밖이면 30으로 보정 |

**동작 순서:** C6 수신 → P4에 `LIVE_CTRL_START` 전달 → P4 인트로음+준비 완료 → C6가 `LIVE_READY` publish → `status=0,reason=0`이면 C6가 Icecast 스트림 수신 시작.

**BUSY 조건:** 파일 다운로드/방송 중이거나 기존 LIVE 진행 중이면 거절, `LIVE_READY status=3 reason=8` 응답.

### 3.2 LIVE_STOP

```json
{ "type": "LIVE_STOP", "session_id": 13 }
```

- 현재 `session_id` 일치 검사는 강하지 않음 — **서버는 반드시 현재 송출 중인 session_id로 보낼 것.**

### 3.3 FILE_START

```json
{
  "type": "FILE_START",
  "cmd_id": 38,
  "file_id": 16,
  "size": 131900,
  "resume_offset": 0,
  "sha256": "97dff9352cc772b4cc5da29244d6fb44275f80f1ff778cb06509209a9496533d",
  "https_url": "http://192.168.0.5:9002/file-1782956366.mp3",
  "file_name": "file-1782956366-W.mp3",
  "store_flash": 1,
  "autoplay": 1
}
```

| 필드 | 타입 | 필수 | 비고 |
|---|---:|---:|---|
| `type` | string | 예 | `"FILE_START"` |
| `cmd_id` | number | 예 | 결과 응답에 그대로 echo |
| `file_id` | number | 예 | 결과 응답에 그대로 echo |
| `size` | number | 예 | 0이면 거절 |
| `resume_offset` | number | 아니오 | 기본 0 |
| `sha256` | string | 권장 | 64 hex, P4 검증용 |
| `https_url` | string | 예 | 필드명은 `https_url`이지만 1차 LAN 테스트에선 `http://...:9002/...`도 사용 |
| `file_name` | string | 권장 | P4 저장/표시 파일명 |
| `store_flash` | bool/number | 권장 | P4 저장 여부 |
| `autoplay` | bool/number | 권장 | 수신 후 자동 방송 여부 |

**PC 측 필요 작업(테스트 구현 예정):** Icecast(8000)와 **별도의 파일 HTTP 서버**(예: 포트 9002)가 필요. → §10 참고.

**BUSY 조건:** 파일 처리 중이면 `FILE_ABORT reason=BUSY`, 라이브 방송 중이면 `FILE_ABORT reason=PREEMPTED_BY_LIVE`.

### 3.4 FILE_STOP

```json
{ "type": "FILE_STOP" }
```

진행 중인 파일 다운로드/처리 중단 요청 → 서버에 `FILE_ABORT reason=USER_CANCEL` 응답.

---

## 4. RESULT 메시지 (단말 → 서버)

Topic: `iotradio/device/<mac_nocolon>/result`, QoS 1

### 4.1 LIVE_READY

```json
{
  "type": "LIVE_READY",
  "ver": 267,
  "session_id": 13,
  "device": "58:e6:c5:f2:cc:74",
  "status": 0,
  "reason": 0
}
```

| status | 의미 |
|---:|---|
| 0 | READY |
| 1 | TIMEOUT |
| 2 | ABORT |
| 3 | FAIL/BUSY |

| reason | 의미 |
|---:|---|
| 0x00 | 정상 |
| 0x01 | 일반 실패/중단 |
| 0x03 | timeout |
| 0x08 | busy |

**서버 처리:** `status=0,reason=0`일 때만 Icecast 송출 준비 완료로 판단. `status=3,reason=8`은 BUSY → 자동 재시도 금지, 사용자에게 표시 후 수동 재시도.

### 4.2 FILE_END

성공:
```json
{ "type": "FILE_END", "ver": 267, "cmd_id": 38, "file_id": 16, "verify_ok": true }
```

실패:
```json
{ "type": "FILE_END", "ver": 267, "cmd_id": 38, "file_id": 16, "verify_ok": false, "fail_reason": "SHA256_FAIL" }
```

`fail_reason`: `SHA256_FAIL` / `STORAGE_FAIL` / `BAD_FIELD` / `NO_PSRAM` / `DL_FAIL`

### 4.3 FILE_ABORT

```json
{ "type": "FILE_ABORT", "ver": 267, "cmd_id": 38, "file_id": 16, "last_offset": 0, "reason": "BUSY" }
```

`reason`: `PREEMPTED_BY_LIVE` / `BAD_FIELD` / `USER_CANCEL` / `NO_PSRAM` / `BUSY` / `NET_ERROR`

**서버 처리:** `BUSY` 수신 시 서버 내부 파일 전송 상태를 반드시 clear. 자동 무한 재시도 금지 — 사용자가 다시 시도 버튼을 눌러야 처음부터 재전송.

---

## 5. STATUS 메시지 (단말 → 서버, 겸 LWT)

Topic: `iotradio/device/<mac_nocolon>/status`

### 5.1 일반 STATUS

연결 직후 1회 + 이후 **30초 주기**(`APP_RUNTIME_MQTT_STATUS_INTERVAL_MS = 30000`, 현재 고정값).

```json
{
  "type": "STATUS",
  "device": "58:e6:c5:f2:cc:74",
  "village_id": "00000001",
  "wifi": 1,
  "mqtt": 1,
  "ip": "192.168.0.21",
  "rssi": -39,
  "state": "IDLE",
  "busy": 0,
  "reason": 0
}
```

`state` 후보: `LIVE_READY_WAIT` / `LIVE` / `FILE` / `OFFLINE` / `IDLE`

### 5.2 LWT

```json
{
  "type": "STATUS",
  "device": "58:e6:c5:f2:cc:74",
  "village_id": "00000001",
  "wifi": 0,
  "mqtt": 0,
  "state": "OFFLINE"
}
```

```
topic  : iotradio/device/<mac_nocolon>/status  (STATUS와 동일 토픽 재사용)
QoS    : 1
retain : false  (현재 구현. 서버 대시보드에서 "마지막 상태 즉시 조회"가 필요해지면 retain=true로 변경 검토 — 2차)
```

---

## 6. LIVE_STATS 메시지

Topic: `iotradio/device/<mac_nocolon>/status` (STATUS와 동일 토픽, `type`으로 구분)

방송 중(P4 live_audio_task)에서 **약 10초 주기**로 전송, C6가 수신 즉시 그대로 publish.

```json
{
  "type": "LIVE_STATS",
  "ver": 267,
  "session_id": 13,
  "p4_buffer_ms": 2120,
  "underrun_count": 1,
  "decode_error_count": 0,
  "rx_seq_last": 1162,
  "rec_overflow": 0
}
```

| 필드 | 의미 |
|---|---|
| `p4_buffer_ms` | P4 지터 버퍼에 남은 오디오 시간(ms) |
| `underrun_count` | 버퍼 고갈로 재버퍼링/은닉 발생 누적 횟수 |
| `decode_error_count` | Opus 디코드 오류 횟수 (현재 항상 0 전송) |
| `rx_seq_last` | P4 기준 마지막 처리 sequence |
| `rec_overflow` | 녹음 overflow 여부 |

**서버 모니터링 기준:** `p4_buffer_ms` 1000~2500ms면 안정권. `underrun_count` 계속 증가 시 끊김 의심. `rx_seq_last`는 40ms 기준 10초당 약 250 증가해야 정상.

---

## 7. QoS / Retain 정리

| 메시지 | Topic | QoS | Retain |
|---|---|---:|---:|
| LIVE_START / LIVE_STOP | cmd | 1 | false |
| FILE_START / FILE_STOP | cmd | 1 | false |
| LIVE_READY / FILE_END / FILE_ABORT | result | 1 | false |
| STATUS (연결 직후) | status | 1 | false |
| STATUS (주기) | status | 0 | false |
| LIVE_STATS | status | 0 | false |
| LWT | status | 1 | false |

**주의:** CMD 토픽에는 retain을 쓰지 말 것 — 단말 재접속 시 과거 방송 명령이 재실행될 위험.

---

## 8. 서버 구현 시 주의사항

1. CMD payload는 1024 bytes 미만 유지 (현재 여유 있음, 필드 추가 시 재확인).
2. `LIVE_READY status=0,reason=0` 확인 후에만 실제 Icecast 송출 시작.
3. `LIVE_READY status=3,reason=8`(BUSY)은 자동 재시도 금지, 사용자 표시.
4. `FILE_ABORT reason=BUSY` 수신 시 서버 내부 파일 전송 상태 clear 필수.
5. `LIVE_STATS`는 `status` 토픽으로 오므로 `type` 필드로 STATUS와 구분해서 처리.
6. 파일 다운로드는 Icecast(8000)와 분리된 별도 HTTP 서버 포트 사용 (예: 9002).

---

## 9. 2차 예정 (현재 미구현)

### 9.1 CONFIG (`iotradio/all/config`, retained)

현재 C6는 이 토픽을 구독하지 않음. 2차 구현 시 아래 정책 권장:

```json
{
  "config_version": 1,
  "status_interval_sec": 30,
  "live_stats_interval_sec": 10,
  "event_qos": 1
}
```

| 필드 | 기본값 | clamp | 비고 |
|---|---:|---:|---|
| `config_version` | 1 | - | 변경 시 증가, 단말이 적용값 STATUS에 echo 권장 |
| `status_interval_sec` | 30 | 10~3600 | 현재 코드 기본값(30초)과 동일하게 유지 |
| `live_stats_interval_sec` | **10** | 1~60 | **확정.** P4 기본 전송 주기는 10초로 반영. 양산 규모(수백 대) 기준 브로커 부하를 고려해 기본값은 10초 이상으로 보수적으로 잡음. 소규모 데모/디버깅 시엔 CONFIG로 좁혀서 사용 |
| `event_qos` | 1 | 0~1 | |

- 알 수 없는 필드는 무시(하위 호환), clamp 범위 벗어나면 해당 필드만 무시하고 기본값 유지.
- CONFIG 미수신 시(최초 부팅 등) 위 기본값을 펌웨어 하드코딩값으로 사용.

### 9.2 기타 2차 검토 항목

- `live_stats` 별도 토픽 분리 여부
- STATUS/LWT retain 정책 (서버 대시보드 요구사항에 따라)
- MQTTS/TLS, broker 인증, client 인증
- village별/device별 CONFIG 오버라이드 (1차에서는 전체 공통 하나로 결정, 필요성 낮음)

---

## 10. 파일 다운로드 서버 (PC측 TODO)

현재 ESP32-C6/P4 단말 쪽 FILE_START/FILE_STOP 수신 및 처리 흐름은 구현되어 있다.  
따라서 1차 검증 기준으로는 **PC쪽 HTTP 파일 서버와 MQTT 명령 송신/결과 처리만 작성하면 된다.**

### 10.1 단말 구현 완료 범위

단말은 현재 아래 흐름을 처리할 수 있다.

```text
MQTT FILE_START 수신
 -> C6가 https_url 필드의 URL로 HTTP/HTTPS 다운로드
 -> C6가 받은 데이터를 SDIO로 P4에 전달
 -> P4가 size/sha256 확인
 -> store_flash/autoplay 조건에 따라 저장/자동재생
 -> C6가 FILE_END 또는 FILE_ABORT를 MQTT result로 publish
```

`https_url`이라는 필드명은 유지한다.  
단, 1차 LAN 테스트에서는 값으로 `http://<PC_LAN_IP>:9002/<file>` 형식을 사용할 수 있다.

예시:

```json
{
  "type": "FILE_START",
  "cmd_id": 1,
  "file_id": 1,
  "size": 131900,
  "resume_offset": 0,
  "sha256": "파일 sha256 64자리",
  "https_url": "http://192.168.0.5:9002/file-1782956366.mp3",
  "file_name": "file-1782956366-W.mp3",
  "store_flash": 1,
  "autoplay": 1
}
```

### 10.2 PC 서버 구현 필요 범위

PC 쪽은 아래 항목을 구현한다.

1. MQTT로 `FILE_START` 발행
2. `https_url`에 접근 가능한 HTTP 파일 서버 실행
3. `size`, `sha256` 자동 계산
4. `cmd_id`, `file_id` 생성 및 증가 관리
5. `FILE_END verify_ok=true/false` 결과 표시
6. `FILE_ABORT reason=BUSY` 수신 시 서버 내부 파일 전송 상태 clear
7. 사용자가 중지 버튼을 누르면 MQTT `FILE_STOP` 발행

### 10.3 포트 정책

Icecast 실시간 방송과 파일 다운로드는 포트를 분리한다.

```text
Icecast live stream : 8000
HTTP file server    : 9002 예시
```

파일 다운로드 서버는 Icecast 서버가 아니다.  
MP3 등 파일을 일반 HTTP response로 내려주는 별도 HTTP 서버로 구성한다.

### 10.4 BUSY 처리 정책

단말이 파일 또는 라이브 방송 처리 중이면 `FILE_ABORT`로 BUSY를 응답할 수 있다.

```json
{
  "type": "FILE_ABORT",
  "ver": 267,
  "cmd_id": 1,
  "file_id": 1,
  "last_offset": 0,
  "reason": "BUSY"
}
```

서버는 BUSY 수신 시 자동 무한 재시도하지 않는다.  
서버 내부 파일 전송 상태를 clear하고, 사용자 화면에는 `BUSY`만 표시한다.  
사용자가 다시 파일 방송 버튼을 누르면 처음부터 새 `cmd_id`, `file_id`로 재전송한다.

---

## 11. 진행 방향 메모

- LIVE 쪽 추가 안정화(지터버퍼 고수위 보정 등)는 **MQTT + 파일다운로드 기본 구성 완료 후** 판단 예정. 현재는 검증/테스트 단계이며, 실제 서버 연동 시점에 지속적으로 안정화할 계획.

---

## 변경 이력

| 버전 | 날짜 | 내용 |
|---|---|---|
| v1-draft | 2026-08-13 | Claude 최초 초안 (LIVE_START/STOP 정리 + CONFIG/LWT/LIVE_STATS 신규 제안) |
| v1-review | 2026-08-13 | Codex 검토 (`MQTT_사양_v1_codex.md`) — 현재 ESP32-C6 실제 구현 기준으로 대량 수정 제안 |
| **v1 확정** | 2026-08-13 | 두 문서 병합. 실제 구현 기준 필드 전면 반영, FILE_START/STOP 추가, CONFIG는 2차로 분리(`live_stats_interval_sec` 기본 10초 확정), 파일 다운로드 서버 TODO 추가 |
