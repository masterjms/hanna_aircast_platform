# xWIFI 마을방송 서버 · 단말 연동 사양서

| 항목 | 내용 |
|---|---|
| 문서 버전 | 1.0 |
| 기준일 | 2026-09-14 |
| 대상 | ESP32-P4 + ESP32-C6 방송 단말과 연동하는 개발자 |
| 범위 | 1~11장: 통신 프로토콜(상세) · 12장: 서버 기능 개요(요약) |

표기: `<mac>` 은 콜론 없는 소문자 MAC 12자리, `<서버 도메인>` 은 운영 서버 호스트명이다.

---

## 1. 시스템 구성

```text
 관리자 브라우저 ──HTTPS/WSS──▶ 운영 서버 ─┬─ MQTT 브로커 ──MQTTS 8883──▶ 단말
                                             ├─ 스트림 서버 ──HTTPS /live──▶ 단말
                                             └─ 파일 서버  ──HTTPS /dl────▶ 단말
```

단말은 세 채널을 쓴다.

| 채널 | 프로토콜 | 용도 |
|---|---|---|
| 제어·상태 | MQTTS (TCP 8883) | 명령, 설정(CONFIG), 상태(STATUS), 결과 |
| 실시간 음성 | HTTPS GET `/live/<job_id>` | Ogg/Opus 스트림 수신 |
| 파일 | HTTPS GET `/dl/<token>` | MP3 다운로드 (Range 이어받기) |

MQTT 로는 제어 메시지만 보낸다. 음성·파일 본문은 HTTPS 로만 전달한다.

---

## 2. 공통 규칙

### 2.1 단말 식별자

- MAC 정규형: 콜론 없는 소문자 12자리. 예) `58:E6:C5:F2:CC:74` → `58e6c5f2cc74`
- MQTT username, client ID, 토픽 경로에 모두 정규형을 쓴다.
- 단말이 payload 의 `device` 에 콜론 표기를 넣어도 된다. 서버가 정규화한다.

### 2.2 마을 ID (`village_id`)

단말이 구독할 마을 토픽의 이름이다. 숫자 문자열이며 단말은 의미를 해석하지 않고 토픽에 그대로 쓴다.

| 종류 | 형식 | 예 |
|---|---|---|
| 일반 마을 | 법정동코드 10자리 + 방송 그룹 연번 2자리 | `128103302101` |
| 주소 미등록 마을 | 8자리 숫자 | `00000003` |
| 미배정 | 전부 `0` (12자리) | `000000000000` |

- 단말은 숫자 8~16자리를 받는다.
- 전부 `0` 이면 마을 토픽을 구독하지 않는다.
- 한 번 발급된 값은 그 마을이 있는 동안 바뀌지 않는다.

### 2.3 메시지 형식

- UTF-8 JSON, 공백 없이 직렬화한다.
- **서버→단말 명령 payload 는 1,024바이트 이하.** 서버가 발행 전에 검사한다.
- 단말은 모르는 필드를 무시한다.

### 2.4 작업 ID (`job_id`)

- LIVE · FILE · OTA 작업의 유일한 식별자. 서버가 발번하며 0 이 아니다.
- 한 작업의 명령·결과·텔레메트리는 모두 같은 `job_id` 를 쓴다.
- QoS 1 은 중복 전달될 수 있다. 서버와 단말 모두 `job_id` 기준으로 멱등 처리한다.

### 2.5 결과 메시지 공통 필드

```json
{"type":"FILE_RESULT","ver":267,"job_id":201,"device":"58:e6:c5:f2:cc:74","ok":true,"code":"OK"}
```

| 필드 | 설명 |
|---|---|
| `type` | 메시지 종류 |
| `ver` | 단말이 채우는 버전 값. 서버는 판정에 쓰지 않는다 |
| `job_id` | 작업 ID |
| `device` | 단말 MAC |
| `ok` | **성공·실패 판정은 이 값만 본다** |
| `code` | 원인 코드 (표시·로그용) |

