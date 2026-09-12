xWIFI 운영서버 API 설계 (v1)

DB 스키마(`xWIFI_DB_스키마_260815.md`) 위에서 REST API를 뽑는다. 이것도 80점 기준 — 엔드포인트 목록과 권한/관련 테이블만 정리하고, 세세한 요청/응답 필드는 실제 구현하면서 다듬는다.

공통 원칙:
- 모든 요청은 로그인 토큰 필요(로그인 자체 제외).
- village_admin 계정은 서버가 자동으로 담당 village_id 범위로 필터링/차단한다 — 프론트가 알아서 숨기는 게 아니라 백엔드가 강제한다.
- 방송/설정/OTA 트리거 계열 API는 내부적으로 "요청 검증 → MQTT payload 생성(§통신 사양 그대로) → 발행 → broadcast_events insert → 응답"의 동일한 패턴을 따른다.

## 1. 인증

```
POST /api/auth/login       {username, password} -> {token}
POST /api/auth/logout
GET  /api/auth/me          -> {username, role, villages: [...]}
```

**계정 사용 기간 (2026-09-07, 문제점 26번)**: `users.expires_at` 이 지난 계정은 로그인이 401 `ACCOUNT_EXPIRED` 로 막히고, 이미 발급된 토큰도 그 시점부터 거절된다(`get_current_user`). 정리 작업이 매시간 돌며 만료된 계정을 지운다. `expires_at` 이 NULL 이면 무기한이다. 만료된 `super_admin` 이 마지막 한 명이면 지우지 않고 로그인만 막는다 — 기간을 잘못 걸어 관리자가 사라지는 상황을 만들지 않는다.

## 2. 단말 관리

```
GET    /api/devices                 목록 (역할 범위로 자동 필터)
GET    /api/devices/unassigned      미배정 단말(village_id NULL) 목록
GET    /api/devices/:mac            상세 (last_status 포함)
POST   /api/devices                 등록 {mac, label, village_id, zone_id, p4/c6 모델·버전, mqtt_password}
POST   /api/devices/credential      신규 등록용 비밀번호+서버호스트 사전 발급 (super_admin, DB 미기록)
PATCH  /api/devices/:mac            수정/재배정
DELETE /api/devices/:mac            삭제 (DB 행 + 브로커 계정을 한 묶음으로 제거)
POST   /api/devices/:mac/credential 단말별 MQTT 계정 발행/조회 {reissue} (super_admin)
```

권한: village_admin은 자기 담당 마을 소속 단말만 조회/수정 가능. 미배정 단말은 super_admin만 배정 가능(아직 마을 소속이 없으므로).

STATUS 메시지를 백엔드가 구독하다가, devices 테이블에 없는 MAC이 오면 자동으로 village_id=NULL로 insert한다 — "미배정 단말 목록"이 여기서 자연스럽게 채워진다(별도 등록 절차 없이 전원만 넣으면 뜨는 방식).

**MQTT village_id 는 법정동코드 12자리다 (2026-09-05, 문제점 16번 — 레지스트리 사양 §2.4)**: `villages.village_code` = 법정동코드(10) + 마을 연번(2). 주소 검색으로 `b_code`가 처음 채워질 때 같은 리 안에서 비어 있는 첫 연번(01, 02…)으로 한 번 만들고, 그 뒤 `b_code`가 바뀌어도 그대로 둔다(행정구역 개편 때 바꾸면 그 마을 전 단말 재설정 + 과거 이력 단절). 단말은 값을 해석하지 않고 `iotradio/village/<값>/cmd`에 그대로 끼워 구독하며 8~16자리를 받으므로, 주소가 없어 코드를 못 만든 마을(초기 시험 마을)은 예전 방식(DB id 8자리 제로패딩)을 그대로 쓴다 — 두 형식이 섞여도 동작한다. 발행 토픽·ACL·CONFIG·불일치 검사가 전부 `app/core/village_token.py`의 한 함수(`token_for`)를 거친다. 목록 응답의 `village_token`이 실제 나가는 값이고 `village_code`는 12자리 코드(없으면 null). 마이그레이션 0012가 기존 마을에 코드를 부여하고 `config_version`을 한 번 올려, 기동 시 재조정이 새 값을 전 단말에 내린다. 주소를 처음 넣어 토큰이 legacy→12자리로 바뀌는 PATCH는 그 자리에서 CONFIG 재발행 + ACL 재생성을 한다.

**미배정 CONFIG 는 빈 retain 이 아니라 전부 0 을 명시한다 (2026-09-05, 문제점 18번)**: 마을 배정 해제·마을 삭제·단말 삭제 때 예전에는 빈 payload로 retain을 지웠는데, 단말은 빈 payload를 무시해서(`net_client.c:830`) 이전 배정이 그대로 남았다. 이제 `config_version`을 올린 뒤 `{"config_version": <새 값>, "village_id": "000000000000"}`을 발행한다. 전부 0은 사양 §2.2의 미배정 값이라 단말이 마을 topic 구독을 끊는다. 재조정(`config_reconcile`)도 미배정 단말에 같은 값을 같은 버전으로 보내므로 매 주기 무해하다.

