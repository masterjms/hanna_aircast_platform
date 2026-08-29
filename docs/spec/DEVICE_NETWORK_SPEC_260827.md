# 단말 네트워크 동작 사양

작성: 2026-08-27 (Fable) — `작업지시_단말네트워크사양_2026-08-27.md`에 따라 코드에서 추출. 코드 수정 없음.

**이 문서는 단말이 네트워크를 어떻게 다루는지를 연결·단절·복구 축으로 정리한다.** 독자는 이 펌웨어를 고치거나 현장 문제를 쫓는 사람이다.

경계:

- 메시지 계약(topic, payload, `ok`/`code`)은 `SERVER_COMM_MQTT_ICECAST_HTTP_SPEC_2026-08-14.md`에 있다. 여기 다시 적지 않는다.
- 기능별 동작(LIVE/FILE/OTA가 무엇을 하는가)은 `ESP32_P4_C6_DEVICE_SPEC.md`에 있다.
- **타임아웃·재시도 값은 장치 사양서 §11이 유일한 출처다.** 이 문서는 그 값이 **어느 경로에서 쓰이는지**만 적는다.

근거 표기: P4 = `iot_radio/main/...`, C6 = `slave/main/...`.

**줄 번호 기준: C6 `58bded7`, P4 `c49c45f` (2026-08-27).** 이 문서는 줄 번호를 많이 인용하므로 해당 파일을 고치면 같이 밀린다. 실제로 두 번 밀렸다(초안 4곳, STATUS 필드 삭제 때 31곳). **코드를 고쳤으면 이 문서의 인용도 같이 본다.** 함수 이름은 그대로이므로, 줄 번호가 안 맞으면 이름으로 찾는 편이 빠르다.

---

## 1. 연결 수립 — 부팅부터 첫 STATUS까지

```text
P4 부팅 ──HELLO 반복──▶ C6 부팅
                        C6: 빈 Wi-Fi 설정으로 시작
        ◀──HELLO+READY──
P4: TIME_SET + SERVER_CFG(host·TLS·계정) 전달
P4: Wi-Fi 자격증명으로 접속 지시
                        C6: got_ip → net_client task 생성
                        C6: SNTP (TLS는 시계가 먼저다)
                        C6: MQTT 접속 → retained CONFIG 대기 → 첫 STATUS
```

단계별 근거와 실패 시 행선지:

| 단계 | 근거 | 실패하면 |
|---|---|---|
| P4가 HELLO를 반복 전송 (**모델·버전 포함**) | P4 `network/sdio_rpc.c:1428` 이하 — `while (!s_c6_ready)` | C6 응답까지 계속 반복. P4는 다음 단계로 못 간다 |
| C6는 빈 Wi-Fi 설정으로 시작 | C6 `esp_hosted_coprocessor.c:1055,1067-1068` — `WIFI_STORAGE_RAM` + `blank_cfg` | — (NVS의 이전 세션 설정을 읽지 않는 것이 목적) |
| C6 READY → P4가 TIME_SET·SERVER_CFG 전달 (**SERVER_CFG에 P4 모델·버전 포함**) | P4 `network/sdio_rpc.c:627` 이하 (`sdio_rpc_ready_task`) | SERVER_CFG를 못 받으면 C6는 host가 없어 MQTT를 시작하지 못하고 기다린다 (C6 `net_client.c` `mqtt_broker_uri()` NULL 반환 → "server host not received yet") |
| Wi-Fi 접속은 P4가 지시 | P4 `network/wifi_ctrl.c:186,214,313` — `esp_wifi_connect()` 3곳 모두 실패 시 `wifi_ctrl_schedule_reconnect()` (`:155`) | P4가 재접속을 예약한다. C6는 스스로 재접속하지 않는다 |
| got_ip → net_client task 생성 | C6 `slave_control.c:773-777` → `net_client_on_got_ip()` (C6 `net_client.c:3066`), task 생성 `:3094` | — |
| SNTP가 MQTT보다 먼저 | C6 `net_client.c:3011-3022` — `mqtt_time_ready()` 통과 전에는 `sntp_sync_with_failover()` | SNTP 실패가 이어지면 task 루프가 끝난다 (`:3017-3019`). Wi-Fi 재접속(= 새 got_ip)이 다시 살린다 |
| MQTT 접속 → CONFIG 대기 → 첫 STATUS | C6 `net_client.c:2899` 세션 루프. CONFIG 대기와 첫 STATUS 발행 조건은 서버 사양서 §4.3 | 아래 §2 |