---

## 3. MQTT 연결

### 3.1 접속값

| 항목 | 값 |
|---|---|
| host | 생산 등록 시 주입한 `@SERVER` 값 |
| port | 8883 |
| TLS | 1.2 이상, 서버 인증서 검증 |
| username / client ID | MAC 정규형 |
| password | 서버가 단말마다 발급한 8자 |
| keepalive | 20초 |
| clean session | true |

비밀번호 문자 집합: 영문 대·소문자, 숫자, `# $ % ^ & * - _ + = ? . ~`. 서버는 발급할 때 `=` 를 쓰지 않는다. `@`, `!` 는 쓰지 않는다.

### 3.2 연결 순서

1. Wi-Fi 연결
2. SNTP 시간 동기화 (인증서 검증에 필요)
3. MQTTS 연결, LWT 등록 (6.2절)
4. cmd · config 토픽 구독
5. retained CONFIG 수신 (최대 10초 대기)
6. 첫 STATUS 를 QoS 1 로 발행
7. 이후 주기 STATUS, 상태가 바뀌면 즉시 STATUS

### 3.3 재접속

- 1초에서 시작해 실패할 때마다 간격을 늘리고, 최대 60초 간격으로 계속 시도한다.
- **각 대기 시간에 무작위 지터를 섞는다.** 브로커 재시작 시 전 단말이 같은 순간 재접속하는 것을 막기 위함이다.
- MQTT 가 끊기면 진행 중 LIVE · FILE 은 즉시 종료한다. 재연결 후 자동 재개하지 않는다.

### 3.4 접근 권한 (ACL)

단말 계정은 아래 토픽만 쓸 수 있다.

| 권한 | 토픽 |
|---|---|
| 구독 | `iotradio/all/cmd`, `iotradio/all/config` |
| 구독 | `iotradio/device/<자기 mac>/cmd`, `iotradio/device/<자기 mac>/config` |
| 구독 | `iotradio/village/<자기 village_id>/cmd` (배정된 경우) |
| 발행 | `iotradio/device/<자기 mac>/status`, `iotradio/device/<자기 mac>/result` |

단말은 cmd 토픽에 발행할 수 없다.

---

## 4. 토픽

| 방향 | 토픽 | 내용 | QoS | retain |
|---|---|---|---:|:---:|
| 서버→단말 | `iotradio/device/<mac>/cmd` | 단말 1대 명령 | 1 | X |
| 서버→단말 | `iotradio/village/<village_id>/cmd` | 마을 명령 | 1 | X |
| 서버→단말 | `iotradio/all/cmd` | 전체 명령 | 1 | X |
| 서버→단말 | `iotradio/all/config` | 공통 설정 | 1 | O |
| 서버→단말 | `iotradio/device/<mac>/config` | 단말별 마을 배정 | 1 | O |
| 단말→서버 | `iotradio/device/<mac>/status` | STATUS · LWT · LIVE_STATS | 6장 참고 | X |
| 단말→서버 | `iotradio/device/<mac>/result` | LIVE · FILE · OTA 결과와 진행 | 메시지별 | X |

- 명령은 절대 retain 하지 않는다. retain 된 명령은 재접속 시 지난 방송을 다시 실행시킨다.
- 서버의 「구역」 대상 방송은 소속 단말마다 `device/<mac>/cmd` 로 펼쳐서 보낸다. 단말은 구역을 알 필요가 없다.

---

## 5. CONFIG

### 5.1 공통 설정 — `iotradio/all/config`

```json
{"config_version":42,"status_interval_sec":30,"live_stats_interval_sec":10,"event_qos":0}
```

| 필드 | 기본 | 범위 | 설명 |
|---|---:|---|---|
| `config_version` | 1 | 증가만 | 설정 버전 |
| `status_interval_sec` | 30 | 10~3600 | 주기 STATUS 간격(초) |
| `live_stats_interval_sec` | 10 | 1~60 | 방송 중 LIVE_STATS 간격(초) |
| `event_qos` | 0 | 0~1 | 주기 STATUS · LIVE_STATS · OTA_PROGRESS 의 QoS |