**STATUS 수신은 모아서 쓴다 (2026-09-02, A-2/A-3)**: 주기 STATUS 는 이력이 아니라 캐시 갱신이라(같은 단말은 최신값만 의미가 있다) MAC별 최신값만 메모리에 모았다가 `STATUS_FLUSH_INTERVAL_SEC`(기본 1초)마다 다중 행 upsert 한 번으로 쓴다. 자동 등록도 이 upsert가 그대로 처리한다. CONFIG 불일치 자동 복구(§8, 사양 §4.3) 판정도 같은 flush에서 묶여 돌아서, 전 단말이 30초마다 같은 `current_config` 행을 다시 읽던 조회가 flush당 한 번으로 줄었다. 결과·LWT는 종전대로 즉시 쓴다. 화면에 상태가 보이기까지 최대 1초 지연이 생기고, 값을 0으로 두면 예전 방식(메시지당 트랜잭션)으로 되돌아간다.

**단말별 MQTT 계정 (2026-08-30, `SERVER_DEVICE_CREDENTIAL_SPEC_2026-08-27.md`)**: username=콜론 없는 소문자 MAC, password=8자 랜덤(문자 집합 사양 §1, `@`·`!` 제외). 발행하면 DB에 평문 보관(등록 화면 표시·시리얼 투입용)하고, 백엔드가 mosquitto passwd 파일(해시)을 통째로 재생성해 공유 볼륨으로 내보낸다 → mosquitto entrypoint 감시 루프가 설치+SIGHUP 리로드. 응답 `{username, password, issued}` — 이미 발행된 단말은 기존 값 재사용(`issued:false`), `reissue:true`는 라인 재작업 전용. 등록(POST /api/devices)은 자동으로 계정을 함께 발행하고, 삭제는 계정도 함께 지운다(도난 단말 차단 수단). 계정 미발행 단말은 목록 응답의 `has_credential:false`로 구분되어 화면에 「미등록*」으로 표시되고, 공유 계정(이행기 `MQTT_DEVICE_PASSWORD`) 제거 후에는 방송 대상에서도 제외된다(레지스트리 사양 §3.6).

**마을별 ACL 자동 생성 (2026-08-31, 통신 사양 §2.1 "별도 규칙")**: `village/<village_id>/cmd`는 MAC이 아니라 서버 배정값이 들어가 `%u`로 못 잡으므로, 백엔드가 passwd와 같은 통로로 **aclfile도 생성**한다(`mqtt_accounts.render_acl` → 공유 볼륨 `aclfile.generated` → entrypoint 감시 루프가 `/mosquitto/data/aclfile`로 설치+SIGHUP). 단말마다 `user <mac>` 블록에 **배정된 마을 topic 한 줄만** 열리고 와일드카드(`village/+/cmd`)는 쓰지 않는다 — 계정 하나가 뽑혀도 그 마을 하나만 노출된다. 등록·삭제·마을 배정 변경·마을 삭제 때 passwd와 함께 재생성. 리포의 `infra/mosquitto/config/aclfile`은 첫 기동용 시드일 뿐이다. 공유 계정(xwifi-device)과 `%c` 규칙은 2026-08-31 폐기 완료.

**시리얼 주입 프레임 (2026-08-31 확정)**: 두 계정 API 응답에는 `server_host`(`PUBLIC_BASE_URL`에서 스킴·포트를 뗀 호스트)가 함께 실린다. 등록 화면은 이것으로 아래 한 프레임을 만들어 USB(Web Serial, 115200) 또는 복사·붙여넣기로 단말에 넣는다.

```text
@SERVER=hanna-aircast.co.kr\n@MQTTID=58e6c5f2cc74\n@MQTTPW=tA$UAcG2\n@END\n
```

(\n = 개행 `0x0A`. 위 한 줄이 실제로 나가는 바이트 전부다.)

개행은 LF(`0x0A`) 고정이고 **모든 명령 줄은 개행으로 끝난다 — `@END` 앞에도 개행이 필요하다** (2026-08-31 실물 단말 로그로 확정: 파서는 줄 단위로 먼저 자른 뒤 `@KEY=VALUE`를 읽는다). `@MQTTPW=<값>@END`처럼 한 줄에 붙이면 `@END`까지 비밀번호에 들어가 브로커 인증이 조용히 실패한다 — 신규 등록 첫 실물 테스트에서 실제로 발생했던 사고다. 구현은 `frontend/src/lib/serial.ts` 한 곳에 모아 두 화면이 갈라지지 않게 했다.

## 3. 기관 / 마을 / 구역

```
GET    /api/organizations                내 관할 기관(부분 트리, 평평한 목록 — parent_id 로 화면이 트리를 세움).
                                          바로 아래 마을·계정·기관 수 포함
POST   /api/organizations                {name, parent_id}   기관 관리자 이상. parent 는 내 관할 안. null(뿌리)은 super_admin 만
PATCH  /api/organizations/:id            {name?, parent_id?}  옮기기는 출발·도착 모두 관할. 자기/후손 아래로는 ORG_CYCLE
DELETE /api/organizations/:id            하위 기관·마을·계정 있으면 ORGANIZATION_IN_USE

GET    /api/villages            목록 (역할 범위)
POST   /api/villages            생성 {name, organization_id, sido, sigungu, address_detail, …}
PATCH  /api/villages/:id        organization_id 변경 = 관리 기관 이관(출발·도착 모두 관할이어야)
DELETE /api/villages/:id

GET    /api/villages/:id/zones
POST   /api/villages/:id/zones
PATCH  /api/zones/:id
DELETE /api/zones/:id
```