**시계가 왜 먼저인가**: mqtts 인증서 검증은 유효기간을 본다. RTC가 틀리면 정상 인증서도 만료/미래로 판정된다. 그래서 MQTT 진입 전에 시계를 맞추고(`:3011`), 접속 후에도 인증서 시간 오류가 나면 SNTP로 돌아간다(§6).

---

## 2. 끊겼을 때 — 계층마다 다르다

**핵심 표.** 각 칸은 코드로 확인했다.

| 끊긴 것 | 무엇이 도는가 | 진행 중이던 방송은 |
|---|---|---|
| **Wi-Fi** | C6는 재접속하지 않는다 — **P4가 재접속을 예약**한다 (P4 `wifi_ctrl.c:155`). C6 쪽은 `net_client_on_ip_lost()`가 전부 정리하고 task가 끝난다 (C6 `net_client.c:3101-3115`, task 종료 `:3010,3033`) | LIVE: 스트림 정지 + P4에 STOP (`:3108,3111`) / FILE: `NET_ERROR`로 중단 (`:3112`) / P4가 저장 중이던 파일은 P4가 마저 저장한다 |
| **MQTT만** (브로커 단절, Wi-Fi 생존) | `MQTT_EVENT_DISCONNECTED` → `s_mqtt_session_end_req` → 세션 루프 탈출 (`:2899`) → **바깥 backoff**가 돈다 (`:3055-3059`, 값은 장치 사양서 §11) | 세션 정리가 LIVE/FILE을 같이 접는다 (DISCONNECTED 처리 `:2773-2775`, 루프 뒤 `:2951-2953`). **MQTT가 없으면 결과를 서버에 보고할 수 없으므로 방송을 유지하지 않는다** |
| **Icecast 스트림만** | 스트림 task 안의 재시도 (C6 `icecast_client.c:684` 이하, 마감 기준 `s_last_data_us` `:706`). MQTT는 그대로 살아 있다 | 재시도 동안 방송 유지·스피커 무음, STATUS `live=RECONNECTING` (`:716-717` → `net_client_on_live_stream_retry`). 최종 실패 시 task가 P4에 STOP을 보내고 끝난다 (`:746-751`) → P4 응답이 `LIVE_RESULT ok=false`로 나간다 (§5) |
| **SDIO (P4↔C6)** | **직접 감지는 없다. 양쪽 다 간접 감지다.** C6: 전송 실패 시 제어 메시지만 3회 재시도 (C6 `sdio_rpc_slave.c:58-59,222-232`), 이후는 응답 대기 타임아웃 (아래 §2.4) | LIVE: P4가 무프레임으로 정리 (P4 `model.h:47`), C6는 READY 대기·housekeeping에서 회수 (C6 `net_client.c:2518,2541-2542`) / FILE: P4 무청크 (P4 `network/file_rx.c:72`), C6는 FILE_END 응답 대기 회수 (`net_client.c:2494`) / OTA: P4 무청크 (P4 `update/online_ota.c:38`), C6는 P4 상태 대기 (`net_client.c:189,1520`) |

### 2.0 서버는 단말이 끊긴 것을 어떻게 아는가 — 2026-08-27 변경

**STATUS의 `wifi`/`mqtt` 필드를 삭제했다. 서버는 이 둘을 파싱하지 않는다.**

정보가 없는 값이었다. 주기 STATUS는 MQTT로 나가므로 발행 시점에는 반드시 연결돼 있고(`mqtt_publish_status_now()`가 연결 안 됐으면 그냥 돌아간다), Wi-Fi가 없으면 발행 자체가 불가능하다. **그래서 단말이 보내는 STATUS에서 두 값은 언제나 `1`이었다.**

`0`이 나오는 곳은 LWT뿐이었는데, 거기에는 `state:"OFFLINE"`이 이미 같은 말을 하고 있었다. 같은 말을 두 번 하는 필드라 **주기 STATUS와 LWT 양쪽에서 뺐다.** LWT payload는 이제 `type`, `device`, `village_id`, `state` 넷뿐이다.

끊김 판단은 그대로다. 필드를 지우기 전과 후의 동작이 같다 — 실제로 삭제 후에도 서버 대시보드가 단말을 offline으로 정상 표시하는 것을 확인했다.