마을 배정(`village_id`)은 이 토픽에 넣지 않는다.

### 5.2 단말별 설정 — `iotradio/device/<mac>/config`

```json
{"config_version":42,"village_id":"128103302101"}
```

- 미배정은 빈 메시지가 아니라 `"village_id":"000000000000"` 으로 명시한다.

### 5.3 적용 규칙

- 두 토픽은 항상 **같은 `config_version`** 으로 함께 발행된다.
- 단말은 `config_version` 이 올라간 경우에만 적용한다.
- 서버는 기동 직후와 이후 1시간마다 두 토픽을 다시 발행한다(값이 같으면 단말은 무시).
- STATUS 의 `village_id` · `config_version` 이 서버 값과 다르면 서버가 그 단말의 CONFIG 를 다시 보낸다(단말당 60초에 한 번 이내).

---

## 6. 상태 보고

### 6.1 STATUS — `status` 토픽

```json
{"type":"STATUS","device":"58:e6:c5:f2:cc:74","village_id":"128103302101","ip":"192.168.0.21",
 "rssi":-54,"state":"IDLE","busy":0,"live":"OFF","config_version":42,"p4_fw":"V.260905-1","c6_fw":"V.260905-1"}
```

| 필드 | 설명 |
|---|---|
| `village_id` | 단말이 현재 적용 중인 마을 ID |
| `ip`, `rssi` | 현재 Wi-Fi 정보 |
| `state` | `IDLE` `LIVE` `FILE` `RF` `OTA` `OFFLINE` |
| `busy` | 0 = 새 작업 수락 가능, 1 = 작업 중 |
| `live` | `OFF` 방송 아님 · `PLAYING` 수신 중 · `RECONNECTING` 스트림 재접속 중(방송 유지, 무음) |
| `config_version` | 현재 적용 중인 설정 버전 |
| `p4_fw`, `c6_fw` | 실행 중인 펌웨어 버전 |

- 상태가 겹칠 때 `state` 표시 우선순위: `OTA > LIVE > FILE > RF > IDLE` (표시 순서일 뿐 명령 선점 순서가 아니다).
- 서버는 **300초 동안 메시지가 없으면 오프라인**으로 판단한다.

### 6.2 LWT

연결 시 QoS 1 Last Will 로 등록한다. 비정상 단절 시 브로커가 대신 발행한다.

```json
{"type":"STATUS","device":"58:e6:c5:f2:cc:74","village_id":"128103302101","state":"OFFLINE"}
```

### 6.3 LIVE_STATS — `status` 토픽, 방송 중에만

```json
{"type":"LIVE_STATS","ver":267,"job_id":101,"p4_buffer_ms":2080,"underrun_count":1,
 "decode_error_count":0,"rx_seq_last":1062,"rec_overflow":0}
```

간격은 `live_stats_interval_sec`, QoS 는 `event_qos` 를 따른다. 서버는 방송·단말별 최신값만 보관하며, 종료된 작업의 늦은 LIVE_STATS 는 버린다.

---

## 7. 실시간 방송 (LIVE)

### 7.1 오디오 규격

| 항목 | 값 |
|---|---|
| 컨테이너 / 코덱 | Ogg / Opus |
| 채널 / 표본율 | mono / 16,000 Hz |
| 비트레이트 | 16 또는 24 kbps (서버 설정) |
| 프레임 | 40 ms, Ogg 페이지당 1프레임 |

서버는 받은 Ogg 스트림을 재인코딩하지 않고 그대로 중계한다.

### 7.2 순서