**권한 (2026-09-08, v2 09-12 — [관리자 계층 설계](xWIFI_관리자_계층_설계_260908.md))**: 관리자는 최고 > 기관 > 마을 세 역할이고 범위는 조직 트리(깊이 무제한)의 **부분 트리**를 따른다 — 마을의 주소는 트리의 입력값이 아니다(위탁 마을). 기관·마을 생성·수정·삭제·옮기기는 기관 관리자 이상이 관할 안에서(뿌리 기관은 최고 관리자만), 구역은 이장도 자기 마을 안에서. 계정(`/api/users`)은 기관 관리자 이상이 **자기보다 아래 마디**만 만들고 고친다 — 같은 마디의 기관 관리자는 동료라 `TIER_TOO_LOW`. 단말 마을 이동은 기관 관리자 이상, 신규 단말 등록·삭제·OTA·CONFIG 는 최고 관리자만. 진행 중 방송은 대상 단말의 소속 마을 기준으로 보이고, 보이면 중지할 수 있다. 응답 `organization_id`·`organization_name` 은 `org_admin` 에만 있다.

**마을 경계 (2026-09-03)**: `villages.boundary` 에 GeoJSON geometry(WGS84)를 넣으면 대시보드 지도가 마을 영역을 그린다. 값은 `PATCH /api/villages/{id}` 의 `boundary` 로 들어가고, 넣는 것은 사람이 아니라 `scripts/import_boundaries.py`(담당자 PC 에서 실행)다. 목록 응답에는 도형 대신 `has_boundary` 불리언만 실린다 — 행마다 수십 KB 가 붙으면 화면이 느려진다. 도형 자체는 `GET /api/dashboard/map` 으로만 내려간다. 데이터 출처와 이용조건은 지도 설계 §4.8.

## 4. 방송 제어

```
POST /api/broadcast/live/start   {target_scope, target_ids[]}
POST /api/broadcast/live/stop    {broadcast_id}
POST /api/broadcast/file/start   {target_scope, target_ids[], file_id}
POST /api/broadcast/file/stop    {broadcast_id}
GET  /api/broadcast/active       현재 진행 중인 방송 목록(대시보드 패널용)
```

target_scope는 device/zone/village/all 중 하나. **target_ids는 목록이다**(2026-08-24) — 마을 여러 곳을 한 방송으로 묶는 "다중 마을 동시 방송"을 값 하나로는 표현할 수 없어서다. scope=all이면 빈 목록.

  village  target_ids = 마을 id 들   예) ["1","2"]  → 두 마을이 같은 방송을 받는다
  zone     target_ids = 구역 id 들
  device   target_ids = MAC 들       예) 한 마을에서 단말 3대만
  all      target_ids = []

**target_label (2026-09-07, 문제점 33번)**: 방송 조회 응답(`/api/broadcast/*`, `/api/dashboard/summary` 의 `active_broadcasts`·`recent_events`)에 사람이 읽는 대상 이름을 같이 담는다. 화면이 내부 id 를 그대로 그려서 "village 5, 6" 처럼 보이던 것을 고친 값이다. `village`·`zone`·`device` 는 이름(구역은 「마을 구역」)으로 바꾸고, 3곳을 넘으면 "가, 나 외 2곳"으로 접는다. `all` 은 「모든 마을」이다. 지워진 마을·단말은 이름을 찾을 수 없으므로 id 를 그대로 남긴다 — 이력에서 대상이 빈칸이 되는 것보다 낫다. 대시보드는 최근 이력을 여러 건 그리므로 id 를 모아 종류별로 한 번씩만 조회한다.

village_admin은 all과, **목록 중 하나라도** 담당 밖이면 403(일부만 나가는 방송은 의도한 결과가 아니므로 전체를 거절). 내부적으로 zone/village/all은 백엔드가 devices 테이블을 조회해서 MQTT는 대상 마을마다 iotradio/village/<id>/cmd 로, 또는 개별 device 토픽으로 발행한다(§통신 사양 그대로). **다중 마을이어도 job_id와 stream_url은 하나**다 — 여러 토픽에 같은 payload를 내보내 단말들이 같은 마운트로 모인다.

라이브 방송의 Icecast 마운트는 `/live/<job_id>`. 마을을 경로에 넣지 않는 이유는 다중 마을 방송을 경로로 표현할 수 없어서다("어느 마을인가"는 이력의 target_ids가 답한다). 단말은 LIVE_START.stream_url 문자열을 그대로 쓰므로 경로 구조는 서버 재량이다.

**무음 방송 자동 종료**: 마지막 오디오 수신 후 LIVE_UPLINK_GRACE_SEC(기본 30초) 동안 마이크 업링크(/ingest)에서 오디오가 오지 않으면 서버가 방송을 자동 종료한다. 화면에는 ON AIR로 보이는데 스피커는 조용한 상태가 프로덕션에서 가장 위험하기 때문이다. 한 번도 붙지 않은 경우와 붙었다 끊긴 경우를 같은 기준으로 본다. 중지 응답을 기다리는 중인 세션은 대상이 아니다.