| 알고 싶은 것 | 봐야 할 것 |
|---|---|
| 살아 있나 | STATUS가 주기(CONFIG의 `status_interval`)대로 도착하는지 |
| 끊겼나 | LWT의 `state == "OFFLINE"` — 브로커가 대신 발행한다 |
| 무선 품질이 나쁜가 | `rssi` |
| 방송 중인데 소리가 안 나가나 | `live == "RECONNECTING"` (§2.3) |

**함께 확인할 것: 숫자형 `reason` 필드는 없다.** 같은 뜻을 문자열 `live`(`OFF` / `PLAYING` / `RECONNECTING`)로 바꾼 지 되었는데 사양서 표만 옛 값을 들고 있었다. 서버가 `reason`을 읽고 있다면 없는 필드를 읽는 중이다.

payload 전체 정의는 `SERVER_COMM_MQTT_ICECAST_HTTP_SPEC_2026-08-14.md` §5에 있다.

### 2.1 Wi-Fi 단절 상세

이벤트 흐름: C6의 Wi-Fi 이벤트 → `sdio_rpc_slave_wifi_disconnected()` (C6 `sdio_rpc_slave.c:864-874`, 호출 `slave_control.c:888`) → **NET_STATE 즉시 발행**(P4 화면이 끊김을 안다) → `net_client_on_ip_lost()`.

`net_client_on_ip_lost()` (C6 `net_client.c:3101-3115`)가 하는 일: Icecast 정지 → `s_net_up=false`, `s_abort=true` → LIVE 정리 → FILE `NET_ERROR` 중단. `s_abort`가 서면 net_client task는 어느 루프에 있든 빠져나와 스스로 죽는다 (`:3062-3063`).

복구: Wi-Fi가 돌아오면 got_ip가 다시 오고 **task가 새로 만들어진다** (`:3090-3097`). 새 task의 backoff는 초기값에서 시작한다 (`:3008`). 즉 **Wi-Fi 복구 뒤 MQTT 재접속은 밀린 backoff 없이 바로 간다.**

### 2.2 MQTT 단절 상세 — 2026-08-27에 고친 경로

**전에는 이 칸이 비어 있었다.** esp-mqtt의 내부 자동 재접속(고정 10초)이 조용히 처리해서 바깥 backoff가 실행되지 않았다 (네트워크 대조 4.2). 지금은:

1. `.network.disable_auto_reconnect = true` — 내부 재접속을 껐다 (MQTT 설정, C6 `net_client.c:2851` 부근)
2. `MQTT_EVENT_DISCONNECTED`에서 `s_mqtt_session_end_req = true`
3. 세션 루프 조건 `!s_mqtt_session_end_req` (`:2899`)로 탈출
4. 플래그는 client **생성 전에** 지운다 — start 뒤에 지우면 그 사이 온 DISCONNECTED를 놓친다

탈출 후 정리(`:2951-2965`): 스트림 정지, LIVE 정리, FILE 중단, client destroy. **매 세션 client를 새로 만들므로 재접속마다 TLS 핸드셰이크를 새로 한다.**

backoff 리셋 조건 둘 (`:3021,3048-3052`): SNTP를 새로 통과했을 때, 직전 세션이 안정 기준(§11) 이상 유지됐을 때. 대기 시간에는 jitter가 더해진다 (`:3055-3056`) — 정전 복구 때 여러 단말이 같은 순간에 몰리지 않게 하려는 것이다.

### 2.3 Icecast 단절 상세

재시도는 스트림 task 안이다 (C6 `icecast_client.c:684` 이하). 마감은 **마지막 수신 시각**(`s_last_data_us`, `:547,:706`) 기준 — 첫 실패 기준이 아니다. 이유와 값은 장치 사양서 §5.3, §11.

재시도 중: 처음 한 번만 `net_client_on_live_stream_retry(true)` (`:716-717`) → STATUS `live=RECONNECTING`. **P4에는 정지를 보내지 않는다** — 보내면 세션이 끝나 재접속해도 소용없다.

최종 실패: task가 P4에 STOP(사유 TIMEOUT/DISCONNECT)을 보내고 `s_active=false`로 끝난다 (`:746-751`). 이 STOP에 P4가 `LIVE_CTRL_READY(ABORT)`로 답하고, 그것이 `LIVE_RESULT ok=false`로 서버에 나간다 (C6 `net_client.c:3122` 이하 `net_client_on_live_ready`). 세션 루프의 스트림 감시(`:2918-2931`)는 이 경로가 못 돌 때의 이중 안전장치다.

### 2.4 SDIO 단절 상세

