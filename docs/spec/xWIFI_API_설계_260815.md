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

**STATUS 수신은 모아서 쓴다 (2026-09-02, A-2/A-3)**: 주기 STATUS 는 이력이 아니라 캐시 갱신이라(같은 단말은 최신값만 의미가 있다) MAC별 최신값만 메모리에 모았다가 `STATUS_FLUSH_INTERVAL_SEC`(기본 1초)마다 다중 행 upsert 한 번으로 쓴다. 자동 등록도 이 upsert가 그대로 처리한다. CONFIG 불일치 자동 복구(§8, 사양 §4.3) 판정도 같은 flush에서 묶여 돌아서, 전 단말이 30초마다 같은 `current_config` 행을 다시 읽던 조회가 flush당 한 번으로 줄었다. 결과·LWT는 종전대로 즉시 쓴다. 화면에 상태가 보이기까지 최대 1초 지연이 생기고, 값을 0으로 두면 예전 방식(메시지당 트랜잭션)으로 되돌아간다.

**단말별 MQTT 계정 (2026-08-30, `SERVER_DEVICE_CREDENTIAL_SPEC_2026-08-27.md`)**: username=콜론 없는 소문자 MAC, password=8자 랜덤(문자 집합 사양 §1, `@`·`!` 제외). 발행하면 DB에 평문 보관(등록 화면 표시·시리얼 투입용)하고, 백엔드가 mosquitto passwd 파일(해시)을 통째로 재생성해 공유 볼륨으로 내보낸다 → mosquitto entrypoint 감시 루프가 설치+SIGHUP 리로드. 응답 `{username, password, issued}` — 이미 발행된 단말은 기존 값 재사용(`issued:false`), `reissue:true`는 라인 재작업 전용. 등록(POST /api/devices)은 자동으로 계정을 함께 발행하고, 삭제는 계정도 함께 지운다(도난 단말 차단 수단). 계정 미발행 단말은 목록 응답의 `has_credential:false`로 구분되어 화면에 「미등록*」으로 표시되고, 공유 계정(이행기 `MQTT_DEVICE_PASSWORD`) 제거 후에는 방송 대상에서도 제외된다(레지스트리 사양 §3.6).

**마을별 ACL 자동 생성 (2026-08-31, 통신 사양 §2.1 "별도 규칙")**: `village/<village_id>/cmd`는 MAC이 아니라 서버 배정값이 들어가 `%u`로 못 잡으므로, 백엔드가 passwd와 같은 통로로 **aclfile도 생성**한다(`mqtt_accounts.render_acl` → 공유 볼륨 `aclfile.generated` → entrypoint 감시 루프가 `/mosquitto/data/aclfile`로 설치+SIGHUP). 단말마다 `user <mac>` 블록에 **배정된 마을 topic 한 줄만** 열리고 와일드카드(`village/+/cmd`)는 쓰지 않는다 — 계정 하나가 뽑혀도 그 마을 하나만 노출된다. 등록·삭제·마을 배정 변경·마을 삭제 때 passwd와 함께 재생성. 리포의 `infra/mosquitto/config/aclfile`은 첫 기동용 시드일 뿐이다. 공유 계정(xwifi-device)과 `%c` 규칙은 2026-08-31 폐기 완료.

**시리얼 주입 프레임 (2026-08-31 확정)**: 두 계정 API 응답에는 `server_host`(`PUBLIC_BASE_URL`에서 스킴·포트를 뗀 호스트)가 함께 실린다. 등록 화면은 이것으로 아래 한 프레임을 만들어 USB(Web Serial, 115200) 또는 복사·붙여넣기로 단말에 넣는다.

```text
@SERVER=hanna-aircast.co.kr\n@MQTTID=58e6c5f2cc74\n@MQTTPW=tA$UAcG2\n@END\n
```

(\n = 개행 `0x0A`. 위 한 줄이 실제로 나가는 바이트 전부다.)

개행은 LF(`0x0A`) 고정이고 **모든 명령 줄은 개행으로 끝난다 — `@END` 앞에도 개행이 필요하다** (2026-08-31 실물 단말 로그로 확정: 파서는 줄 단위로 먼저 자른 뒤 `@KEY=VALUE`를 읽는다). `@MQTTPW=<값>@END`처럼 한 줄에 붙이면 `@END`까지 비밀번호에 들어가 브로커 인증이 조용히 실패한다 — 신규 등록 첫 실물 테스트에서 실제로 발생했던 사고다. 구현은 `frontend/src/lib/serial.ts` 한 곳에 모아 두 화면이 갈라지지 않게 했다.

## 3. 마을 / 구역

```
GET    /api/villages            목록 (역할 범위)
POST   /api/villages            생성 {name, sido, sigungu, address_detail}
PATCH  /api/villages/:id
DELETE /api/villages/:id

GET    /api/villages/:id/zones
POST   /api/villages/:id/zones
PATCH  /api/zones/:id
DELETE /api/zones/:id
```

권한: 마을 생성/삭제는 super_admin만. village_admin은 자기 담당 마을 내 구역 관리까지만.

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

village_admin은 all과, **목록 중 하나라도** 담당 밖이면 403(일부만 나가는 방송은 의도한 결과가 아니므로 전체를 거절). 내부적으로 zone/village/all은 백엔드가 devices 테이블을 조회해서 MQTT는 대상 마을마다 iotradio/village/<id>/cmd 로, 또는 개별 device 토픽으로 발행한다(§통신 사양 그대로). **다중 마을이어도 job_id와 stream_url은 하나**다 — 여러 토픽에 같은 payload를 내보내 단말들이 같은 마운트로 모인다.

라이브 방송의 Icecast 마운트는 `/live/<job_id>`. 마을을 경로에 넣지 않는 이유는 다중 마을 방송을 경로로 표현할 수 없어서다("어느 마을인가"는 이력의 target_ids가 답한다). 단말은 LIVE_START.stream_url 문자열을 그대로 쓰므로 경로 구조는 서버 재량이다.

**무음 방송 자동 종료**: 라이브 시작 후 LIVE_UPLINK_GRACE_SEC(기본 30초) 안에 마이크 업링크(/ingest)가 한 번도 붙지 않으면 서버가 방송을 자동 종료한다. 화면에는 ON AIR로 보이는데 스피커는 조용한 상태가 프로덕션에서 가장 위험하기 때문이다. 붙었다 끊긴 경우는 재연결 여지가 있으므로 종료하지 않는다.

## 5. 파일 라이브러리

```
GET    /api/files
POST   /api/files              업로드(multipart), size/sha256 서버가 계산
POST   /api/files/tts          {text, lang, voice} -> Polly 호출 -> 파일 생성
DELETE /api/files/:id
GET    /api/files/:id/audio    미리듣기/다운로드
GET    /dl/:token              단말 전용 다운로드 — 로그인 없음, FILE_START 의 단기 토큰만. Range 지원
```

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
GET /api/dashboard/map       지도용 좌표+상태 목록
```

## 8. 설정 (CONFIG)

```
GET /api/config              현재 값 (current_config 테이블)
PUT /api/config              {status_interval_sec, live_stats_interval_sec, event_qos}
                              -> DB 갱신 + config_version 증가 + MQTT 재발행
```

권한: super_admin만 (전체 단말에 영향을 주므로 마을관리자는 접근 불가).

## 9. 자동방송 스케줄

```
GET    /api/schedules
POST   /api/schedules         최대 10개 제한은 여기서 체크
PATCH  /api/schedules/:id
DELETE /api/schedules/:id
```

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