### 방송 종료 판정 (2026-09-02, 문제점 리스트 3·4·5번)

`broadcast_events`에 `expected_count`(발행 시점에 명령을 보낸 대수)와 `stop_requested_at`(중지를 누른 시각)을 남긴다. 종료를 확정하는 경로는 넷이고 전부 `end_event()`를 거친다(그래야 `bytes_estimated`가 빠지지 않는다).

**서버가 보는 국면(`phase`, 2026-09-03 문제점 10번)** — 화면 머리말에 그대로 나간다.

| 종류 | 국면 | 넘어가는 조건 |
|---|---|---|
| 라이브 | 준비 중 → **송출 중** | `LIVE_READY ok=true`가 `expected_count`만큼 |
| 라이브 | → 중지 중 → 종료 | 중지 → `LIVE_RESULT` 전원 도착 또는 `live_stop_wait_sec` 경과 → **그때 스트림 닫기** |
| 파일 | 전송 중 → **재생 중** | `FILE_RESULT ok=true`가 전원 도착 = 다 받고 검증 끝, **이때 재생이 시작**된다(`playing_since`). 저장은 방송 중 백그라운드 |
| 파일 | 재생 중 → 종료 | `playing_since` + 재생 길이(`files.duration_sec`, 없으면 10분) + 5초 |
| 파일(autoplay=false) | 전송 중 → 저장 완료 → 종료 | 저장만 하는 방송은 `FILE_RESULT`가 곧 끝 |
| 파일 | → 중지 중 → 종료 | 중지 → 종료 응답 전원 도착 또는 `file_wait_sec` 경과 |

예전에는 파일 방송을 `FILE_RESULT`에 바로 끝냈다. `FILE_RESULT`는 「받아서 저장까지 끝냈다」는 신호고 그 뒤에 재생이 시작되므로(단말 요청 2026-09-03 §2.3), 스피커가 나오는 중에 화면은 「종료」였다. 이제 마지막 단말의 저장 완료 시각을 재생 시작으로 보고 재생 길이 뒤에 끝낸다 — 마지막 단말이 가장 늦게 시작하니 그 기준이면 전원이 끝난 뒤다. 재생 완료 신호는 단말 프로토콜에 없어서(파일은 응답이 `FILE_RESULT` 하나) 시간으로 판단한다.

| 종료 경로 | 언제 | 비고 |
|---|---|---|
| 전 단말 응답 | 종료 결과(`FILE_RESULT`·`LIVE_RESULT`)를 보낸 단말 수가 `expected_count`에 도달 | 라이브·중지 대기 중·저장만 하는 파일은 즉시 종료. 재생하는 파일은 「재생 중」으로 |
| 재생 완료 | `playing_since` + 재생 길이 + 5초 | 파일만 |
| 중지 응답 대기 만료 | 중지 후 `live_stop_wait_sec`(10~30초, 기본 10) / `file_wait_sec`(10~60초, 기본 30) 경과 | 못 받은 대수를 로그에 남긴다 |
| 파일 수신 완료 상한 | 시작 + `file_wait_sec` 경과인데 **아직 응답 안 한 단말이 있을 때만** | 전원이 수신·검증을 마쳐 재생 중인 방송은 이 경로로 끊지 않는다 |
| 기동 시 고아 정리 | 서버 재시작 | `close_orphaned_events`, 아래 §6 |

**중지는 즉시 종료가 아니다.** 예전에는 `/stop`이 단말 응답을 보지 않고 바로 `ended_at`을 찍어서, 단말이 실제로 멈췄는지와 무관하게 화면만 "중지됨"이 됐다. 이제 `stop_requested_at`만 찍고 단말의 종료 결과를 기다린다 — 다 오면 그 순간, 안 오면 대기 시간 뒤에 확정한다. 대기 중에 다시 중지를 누르면 `BROADCAST_STOP_PENDING`(409)으로 거절한다. 대기 중인 방송도 겹침 검사에는 계속 잡히므로, 확정 전에는 같은 대상으로 새 방송을 시작할 수 없다.

**라이브 종료 순서 — 스트림은 마지막에 닫는다 (2026-09-03, 단말 요청 `SERVER_BROADCAST_STOP_SEQUENCE_2026-09-03.md` §1)**

```
LIVE_STOP 발행 → 단말별 LIVE_RESULT 대기(최대 live_stop_wait_sec) → 종료 확정 → 스트림(mount·source) 닫기
```

대기 중에도 mount와 source는 살아 있다. 예전에는 `LIVE_STOP`과 스트림 닫기가 거의 동시에 나갔고, 단말은 스트림이 끊기면 「끊긴 건지 끝난 건지」를 스스로 판단해야 했다 — 그 판단이 어긋나 정지한 단말이 571ms 뒤 다시 붙는 일이 실기에서 잡혔다(2026-09-02). 정지 명령이 먼저 도착하면 단말은 멀쩡한 스트림에서 스스로 끊으므로 해석할 일이 없다. 스트림 닫기는 `end_event`가 맡는다 — 종료 확정과 한 곳에 묶어 순서가 어긋날 여지를 없앴다. 대기 중에는 마이크가 이미 끊겨 무음이 쌓이지만 무음 워치독은 `stopping` 세션을 건드리지 않는다.