**전송 계층에 링크 감시가 없다.** 각 기능의 응답 대기가 감지를 대신한다:

| 방향 | 감지 수단 | 근거 |
|---|---|---|
| C6→P4 전송 실패 | 제어 메시지만 3회 재시도 후 로그. FILE_CHUNK/LIVE_FRAME은 재시도하지 않는다(중복이 오디오를 꼬이게 함) | C6 `sdio_rpc_slave.c:48-59,222-238` |
| C6가 P4 무응답 감지 | LIVE: READY 대기 시간 초과 (`cmd_live_housekeeping`, C6 `net_client.c:2518`) / FILE: FILE_END 응답 대기 (`cmd_file_end_wait_housekeeping`, `:2494`) / OTA: P4 상태 대기 (`:1520`) | 값은 §11 |
| P4가 C6 무응답 감지 | LIVE 무프레임 (P4 `model.h:47`) / FILE 무청크 (P4 `network/file_rx.c:72`) / OTA 무청크 (P4 `update/online_ota.c:38`) | 값은 §11 |

**양쪽 워치독이 서로 독립이라, SDIO가 죽으면 P4와 C6가 각자 따로 정리한다.** P4 재부팅은 GPIO로 C6도 리셋하므로 최종 동기화는 재부팅이 맡는다.

---

## 3. 재시도와 백오프 — 어느 경로가 언제 도는가

값은 전부 장치 사양서 §11. 여기는 **트리거 조건**만.

| 재시도 | 시작 조건 | 안 도는 조건 | 근거 |
|---|---|---|---|
| MQTT backoff | 세션이 끝나고 `s_net_up` 유지 (브로커 단절, TLS 시간 오류 재동기 후 실패 포함) | Wi-Fi가 끊겼으면 task 자체가 죽는다 — backoff 없이 got_ip 대기 | C6 `net_client.c:3010-3059` |
| Icecast 재시도 | 읽기 실패/EOF, 그리고 `s_stop_req`가 아닐 때 | 서버 STOP·MQTT 단절로 `s_stop_req`가 서면 재시도하지 않는다 | C6 `icecast_client.c:691-706` |
| FILE 이어받기 | HTTP 읽기 실패. Range로 이어받는다 | 4xx는 재시도 무의미로 즉시 중단 | C6 `net_client.c:1859`, 서버 사양서 §11.1 |
| Wi-Fi 재접속 | 접속 실패·끊김. **P4 소관** | — | P4 `wifi_ctrl.c:155,186-217,313-317` |
| SDIO 제어 재시도 | `esp_hosted_send_custom_data` 실패 | 데이터 프레임(FILE_CHUNK/LIVE_FRAME)은 대상 아님 | C6 `sdio_rpc_slave.c:210-238` |

**주의 — 같은 모양의 루프가 두 개다.** `while (!s_abort && s_net_up)`이 SNTP 쪽(C6 `net_client.c:2206`)과 net_client task(`:3010`)에 있다. 세션 루프(`:2899`)만 `s_mqtt_session_end_req`를 추가로 본다. 고칠 때 줄 번호로 구분할 것.

---

## 4. 무엇을 거절하는가 — 판단 위치

거절 표 자체는 서버 사양서 §5.1(수락 규칙)과 §5.4(code). 여기는 **그 판단이 어디서 나는지**다.

| 거절 | 판단 주체 | 보는 것 | 근거 |
|---|---|---|---|
| LIVE `BUSY` | **C6가 먼저** | `s_ota_ctx.active`/`s_ota_task`, `s_file_ctx.active`/`s_file_task`, `s_live_active`/`s_wait_p4_ready` | C6 `net_client.c:2400-2415` |
| LIVE `BAD_FIELD` | C6 | scheme(`url_scheme_allowed`, `:583`), `job_id=0` | `:2434,2440` |
| LIVE `BUSY` (P4측) | P4도 자체 방어한다 | P4 상태머신 | P4 `live/live_ctrl_task.c:268` |
| FILE 거절 전부 | **P4가 최종** — C6는 META를 보내고 P4의 거절을 기다렸다가 다운로드한다 | 크기·확장자·resume·busy | P4 `network/file_rx.c:1231` 이하, C6 대기 `file_wait_p4_abort_after_meta()` (`net_client.c:1367`) |
| OTA `BUSY`/`BAD_FIELD` | C6 | `ota_system_busy()`, scheme, `job_id`/`size` | C6 `net_client.c:1747` (scheme·필드), busy는 그 아래 |