```text
서버                                         단말
 │ 1. 스트림 mount /live/<job_id> 준비        │
 │ 2. LIVE_START ─────────────────────────────▶│
 │◀───────────────────────────── 3. LIVE_READY │ (오디오 출력 준비 완료)
 │                          4. GET stream_url ─▶│ 재생, STATUS live=PLAYING
 │   … 방송 …                  LIVE_STATS 주기 │
 │ 5. LIVE_STOP ──────────────────────────────▶│
 │◀──────────────────────────── 6. LIVE_RESULT │
 │ 7. mount 종료                               │
```

### 7.3 LIVE_START

```json
{"type":"LIVE_START","job_id":101,"stream_url":"https://<서버 도메인>/live/101","codec":"opus",
 "frame_ms":40,"sample_rate":16000,"record_flash":1,"ready_timeout_sec":30}
```

| 필드 | 필수 | 설명 |
|---|:---:|---|
| `job_id` | O | 작업 ID |
| `stream_url` | O | 단말이 **그대로 GET 하는 완성 URL.** HTTPS, 512바이트 이하. 경로를 해석·재조립하지 않는다 |
| `codec` | | `opus` |
| `frame_ms` | | `40` |
| `sample_rate` | | `16000` |
| `record_flash` | | 1 = 단말에 녹음 저장, 0 = 저장 안 함 (생략 시 0) |
| `ready_timeout_sec` | | 출력 준비 제한(초). 기본 30, 1~60. 외부 앰프 안정화 시간보다 길어야 한다 |

마을 여러 곳이 대상이면 같은 payload(같은 `job_id`·`stream_url`)를 각 마을 토픽에 발행한다.

### 7.4 LIVE_READY — `result` 토픽, QoS 1

| `ok` | `code` | 의미 |
|:---:|---|---|
| true | `OK` | 오디오 출력 준비 완료 |
| false | `BUSY` | 다른 작업 진행 중 |
| false | `BAD_FIELD` | URL scheme 또는 `job_id` 오류 |
| false | `TIMEOUT` | 준비 제한 초과 |

- READY 는 스트림 접속 **전**에 온다. 실제 수신은 `STATUS live=PLAYING` 으로 확인한다.
- 같은 LIVE_START 가 QoS 1 로 재전달돼 BUSY 가 오더라도, 같은 작업에서 이미 `ok=true` 를 받았으면 서버는 그 BUSY 를 실패로 보지 않는다.

### 7.5 스트림 단절

스트림만 끊기고 MQTT 가 살아 있으면:

1. `STATUS live=RECONNECTING` 즉시 발행 (방송 유지, 무음)
2. 같은 `stream_url` 로 0.5초 · 2초 · 4초 간격 최대 3회 재접속
3. 성공 → `STATUS live=PLAYING` / 실패 → `LIVE_RESULT ok=false`, `STATUS state=IDLE live=OFF`

서버는 방송이 끝날 때까지 mount 를 유지한다.

### 7.6 LIVE_STOP / LIVE_RESULT

```json
{"type":"LIVE_STOP","job_id":101}
```

**종료 순서:** 서버는 LIVE_STOP 발행 → mount 를 유지한 채 대상 전체의 LIVE_RESULT 를 기다림(최대 10초) → mount 종료. mount 를 먼저 닫으면 단말이 네트워크 장애로 보고 재접속을 시작한다.

| `ok` | `code` | 의미 |
|:---:|---|---|
| true | `STOPPED_BY_SERVER` | 정상 중지 |
| false | `ABORTED` | 단말 내부 중단 |
| false | `TIMEOUT` | 15초간 오디오 프레임 없음 |
| false | `NOT_ACTIVE` | 해당 `job_id` 작업 없음 |

---

## 8. 파일 방송 (FILE)

### 8.1 파일 규격

| 항목 | 값 |
|---|---|
| 형식 | MP3, mono, 16,000 Hz, 16 또는 24 kbps (서버 설정) |
| 최대 크기 | 2,621,440 바이트 (2.5 MiB) |
| 최대 길이 | 600초 |
| 무결성 | 전체 바이트의 SHA-256 |
| 파일명 | ASCII 영문·숫자·`-`·`_`, 확장자 `.mp3`, 64바이트 이하 |