**종료된 job_id의 늦은 telemetry는 버린다** (같은 문서 §2.5). 정지 보고 뒤에도 그 방송의 `LIVE_STATS`가 한 번 더 도착할 수 있고(실기 367ms 뒤), 그걸 적재하면 끝난 방송이 잠깐 되살아나 보인다. 서버는 최근 종료한 job_id를 기억해 두고 `LIVE_STATS`·`OTA_PROGRESS`를 버린다. 결과(`LIVE_RESULT` 등)는 이력이라 그대로 남기되, 종료 판정에는 쓰지 않는다(이미 끝난 방송이라 `finish_if_all_reported`가 대상을 찾지 못한다).

**한 대만 정지시키는 API는 없다.** 중지는 방송 단위다. 그래서 "한 대만 정지할 때 스트림을 닫지 않는다"는 규칙은 지금 구조에서는 해당 경우가 생기지 않는다.

**라이브 준비 지연 알림 기준 = `live_ready_timeout_sec` + 5초** (같은 문서 §3.1). 서버가 `LIVE_START.ready_timeout_sec`로 30을 보내면 단말은 **35초**까지 기다렸다가 `LIVE_READY`를 보낸다(+5는 펌웨어 상수). 서버가 30초에 「준비 안 됨」으로 알리면 32초에 준비를 마친 단말에서 소리가 나는 구멍이 생기므로, 알림 기준은 별도 설정이 아니라 보낸 값 + 5로 계산한다. 서버는 이 시각에 아무것도 강제하지 않는다 — 그대로 방송할지, 더 기다릴지, 중지할지는 방송하는 사람이 정한다.

**LIVE_READY ok=true 뒤에 온 BUSY 는 버린다 (2026-09-05, 문제점 17번)**: MQTT QoS 1은 중복 배달이 가능하다. 브로커가 `LIVE_START`를 재배달하면 정상 방송 중인 단말도 사양 §5.1(선착순)대로 `LIVE_READY ok=false code=BUSY`로 답한다. 이미 같은 job에 `ok=true`를 받은 단말의 BUSY는 실패가 아니므로 이력에 넣지 않는다 — 넣으면 화면이 그 단말을 실패로 뒤집는다(단말 확인 2026-09-04).

**진행률 막대의 의미**: `(성공 + 실패) / expected_count`. 즉 "응답이 온 비율"이고 성공률이 아니다. 성공은 라이브면 `LIVE_READY ok=true`(P4 오디오 준비 완료), 파일이면 `FILE_RESULT ok=true`(재생까지 끝남)다. 분모를 `expected_count`로 고정한 이유는, 방송 중에 단말이 꺼지거나 배정이 바뀌면 매번 다시 센 분모가 흔들려서 100%가 영영 안 되기 때문이다.

**⚠ `LIVE_RESULT`는 준비 신호가 아니다.** 통신 사양(§5.4, 2026-08-27 개정)은 `LIVE_READY`=준비 결과, `LIVE_RESULT`=**종료** 결과로 나눈다. `LIVE_RESULT ok=true`는 `STOPPED_BY_SERVER`, 즉 방송이 끝났다는 뜻이라 방송 중에는 오지 않는다. 따라서 "`LIVE_READY`와 `LIVE_RESULT`가 둘 다 true여야 라이브 준비 완료"로 판정하면 방송은 영원히 준비되지 않는다. 스트림 연결 실패는 같은 job_id로 오는 `LIVE_RESULT ok=false`로 알 수 있고, 실제 재생 여부는 STATUS의 `live=PLAYING`이 답한다.

## 5. 파일 라이브러리

```
GET    /api/files
POST   /api/files              업로드(multipart), size/sha256 서버가 계산
                               ?transcode=true = "규격에 맞게 변환해도 좋다"는 사용자 확인
POST   /api/files/tts          {text, lang, voice} -> Polly 호출 -> 파일 생성
DELETE /api/files/:id
GET    /api/files/:id/audio    미리듣기/다운로드
GET    /dl/:token              단말 전용 다운로드 — 로그인 없음, FILE_START 의 단기 토큰만. Range 지원
```

**파일 삭제를 막는 것은 스케줄뿐이다 (2026-09-09, 0017)**: 예전에는 한 번이라도 방송한 파일이 영영 지워지지 않았다 — `broadcast_events.file_id` 가 제약 없는 FK 라 DB 가 삭제를 거부했고, 화면에는 「스케줄이나 이력에서 사용 중」이라고만 떠서 스케줄에 넣은 적 없는 사람은 이유를 알 수 없었다. 같은 부류의 세 번째다(0006 `device_events.mac`, 0014 `users` 참조 셋). 원칙은 같다 — **이력은 남되 참조당하는 쪽의 삭제를 막지 않는다.** 방송 시작 시점의 파일명을 `broadcast_events.file_name` 에 박아 두고(`expected_count`·`bytes_estimated` 와 같은 방식) `file_id` 를 `ON DELETE SET NULL` 로 바꿨다. 파일을 지워도 이력의 「무엇을」은 남고 링크만 끊긴다. 스케줄은 계속 막는다 — 파일이 사라진 스케줄은 걸릴 때마다 조용히 실패하므로, 대신 어느 스케줄인지 이름을 대 주고 파일함 목록에서도 미리 보여준다(`FileOut.schedule_labels`).