**FILE만 P4가 최종 심판인 이유**: 저장 공간과 PSRAM은 P4 소관이라 C6가 알 수 없다. 그래서 다운로드 전에 META로 물어본다 — 거절되면 바이트 하나도 안 받는다.

---

## 5. 네트워크 상태의 P4↔C6 전파

### NET_STATE (C6→P4)

Wi-Fi/MQTT 상태·IP·RSSI·사유 코드를 담는다 (C6 `sdio_rpc_slave.c:108-116` 구조체).

발행 시점 둘: **변화 즉시**(Wi-Fi 연결/끊김/IP/MQTT 상태 변화마다, `:861,872,879,885,893`) + **주기 재전송** (`sdio_rpc_net_state_task`, `:320-327`, 주기 `:32`). 주기 재전송은 SDIO로 한 번 유실돼도 P4 화면이 어긋난 채 남지 않게 하는 안전장치다.

### OUTPUT_STATE (P4→C6)

P4의 실제 출력 상태(IDLE/FILE/RF)가 C6로 와서 STATUS `state`와 `busy`에 반영된다 (C6 `net_client.c:2439-2442` 부근, busy 판정에도 들어간다). 상세는 장치 사양서 §9.

### 펌웨어 버전 — P4에서 C6를 거쳐 STATUS까지

```text
P4 model.h                     C6 RAM                    서버
MODEL_SYSTEM_VERSION ──HELLO──▶ s_p4_system_version ──▶ STATUS "p4_fw"
                    └SERVER_CFG┘
C6 model.h
C6_MODEL_SYSTEM_VERSION ─────────────────────────────▶ STATUS "c6_fw"
```

| 필드 | 출처 | SDIO를 타나 |
|---|---|---|
| `p4_fw` | P4 `model.h` `MODEL_SYSTEM_VERSION` | **탄다** — HELLO와 SERVER_CFG 두 경로 |
| `c6_fw` | C6 `model.h` `C6_MODEL_SYSTEM_VERSION` | 안 탄다. C6 자기 빌드값이다 |

근거: C6 보관 `sdio_rpc_slave.c:105-106`, STATUS 조립 `net_client.c:2617-2618,2624,2637`.

**왜 필요한가**: OTA가 실제로 적용됐는지는 재부팅 여부가 아니라 **버전으로만** 구분된다. 롤백된 단말도 재부팅은 한다. 서버 사양서 §5 참고.

#### 서버가 이 값을 읽는 법

```text
OTA_RESULT ok=true  ──▶  연결 끊김  ──▶  재부팅  ──▶  STATUS 도착
                                                    p4_fw 가 새 버전 → 적용 성공
                                                    p4_fw 가 옛 버전 → 롤백됨
                                                    p4_fw 가 빈 값   → 아직 판정하지 않는다
```

**주기 STATUS마다 들어간다.** 한 건을 놓쳐도 다음 STATUS로 판정할 수 있다.

#### 두 경로로 온다 — HELLO 한 번으로는 부족하다

| 경로 | 시점 | 근거 |
|---|---|---|
| HELLO | 부팅 직후, C6 READY까지 반복 | P4 `network/sdio_rpc.c:1428` 이하 |
| **SERVER_CFG** | **C6가 READY를 올릴 때마다** | P4 `network/sdio_rpc.c` `sdio_rpc_send_server_cfg()` 끝부분 |

**HELLO만으로는 C6가 혼자 재부팅했을 때 버전이 영영 안 온다.** 부팅 HELLO는 `s_c6_ready`가 서면 task가 끝나고 다시 돌지 않는데, 그 플래그는 한 번 `true`가 되면 내려가지 않는다(P4 `:821`). C6가 크래시·워치독으로 재부팅하면 `p4_fw`가 P4 재부팅 때까지 빈 문자열로 남는다.

**그래서 SERVER_CFG에 같이 싣는다.** SERVER_CFG는 `ready_task`에 있고, 그 task는 끝나면서 자기 핸들을 되돌리므로(`:588`) READY마다 다시 돈다.

##### HELLO를 재전송하면 안 된다 — 실제로 겪은 무한 재부팅

처음에는 `ready_task`에서 HELLO를 다시 보내는 방법을 썼다. **단말이 부팅 직후부터 계속 재부팅했다.**