### 8.2 순서

```text
서버                                           단말
 │ 1. FILE_START ────────────────────────────────▶│
 │                         2. GET https_url (Range)│ 다운로드
 │                                                 │ SHA-256 검증
 │◀──────────────────────────────── 3. FILE_RESULT │ ok=true → autoplay 면 재생 시작
 │   (재생 시간 + 5초 후 서버가 방송 종료 처리)       │
```

### 8.3 FILE_START

```json
{"type":"FILE_START","job_id":201,"size":73990,"resume_offset":0,
 "sha256":"071e5e40ad46b332a4f6d013625dcccd522ecc5f72336a088789bd3d4ad3d556",
 "https_url":"https://<서버 도메인>/dl/<token>","file_name":"notice.mp3","store_flash":0,"autoplay":1}
```

| 필드 | 설명 |
|---|---|
| `job_id` | 작업 ID |
| `size` | 파일 바이트 수 (1 ~ 2,621,440) |
| `resume_offset` | 서버는 항상 0 |
| `sha256` | 소문자 hex 64자 |
| `https_url` | 다운로드 URL (8.4절) |
| `file_name` | 단말 저장용 이름 |
| `store_flash` | 1 = 단말에 저장, 0 = 재생만 |
| `autoplay` | 1 = 검증 후 즉시 재생, 0 = 받기만 |

10분을 넘길 수 있는 방송은 LIVE · FILE 모두 저장 옵션을 0 으로 보낸다.

### 8.4 다운로드 HTTP

| 항목 | 규칙 |
|---|---|
| 요청 | `GET https_url`, 인증 헤더 없음 (토큰이 URL 에 포함) |
| 유효 기간 | 발급 후 600초. 기간 안에서는 여러 번 요청 가능 |
| 이어받기 | `Range: bytes=<offset>-` → `206 Partial Content` |
| 토큰 없음·만료 | `404` |

단말 재시도: 끊긴 위치부터 Range 로 0.5초 · 2초 · 4초 간격 최대 3회. 4xx 는 즉시 실패, 5xx·연결 실패는 재시도. `200` 전체 응답이 오면 처음부터 받는다.

### 8.5 FILE_RESULT — `result` 토픽, QoS 1

```json
{"type":"FILE_RESULT","ver":267,"job_id":201,"device":"58:e6:c5:f2:cc:74","ok":true,"code":"OK","last_offset":0}
```

| `ok` | `code` | 의미 |
|:---:|---|---|
| true | `OK` | 다운로드·검증 완료 (autoplay 면 재생 시작) |
| true | `STOPPED_BY_SERVER` | 서버 중지로 종료 |
| false | `BAD_FIELD` | URL · 확장자 · 크기 · ID 오류 |
| false | `BUSY` | 다른 작업 진행 중 |
| false | `PREEMPTED_BY_LIVE` | LIVE 진행 중 |
| false | `NET_ERROR` | 다운로드 최종 실패 |
| false | `VERIFY_FAIL` | SHA-256 불일치 |
| false | `STORAGE_FAIL` | 저장 실패 (재생은 가능) |
| false | `NO_MEMORY` | 수신 버퍼 부족 |
| false | `CREDIT_TIMEOUT` | 내부 데이터 전달 지연 |
| false | `NOT_ACTIVE` | 중지할 작업 없음 |

`last_offset` 은 작업이 중단된 바이트 위치다.

서버는 시작·중지 결과를 기본 30초(설정 10~60초)까지 기다린다.

### 8.6 FILE_STOP

```json
{"type":"FILE_STOP","job_id":201}
```

다운로드 중이거나 재생 중인 작업을 중지한다. 단말은 `FILE_RESULT` 로 응답한다.

---

## 9. OTA

단말 계약만 확정되어 있으며, 서버의 배포 기능은 준비 중이다.