**업로드 오디오 규격 검사 (2026-09-07, 문제점 31번)**: 업로드된 mp3 를 `ffprobe` 로 재서 방송 규격(16kHz · mono · `file_bitrate_kbps`)과 비교한다.

| 파일 | 처리 | 응답 |
|---|---|---|
| 규격보다 낮다 (표본율 또는 비트레이트) | 거절 | 400 `AUDIO_QUALITY_TOO_LOW` |
| 규격과 같다 | 그대로 등록 | 201 |
| 규격보다 높다 (표본율·채널·비트레이트 중 하나라도) | 확인 후 재인코딩 | 400 `AUDIO_NEEDS_TRANSCODE` → 화면이 팝업으로 묻고 `?transcode=true` 로 재전송 |

낮은 파일을 거절하는 이유: 다시 인코딩해도 없는 음질이 생기지 않고, 단말에는 규격 하나만 내려보내야 P4 디코더가 한 가지만 다룬다. 재인코딩은 `-map_metadata -1` 로 ID3 를 포함한 내부 tag 를 전부 버린다. CBR 인코더도 헤더 오버헤드로 1kbps 정도 어긋나므로 비트레이트 비교에는 ±1 여유를 둔다. `ffprobe` 가 없는 환경(개발)에서는 검사를 건너뛰고 그대로 받는다 — 길이 계산과 같은 방침이다.

**다운로드 바이트는 nginx 가 보낸다(2026-09-02)**: `/dl/:token` 과 `/api/files/:id/audio` 는 백엔드가 토큰·권한만 검증하고 `X-Accel-Redirect: /_files/<storage_path>` 헤더를 돌려준다. nginx 가 같은 파일 볼륨(읽기전용)을 `internal` location 으로 sendfile 서빙하므로, 마을 단위 FILE_START 로 단말 수백 대가 동시에 받아도 파이썬 프로세스는 요청당 DB 조회 한 번뿐이다. Range(resume_offset 재개)도 nginx 가 처리한다. 개발 환경(nginx 없음)은 `FILE_ACCEL_LOCATION` 을 비워 두면 백엔드가 FileResponse 로 직접 보낸다. nginx access log 에 요청별 `$body_bytes_sent`·`$request_time` 이 남아 단말 다운로드 트래픽을 그대로 셀 수 있다.

## 6. 이력

```
GET /api/events              필터(마을/기간/타입) + 페이지네이션, 역할 범위 적용
GET /api/events/:id          상세 (device_events 결과 포함)
```

**bytes_estimated (2026-09-02, A-8/D-1)**: 단말은 실제 전송 바이트 수를 보고하지 않는다(통신 사양에 그런 필드가 없다). 방송이 정상 종료(수동 stop 또는 무음 워치독)될 때 서버가 추정치를 계산해 `broadcast_events.bytes_estimated` 에 남긴다 — LIVE 는 `방송 시간 × 24kbps(사양 고정) × 응답한 단말 수`, FILE 은 `파일 크기 × 응답한 단말 수`. 서버 재시작으로 고아가 된(§ 아래 참고) 방송은 시간을 신뢰할 수 없어 NULL 로 남긴다.

**진행 중 방송의 재시작 정리(A-8)**: `ended_at IS NULL` 인 행은 "방송 중" 취급이라 겹침 검사가 같은 대상에 새 방송을 막는다. 라이브 세션 상태(LiveRegistry)와 무음 워치독은 프로세스 메모리에만 있어서, 배포로 백엔드 컨테이너가 재생성되면 진행 중이던 방송이 고아로 남는다. 기동 시(`app/main.py` lifespan) `close_orphaned_events` 가 그 시점까지 `ended_at` 이 NULL 인 행을 전부 지금 시각으로 닫는다.

## 7. 대시보드

```
GET /api/dashboard/summary   요약 타일(온라인/오프라인/방송중/미배정 수) - 역할 범위 내 집계
GET /api/dashboard/map       지도용 좌표+상태 목록 (마을 경계 폴리곤 포함) (마을 경계 폴리곤 포함)
```

## 8. 설정 (CONFIG)

```
GET /api/config              현재 값 (current_config 테이블)
PUT /api/config              {status_interval_sec, live_stats_interval_sec, event_qos}
                              -> DB 갱신 + config_version 증가 + MQTT 재발행
                             {live_ready_timeout_sec, live_stop_wait_sec, file_wait_sec}
                             {live_bitrate_kbps, file_bitrate_kbps}
                              -> DB 갱신만 (서버 전용, 재발행 없음)
```

권한: super_admin만 (전체 단말에 영향을 주므로 마을관리자는 접근 불가).

**설정은 세 묶음이다 (2026-09-02·03 정리, 09-06 오디오 추가 — 문제점 7·8·9·10·29·30번, 단말 요청 §3.4)**: 화면도 세 구역으로 나뉜다.