```text
P4 ready_task ──HELLO──▶ C6
C6: HELLO를 받으면 HELLO+READY로 응답한다 (C6 `sdio_rpc_slave.c:742-743`)
    그 HELLO의 boot_id는 보낼 때마다 증가한다 (C6 `:343`)
P4: boot_id가 바뀌었다 → "C6가 재부팅했다" → 자기를 재부팅 (P4 `:808-811`)
    → 다시 ready_task → HELLO → ... 무한 반복
```

**HELLO는 응답을 유발하는 메시지다.** 재전송 용도로 쓸 수 없다. SERVER_CFG는 단방향이라 이 문제가 없다.

#### `p4_fw`가 빈 문자열일 때

C6가 아직 P4에게서 값을 못 받은 것이다. **그 STATUS로는 버전을 판정하지 않는다.**

| 언제 | 정상인가 |
|---|---|
| 부팅 직후, SERVER_CFG 도착 전 | **정상.** 짧은 구간이다 |
| 계속 비어 있다 | SDIO 문제이거나, 이 필드를 안 보내는 구버전 P4다 |

`c6_fw`는 컴파일 상수라 **빈 값이 나올 수 없다.** `c6_fw`는 차 있는데 `p4_fw`만 비어 있으면 **P4↔C6 구간을 의심한다** — MQTT는 멀쩡하다는 뜻이기 때문이다.

### 어긋나면

- HELLO가 유실되면: 부팅 중이면 재시도가 덮고, 그 뒤로는 **SERVER_CFG가 READY마다 같은 값을 다시 실어 온다.**
- NET_STATE가 유실되면: P4 화면이 옛 상태를 보이다가 **최대 주기 시간 안에** 재전송으로 맞는다.
- OUTPUT_STATE가 유실되면: C6의 STATUS `state`가 실제 P4 출력과 어긋난다. 서버에서 "IDLE인데 소리가 난다"로 보이면 이 경로를 의심할 것.

---

## 6. TLS — 네트워크 관점

구성(빌드 선택, 인증서 번들)은 장치 사양서 §10. 여기는 두 가지만.

### 검증이 붙는 곳 — 네 군데 전부

| 경로 | 근거 |
|---|---|
| MQTT | C6 `net_client.c:2857` |
| Icecast 스트림 | C6 `icecast_client.c:490` |
| FILE 다운로드 | C6 `net_client.c:1871` |
| OTA 다운로드 | C6 `net_client.c:1529` |

전부 같은 커스텀 번들이므로 인증서 요건도 같다. 단 **검증은 URL이 https일 때만 돈다** — 그래서 scheme 검사(`url_scheme_allowed`, `:583`)가 별도로 있다 (네트워크 대조 4.1).

### 시계가 틀렸을 때

1. MQTT 접속 중 인증서 시간 오류(만료/미래 판정) → `s_mqtt_tls_time_fail` (C6 `net_client.c:2789-2796`)
2. 세션 루프가 이를 보고 스스로 끝난다 (`:2900-2902`)
3. task 루프가 SNTP 재동기 후 재접속한다 (`:3037-3046`)

즉 **시계 문제는 재시도 대상이 아니라 SNTP로 돌아가는 별도 경로다.** 시계를 안 고치고 재시도만 하면 같은 오류만 반복되기 때문이다.

---

## 7. 이 문서가 답해야 하는 질문 (자기 검사)

- **"서버는 단말이 끊긴 것을 어떻게 아나?"** → §2.0. LWT의 `state="OFFLINE"`. STATUS의 `wifi`/`mqtt`는 삭제했다 — 항상 `1`이라 쓸모가 없었다.
- **"MQTT가 끊기면?"** → §2 둘째 행 + §2.2. 세션 접고 방송 정리, backoff로 재접속, 매번 새 TLS 핸드셰이크.
- **"Wi-Fi가 끊기면?"** → §2 첫 행 + §2.1. C6 task 죽음, P4가 재접속 예약, 복구 시 backoff 초기화.
- **"방송 중 스트림만 끊기면?"** → §2.3. 재시도 동안 무음 유지, 실패 시 P4 왕복을 거쳐 `LIVE_RESULT ok=false`.
- **"SDIO가 죽으면?"** → §2.4. 직접 감지 없음, 양쪽 워치독이 각자 정리, 최종 동기화는 재부팅.
- **"OTA가 실제로 적용됐나?"** → §5. 재부팅 후 STATUS의 `p4_fw`/`c6_fw`. 재부팅했다는 것과 새 펌웨어가 올라갔다는 것은 다르다.