- 명령은 `OTA_START` 하나. QoS 1, **retain 금지**.
- 필드: `job_id`, `url` (HTTPS 패키지), `size`, `sha256` 등. 서명 검증 정보는 배포 기능 확정 시 추가한다.
- 패키지명 `IOT_RADIO.pkg`. 단말이 패키지 내용을 보고 P4 · C6 대상을 스스로 판단한다.
- 단말은 다운로드 → 검증 → 적용 → 재부팅까지 승인 없이 진행한다.

**OTA_PROGRESS** (`result` 토픽, QoS 는 `event_qos`, 최종 아님)

```json
{"type":"OTA_PROGRESS","job_id":301,"state":"DOWNLOADING","percent":50,"received":3600000,"total_size":7297148}
```

`state`: `ACCEPTED` `PREPARE` `DOWNLOADING` `VERIFYING`

**OTA_RESULT** (`result` 토픽, QoS 1, 작업당 1회)

| `ok` | `code` | 의미 |
|:---:|---|---|
| true | `OK` | 적용 완료, 곧 재부팅 |
| false | `BAD_FIELD` | URL · ID · size 오류 |
| false | `BUSY` | 다른 작업 진행 중 |
| false | `NO_MEMORY` | 메모리 확보 실패 |
| false | `DL_FAIL` | 다운로드 실패 |
| false | `VERIFY_FAIL` | 해시·서명 검증 실패 |
| false | `APPLY_FAIL` | 플래시 적용 실패 |

최종 성공은 재부팅 후 STATUS 의 `p4_fw` · `c6_fw` 가 목표 버전인지로 확인한다.

---

## 10. 작업 충돌

단말은 **먼저 시작한 작업을 유지**한다. 새 명령이 기존 작업을 끊지 않는다.

| 진행 중 \ 새 명령 | LIVE_START | FILE_START | OTA_START |
|---|---|---|---|
| 없음 | 수락 | 수락 | 수락 |
| LIVE | BUSY | PREEMPTED_BY_LIVE | BUSY |
| FILE | BUSY | BUSY | BUSY |
| OTA | BUSY | BUSY | BUSY |

- 서버도 대상 단말이 겹치는 방송이 진행 중이면 새 방송을 시작하지 않는다.
- 작업을 바꾸려면 서버가 기존 작업을 중지하고, 결과 또는 `STATUS state=IDLE busy=0` 을 확인한 뒤 새 명령을 보낸다.
- RF(자체 무선 수신) 중 LIVE · FILE 명령의 응답 규칙은 별도 협의한다.

---

## 11. 생산 등록

### 11.1 QR 라벨

```text
MAC|P4_MODEL|P4_VERSION|C6_MODEL|C6_VERSION
```

### 11.2 시리얼 주입

| 항목 | 값 |
|---|---|
| 연결 | USB 시리얼 115200 bps |
| 진입 | 단말 KEY1 + KEY4 (생산 모드) |
| 줄 끝 | LF (`0x0A`). **`@END` 줄도 LF 로 끝난다** |

서버 등록 화면이 보내는 프레임:

```text
@SSID=<생산 라인 Wi-Fi>
@PASSWORD=<생산 라인 Wi-Fi 비밀번호>
@SERVER=<서버 도메인>
@MQTTID=<mac>
@MQTTPW=<8자 비밀번호>
@END
```

- `@SSID` · `@PASSWORD` 는 둘 다 입력된 경우에만 보낸다. 없으면 단말의 기존 Wi-Fi 설정을 유지한다.
- 단말은 두 키를 한 쌍으로만 받는다(하나만 보내면 거부).
- 보내지 않은 항목은 바뀌지 않는다. 값 하나라도 잘못되면 아무것도 저장하지 않는다.
- 저장값 적용은 `@OFF` + `@END` 로 재부팅한다.

단말 응답 (적용한 항목만 돌려준다):

```text
@RESULT=OK
@SSID=<ssid>
@PASSWORD=SET
@SERVER=<서버 도메인>
@MQTTID=<mac>
@MQTTPW=SET
@END
```