- **단말 공통 CONFIG** — `status_interval_sec`·`live_stats_interval_sec`·`event_qos`. 저장하면 `config_version`이 올라가고 MQTT CONFIG가 재발행된다. 정본은 `constants.DEVICE_CONFIG_FIELDS`.
- **방송 응답 시간** — 아래 셋. CONFIG 토픽으로 나가지 않으므로 이 값만 바뀌면 `config_version`을 올리지 않고 재발행도 하지 않는다(올리면 전 단말이 내용상 같은 CONFIG를 다시 받고 적용 확인까지 오간다). 문구는 시작을 먼저, 종료를 뒤에 쓴다.

| 설정 | 기본 | 범위 | 단말에 전달 | 시작에서 | 종료에서 |
|---|---|---|---|---|---|
| `live_ready_timeout_sec` 라이브 준비 제한 | 30 | 1~60 (사양) | **예** — `LIVE_START.ready_timeout_sec` | 화면 준비 지연 알림 = 이 값 **+ 5** | — |
| `live_stop_wait_sec` 라이브 종료 대기 | 10 | 10~30 | 아니오 | — | 중지 후 `LIVE_RESULT` 대기 상한. 그 뒤 스트림 닫기 |
| `file_wait_sec` 파일방송 응답 대기 | **30** | **10~60** | 아니오 | 시작 후 `FILE_RESULT ok=true`(다 받고 무결성 검증 완료 → 재생 시작) 대기 상한 | 중지 후 종료 응답 대기 상한 |

- **오디오 품질** — 아래 둘. 표본율 16kHz · mono 는 통신 사양 고정이고 비트레이트만 고른다. CONFIG 토픽으로 나가지 않는다: opus 와 mp3 모두 파일 헤더에 비트레이트가 들어 있어 단말이 미리 알 필요가 없다.

| 설정 | 기본 | 선택지 | 적용 시점 |
|---|---|---|---|
| `live_bitrate_kbps` 라이브 스트림 속도 | 24 | 16 / 24 | 다음 방송부터. 브라우저 opus 인코더가 이 값으로 만든다 |
| `file_bitrate_kbps` MP3 파일 속도 | 24 | 16 / 24 | 이후 올리거나 합성하는 파일부터. 업로드 규격 검사(§5)와 TTS 합성에 함께 쓴다 |

**mp3 는 모든 프레임이 설정값이어야 한다 (2026-09-08, 문제점 30번)**: LAME 은 파일 맨 앞에 Xing/Info 헤더 프레임을 하나 넣는데, 16kHz·16kbps 프레임은 72바이트뿐이라 태그가 안 들어가서 **그 프레임만 40kbps 로 올려** 180바이트를 만든다. 오디오는 설정값이 맞지만 파일의 첫 프레임이 40kbps 라고 말하게 되고, 첫 프레임을 읽는 도구(탐색기 속성 등)는 파일 전체를 40kbps 로 보고한다 — 단말팀 실측이 이것이었다(24kbps 로 뽑던 때도 첫 프레임은 40이었다). TTS 합성과 업로드 재인코딩 모두 `-write_xing 0 -id3v2_version 0` 을 붙여 Xing 프레임과 빈 ID3v2 껍데기를 없앤다(`app/tts/engine.py` 의 `MP3_MUXER_ARGS`).

**만드는 방식을 고치면 `MP3_FORMAT_VERSION` 을 올린다 (2026-09-09)**: TTS 캐시 키에 이 값이 들어간다. 안 올리면 방식을 고쳐도 이미 만들어 둔 파일이 계속 나간다 — 같은 문구·언어·보이스·비트레이트면 키가 같아서다. Xing 수정이 현장에서 안 보이던 이유가 이것이었다. 버전을 올리면 옛 캐시가 자동으로 무효가 되고 다음 합성 때 새로 만든다(옛 파일은 파일함에 남으므로 사람이 지운다).

**합성 결과를 다시 잰다**: `normalize_mp3` 는 만든 파일을 `ffprobe` 로 재서 표본율·채널·비트레이트가 기대와 다르면 실패로 던진다. 그리고 ffmpeg 이 없거나 변환이 실패하면 **합성 엔진 출력을 그대로 쓰지 않고 던진다** — 예전에는 로그 한 줄만 남기고 규격 밖 파일을 파일함에 넣었다. 업로드는 규격 밖 파일을 거절하는데 TTS 만 통과시킬 이유가 없다. CBR 이라 길이는 크기 ÷ 비트레이트로 나오고, LAME tag 가 다듬어 주던 인코더 지연 약 0.1초 무음만 남는다.

값이 목록형이라 범위(`CONFIG_LIMITS`)가 아니라 `CONFIG_CHOICES` 로 막는다. 이미 파일함에 있는 파일은 다시 인코딩하지 않는다. TTS 캐시 키에는 비트레이트가 들어간다 — 안 넣으면 설정을 바꾼 뒤 같은 문구가 예전 비트레이트 파일로 나온다.

파일은 **설정 하나가 시작과 종료를 같이** 맡는다(문제점 8번). 기본·범위는 두 번 바뀌었다: 0011에서 "3MB 저장 40초" 전제로 120(30~180)으로 올렸는데, 단말 확인(2026-09-04, 문제점 19번)으로 `FILE_RESULT`가 **저장이 아니라 수신·검증 완료** 시점이고 저장은 방송 중 백그라운드라 크기와 무관함이 밝혀졌다(716KB 실측 3.6초). 0012에서 30(10~60)으로 되돌리고 범위 밖 값은 30으로 맞췄다. 화면 순서는 파일 → 라이브 준비 → 라이브 종료(문제점 12번, 방송 흐름 순서).

「라이브 시작 대기」는 설정으로 두지 않는다 — `live_ready_timeout_sec + 5`로 계산한다. +5는 단말 펌웨어 상수라 별도 설정으로 두면 둘이 어긋난다. 타임아웃은 상한이지 고정 대기가 아니다 — 단말이 모두 응답하면 그 자리에서 끝나므로 넉넉히 잡아도 정상 동작에서는 비용이 없다.

## 9. 자동방송 스케줄

```
GET    /api/schedules                         내 범위와 겹치는 스케줄 (editable · next_fire_at · last_run)
POST   /api/schedules                         전체 100개 상한
PATCH  /api/schedules/:id                     {enabled} 만 보내면 켜기·끄기
DELETE /api/schedules/:id
GET    /api/schedules/occurrences?from&to     예정 회차 — 서버가 규칙에서 계산 (최대 31일)
GET    /api/schedules/:id/runs                최근 실행 결과
```

**2026-09-09 구현** — [스케줄 설계](xWIFI_스케줄_설계_260909.md). 규칙 하나만 저장하고 실행 날짜는 계산한다(cron 과 같다). 반복은 매일·매주·매월·매년 중 하나, 시각은 하나(KST). 대상은 `village`·`device`·`organization`(관할 전체 — 실행 시점에 마을로 펼침). 실행기가 매분 규칙을 평가해 기존 `start_file_broadcast` 로 `FILE_START` 를 건다 — 단말 프로토콜은 그대로. 중복 실행 방지는 `schedule_runs UNIQUE(schedule_id, fire_at)`, 유예 120초를 넘긴 회차는 건너뛰고 기록만 남긴다(재시도 없음). 권한: 보기는 범위가 겹치면, 수정·삭제는 대상 전체가 범위 안일 때만. `schedules.created_by` 는 계정이 삭제되면 NULL 이 된다(0014).

## 10. OTA

```
POST /api/ota/start          {file_id(pkg), target_scope, target_ids[]} -> OTA_START 발행
GET  /api/ota/jobs            진행 중/완료된 OTA job 목록과 최신 상태(state: ACCEPTED/PREPARE/DOWNLOADING/VERIFYING/COMPLETED/FAIL)
```

2026-08-20부터 `OTA_APPLY`가 폐지되어 `/api/ota/apply` 엔드포인트는 없다 — 단말이 다운로드/검증(COMPLETED) 이후 서버 승인 없이 자동으로 적용+재부팅까지 진행한다. 최종 성공 여부는 재부팅 후 재접속된 단말의 STATUS 펌웨어 버전으로 판단(§단말 관리 API의 last_status 기준).

권한: super_admin만. OTA 진행 중인 단말은 방송 API에서 자동으로 거절되어야 함(단말 자체도 BUSY로 거절하지만, 프론트 UI에서도 버튼을 미리 막아주는 게 자연스러움).

## 11. 비용

```
GET /api/costs/summary   ?scope=all|village&village_id=&from=&to=
```

`daily_cost_summary` 테이블을 조회만 한다(요청마다 재계산하지 않음). `scope=village`면 마을별 추정 비용(estimated_*) 목록, `scope=all`이면 전체 실비용(actual_total_cost_krw) + 참고용 전체 추정치를 함께 반환.

권한: village_admin은 scope=village만 요청 가능하며 자기 담당 village_id로 자동 제한(다른 마을 조회 시 403). scope=all은 super_admin만 — AWS 실비용은 마을관리자에게 노출하지 않는다.

## 다음에 정할 것 (구현하면서)

- 정확한 요청/응답 JSON 필드명, 에러 코드 체계
- 페이지네이션 방식(offset/cursor)
- WebSocket 또는 폴링으로 대시보드 실시간 갱신할지 여부 — 지금은 안 정함, 화면 설계 때 같이 결정
- ~~ID 필드 통일(job_id로 통일하는 안)~~ — 2026-08-20 ESP32측(코덱스)과 확정 완료. MQTT 프로토콜은 job_id로 통일됨(§통신 사양 참고). REST API의 `job_id`(방송 정지/OTA 대상 지정용)는 이미 이 명칭을 쓰고 있어 추가 변경 없음 — 단, `/api/broadcast/file/start`의 `file_id`는 별개 개념(재생할 파일의 DB `files.id`)이므로 혼동 주의

## 참고

DB 스키마: `xWIFI_DB_스키마_260815.md`. MQTT payload 형식: `xWIFI_통신_사양_최종_260813.md`. 권한 모델: `xWIFI_운영서버_구성_개요_260813.md`의 "역할별 권한 분리" 절.