- 실패 시 `@RESULT=FAIL` 과 `@ERROR=<사유>`.
- 비밀번호 두 항목은 값 대신 `SET`(있음) / `NONE`(없음)만 돌려준다.
- `@GET` + `@END` 로 현재 저장값을 읽을 수 있다(식별값 `@MAC` `@MODEL` `@P4` `@C6MODEL` `@C6` 포함).

### 11.3 설치 확인 순서

1. 서버에 QR 로 단말 등록 → 단말별 MQTT 계정 발급
2. 시리얼로 서버 주소 · 계정(· Wi-Fi) 주입, 재부팅
3. 서버에서 마을 배정 → 단말별 CONFIG 자동 발행
4. STATUS 의 `village_id` · `config_version` · 펌웨어 · RSSI 확인
5. 개별 단말 토픽으로 짧은 시험 음원 방송(`store_flash=0`) → FILE_RESULT · 스피커 출력 확인

시험에는 `all/cmd` 를 쓰지 않는다.

---

## 12. 서버 기능 개요

| 기능 | 주요 동작 |
|---|---|
| 관리자 권한 | 최고 관리자 · 기관 관리자 · 마을 관리자 3단계. 기관을 트리로 구성하며, 관리자는 자기 기관 아래의 마을·단말만 조회·제어한다. 계정별 사용 기간(1~30일 또는 무기한) |
| 지역 관리 | 기관 트리와 마을·구역 관리. 마을 주소를 검색하면 법정동코드·좌표가 채워지고 마을 ID(2.2절)가 발급된다 |
| 단말 관리 | QR 등록과 시리얼 주입, 단말별 MQTT 계정·ACL 자동 생성, 마을·구역 배정(배정 변경 시 CONFIG 자동 재발행), 온라인·RSSI·펌웨어 상태 표시, 계정 재발급·삭제 |
| 방송 제어 | 실시간(관리자 브라우저 마이크 → 스트림 서버 중계)과 파일 방송. 대상은 전체·마을·구역·단말. 단말별 결과 표시와 중지. 대상이 겹치는 방송은 거부 |
| 파일함·TTS | MP3 업로드 시 방송 규격 검사(필요하면 변환), 문장 → 음성 합성, 미리듣기 |
| 자동 스케줄 | 매일·매주·매월·매년 규칙으로 파일 방송 예약. 대상은 마을·단말·기관 전체. 1분 주기로 실행하며, 예정 시각에서 2분 넘게 늦으면 건너뛰고 기록한다 |
| 대시보드 | 지도(마을 경계·단말 위치), 이상 단말, 진행 중 방송, 최근 이력 |
| 설정 | 공통 CONFIG(5.1절), 방송 응답 대기 시간, 라이브·파일 비트레이트(16/24 kbps) |
| OTA | 단말 계약 확정(9장), 서버 배포 기능 준비 중 |

---

## 부록. 시간 값 요약

| 항목 | 값 | 비고 |
|---|---:|---|
| MQTT keepalive | 20초 | |
| 재접속 간격 | 1 → 최대 60초 | 지터 필수 |
| 연결 후 CONFIG 대기 | 최대 10초 | |
| 오프라인 판정 | 300초 | 마지막 메시지 기준 |
| CONFIG 재발행 | 기동 시 + 1시간마다 | 불일치 단말은 즉시(60초에 한 번 이내) |
| LIVE 출력 준비 제한 | 기본 30초 (1~60) | `ready_timeout_sec` |
| 스트림 재접속 | 0.5 · 2 · 4초, 3회 | |
| LIVE 프레임 무수신 종료 | 15초 | 단말 |
| LIVE 종료 결과 대기 | 최대 10초 | 서버 |
| 다운로드 URL 유효 | 600초 | |
| 다운로드 재시도 | 0.5 · 2 · 4초, 3회 | Range |
| FILE 결과 대기 | 기본 30초 (10~60) | 서버 |
| 파일 방송 자연 종료 | 재생 시간 + 5초 | 서버 |
