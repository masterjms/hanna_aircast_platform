# xWIFI 백엔드 API

기준일: 2026-09-06

구현: FastAPI

관련 문서: [아키텍처](01_시스템_아키텍처.md) · [단말 연동](02_단말_연동_사양.md) · [데이터 모델](04_데이터_모델.md)

이 문서는 현재 코드에 실제로 등록된 REST·WebSocket API를 기준으로 한다. 스케줄, OTA, 비용, 전체 이력 검색 API는 아직 없으며 별도 절에서 명확히 구분한다.

## 1. 공통 규칙

### 1.1 주소와 형식

- 운영 base URL: 동일 origin의 `https://<host>`
- 관리자 API prefix: `/api`
- health: `/health`
- 브라우저 마이크: `wss://<host>/ingest?session=<broadcast_id>`
- 단말 파일: `https://<host>/dl/<token>`
- JSON charset: UTF-8
- 시각: timezone이 포함된 ISO 8601 문자열
- 성공 삭제·로그아웃: `204 No Content`

목록 API는 현재 대부분 JSON 배열을 직접 반환한다. 코드에 `limit=50`, 최대 200의 공통 페이지 모델은 준비되어 있지만 현재 엔드포인트에 연결되어 있지 않다. 클라이언트는 배열 응답을 전제로 해야 한다.

### 1.2 인증

로그인 성공 시 HS256 JWT를 받는다.

```http
Authorization: Bearer <access_token>
```

JWT에는 `sub`, `username`, `role`, `iat`, `exp`가 들어간다. 운영 예시 만료는 240분, 코드 기본값은 720분이며 실제값은 환경 변수로 결정한다. 서버는 서명만 믿고 끝내지 않고 매 요청에서 `sub`의 사용자가 DB에 존재하는지 다시 확인한다.

로그아웃은 서버 상태를 만들지 않는 stateless 방식이다. `POST /api/auth/logout`은 204를 반환하고 브라우저가 토큰을 버린다. 즉시 강제 무효화나 blacklist는 현재 없다.

관리자 미리듣기 `<audio>`만 브라우저 제약 때문에 `?access_token=`도 허용한다. query 토큰은 URL·프록시 로그에 남을 수 있으므로 다른 API에는 사용하지 않는다.

### 1.3 역할과 범위

| 역할 | 범위 |
|---|---|
| `super_admin` | 모든 마을, 전체 방송, 미배정 단말, 시스템 설정·조직·계정 관리 |
| `village_admin` | `user_villages`에 지정된 마을의 조회·단말·방송 기능 |

권한은 두 단계다.

1. 전용 기능은 `super_admin` guard로 제한한다.
2. 일반 조회·제어는 `VillageScope`로 쿼리를 필터하고 요청 대상을 검사한다.

미배정 단말은 어떤 마을에도 속하지 않으므로 `super_admin`만 볼 수 있다. `all` 방송도 `super_admin`만 가능하다.

### 1.4 에러 응답

```json
{
  "error": {
    "code": "DEVICE_NOT_FOUND",
    "message": "등록되지 않은 단말입니다.",
    "detail": {"mac": "58e6c5f2cc74"}
  }
}
```

- 클라이언트 분기는 `message`가 아니라 안정적인 `code`를 사용한다.
- `detail`은 필요한 경우만 포함된다.
- Pydantic 검증 실패도 기본 FastAPI 형식 대신 `VALIDATION_FAILED`로 감싼다.

| HTTP | 주요 code |
|---:|---|
| 400 | `BAD_REQUEST`, `NO_TARGET_DEVICE`, `NO_ONLINE_TARGET`, `CONFIG_OUT_OF_RANGE`, `FILE_TOO_LARGE`, `FILE_TOO_LONG`, `UNSUPPORTED_FILE_TYPE`, `EMPTY_FILE`, `STREAM_URL_TOO_LONG`, `STREAM_URL_MUST_BE_HTTPS`, `ICECAST_UNAVAILABLE` |
| 401 | `UNAUTHORIZED`, `INVALID_CREDENTIALS` |
| 403 | `FORBIDDEN`, `SUPER_ADMIN_REQUIRED`, `VILLAGE_OUT_OF_SCOPE` |
| 404 | `DEVICE_NOT_FOUND`, `VILLAGE_NOT_FOUND`, `ZONE_NOT_FOUND`, `USER_NOT_FOUND`, `FILE_NOT_FOUND`, `BROADCAST_NOT_FOUND` |
| 409 | `DUPLICATE_USERNAME`, `DEVICE_ALREADY_EXISTS`, `BROADCAST_OVERLAP`, `FILE_IN_USE`, `BROADCAST_ALREADY_ENDED`, `BROADCAST_STOP_PENDING` |
| 422 | `VALIDATION_FAILED` |
| 500 | `MQTT_PAYLOAD_TOO_LARGE` |
| 503 | `MQTT_UNAVAILABLE`, `SERVICE_UNAVAILABLE` |

## 2. 인증 API

### 2.1 로그인

`POST /api/auth/login` — 인증 불필요

```json
{
  "username": "admin",
  "password": "example-password"
}
```

제약:

- username 1~50자
- password 1~128자 입력 허용; 계정 생성 비밀번호는 8~64자
- 아이디 없음과 비밀번호 오류는 모두 `INVALID_CREDENTIALS`로 응답
- nginx가 IP별 분당 10회, 순간 burst 5회로 제한

응답:

```json
{
  "access_token": "...",
  "token_type": "bearer",
  "expires_in": 14400,
  "user": {
    "id": 1,
    "username": "admin",
    "role": "super_admin",
    "villages": [{"id": 1, "name": "새뜰마을"}],
    "all_villages": true,
    "device_count": 12
  }
}
```

### 2.2 현재 사용자

`GET /api/auth/me` — 로그인

로그인 응답의 `user`와 같은 구조를 최신 DB 기준으로 반환한다. 상단 담당 범위와 권한 복구에 사용한다.

### 2.3 로그아웃

`POST /api/auth/logout` — 인증 불필요, `204`

서버 토큰 blacklist는 없다. 클라이언트가 저장 토큰과 사용자 상태를 삭제해야 한다.

## 3. 상태와 설정 API

### 3.1 health

`GET /health` — 인증 불필요

```json
{
  "status": "ok",
  "database": true,
  "mqtt": true
}
```

배포 스크립트와 모니터링이 사용한다. HTTP 응답만 아니라 DB와 MQTT 연결 상태를 함께 본다.

### 3.2 현재 설정

`GET /api/config` — `super_admin`

```json
{
  "config_version": 42,
  "status_interval_sec": 30,
  "live_stats_interval_sec": 10,
  "event_qos": 0,
  "live_ready_timeout_sec": 30,
  "live_stop_wait_sec": 10,
  "file_wait_sec": 30,
  "updated_at": "2026-09-06T00:00:00+00:00"
}
```

### 3.3 설정 변경

`PUT /api/config` — `super_admin`

부분 필드만 보내도 된다.

```json
{
  "status_interval_sec": 30,
  "event_qos": 1,
  "live_stop_wait_sec": 15
}
```

| 필드 | 범위 | 단말 CONFIG 포함 |
|---|---:|---:|
| `status_interval_sec` | 10~3600 | 예 |
| `live_stats_interval_sec` | 1~60 | 예 |
| `event_qos` | 0~1 | 예 |
| `live_ready_timeout_sec` | 1~60 | 아니오; LIVE_START에 포함 |
| `live_stop_wait_sec` | 10~30 | 아니오 |
| `file_wait_sec` | 10~60 | 아니오 |

단말 CONFIG 필드가 변경될 때만 `config_version`을 올리고 retained CONFIG를 재발행한다.

## 4. 마을·구역 API

### 4.1 엔드포인트

| Method | Path | 권한 | 설명 |
|---|---|---|---|
| GET | `/api/villages` | 로그인·범위 적용 | 접근 가능한 마을 목록 |
| GET | `/api/villages/{id}` | 로그인·범위 적용 | 마을 한 건 |
| POST | `/api/villages` | `super_admin` | 마을 생성 |
| PATCH | `/api/villages/{id}` | `super_admin` | 마을 부분 수정 |
| DELETE | `/api/villages/{id}` | `super_admin` | 마을 삭제, 단말은 미배정으로 유지 |
| GET | `/api/villages/{id}/zones` | 로그인·범위 적용 | 구역 목록 |
| POST | `/api/villages/{id}/zones` | `super_admin` | 구역 생성 |
| PATCH | `/api/zones/{id}` | `super_admin` | 구역 수정 |
| DELETE | `/api/zones/{id}` | `super_admin` | 구역 삭제, 단말의 zone은 NULL |

### 4.2 마을 생성·수정 body

```json
{
  "name": "새뜰마을",
  "sido": "경기도",
  "sigungu": "군포시",
  "address_detail": null,
  "b_code": "4141010100",
  "road_address": "경기도 군포시 ...",
  "jibun_address": "경기도 군포시 ...",
  "lat": 37.36,
  "lng": 126.93
}
```

- `name` 필수, 1~100자
- `b_code`는 선택 10자리 숫자
- 주소 검색 결과의 도로명·지번·법정동코드·좌표를 함께 저장한다.
- boundary는 생성 body가 아니라 PATCH 또는 경계 import script가 넣는다.
- `village_code`는 서버가 생성하며 클라이언트가 지정하지 않는다.

응답에는 `village_code`, 실제 MQTT에 쓰는 `village_token`, `has_boundary`, `device_count`, `online_count`가 추가된다.

마을의 token이 바뀌는 수정은 해당 단말 CONFIG와 Mosquitto ACL을 재동기화한다. 삭제하면 단말 행은 남고 마을·구역 배정이 해제되며 단말별 CONFIG에 미배정 토큰을 보낸다.

### 4.3 구역 body

```json
{
  "name": "서쪽 구역",
  "address_detail": "마을회관 서쪽",
  "lat": 37.36,
  "lng": 126.92
}
```

구역은 서버 내부 그룹이다. 단말 프로토콜에는 zone ID가 없으며 구역 방송은 서버가 소속 MAC 목록으로 펼친다.

## 5. 계정 API

모든 계정 API는 `super_admin` 전용이다.

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/users` | 계정 목록 |
| POST | `/api/users` | 계정 생성 |
| PATCH | `/api/users/{id}` | 비밀번호·역할·담당 마을 수정 |
| DELETE | `/api/users/{id}` | 계정 삭제 |

생성 body:

```json
{
  "username": "village.operator",
  "password": "at-least-8",
  "role": "village_admin",
  "village_ids": [1, 2]
}
```

- username: 3~50자, 영문·숫자·`.`·`_`·`-`
- password: 8~64자이며 bcrypt 72바이트 한계 내여야 함
- `village_ids`는 중복 제거·정렬
- `super_admin`에는 담당 마을 목록을 지정할 수 없음
- 자기 계정 삭제와 마지막 `super_admin` 삭제는 거부

PATCH에서 생략한 필드는 유지한다.

## 6. 단말 API

### 6.1 목록과 상세

| Method | Path | 권한 | 설명 |
|---|---|---|---|
| GET | `/api/devices` | 로그인·범위 적용 | 단말 목록·검색 |
| GET | `/api/devices/unassigned` | `super_admin` | 미배정 단말 목록 |
| GET | `/api/devices/{mac}` | 로그인·범위 적용 | 단말 상세와 원본 STATUS |

목록 query:

| 이름 | 값 |
|---|---|
| `village_id` | DB 마을 정수 ID |
| `zone_id` | DB 구역 정수 ID |
| `status` | `online`, `offline`, `unassigned` |
| `q` | label 또는 MAC 검색, 최대 100자 |

응답에는 저장 필드 외에 계산·파생 필드가 포함된다.

```json
{
  "mac": "58e6c5f2cc74",
  "label": "회관 스피커",
  "village_id": 1,
  "village_name": "새뜰마을",
  "zone_id": null,
  "zone_name": null,
  "p4_model": "P4",
  "p4_version": "V.260905-1",
  "c6_model": "C6",
  "c6_version": "V.260905-1",
  "last_seen_at": "2026-09-06T00:00:00+00:00",
  "online": true,
  "rssi": -54,
  "state": "IDLE",
  "live": "OFF",
  "has_credential": true,
  "config_version": 42,
  "ip": "192.168.0.21"
}
```

비밀번호 자체는 일반 응답에 포함하지 않는다.

### 6.2 등록

`POST /api/devices` — 로그인·범위 적용, `201`

```json
{
  "mac": "58:E6:C5:F2:CC:74",
  "label": "회관 스피커",
  "village_id": 1,
  "zone_id": null,
  "p4_model": "P4",
  "p4_version": "V.260905-1",
  "c6_model": "C6",
  "c6_version": "V.260905-1",
  "mqtt_password": "aB3#kP9_"
}
```

- MAC은 서버가 정규형으로 변환한다.
- `village_admin`은 담당 마을에만 등록할 수 있고 미배정 등록은 할 수 없다.
- zone이 있으면 같은 village에 속해야 한다.
- `mqtt_password`를 생략하면 서버가 생성한다.
- 등록 후 DB 전체를 기준으로 passwd와 ACL을 다시 내보내고 단말 CONFIG를 발행한다.

### 6.3 수정·삭제

| Method | Path | 설명 |
|---|---|---|
| PATCH | `/api/devices/{mac}` | label, 마을·구역, 주소·좌표 부분 수정 |
| DELETE | `/api/devices/{mac}` | 단말 삭제, credential·ACL·retained CONFIG 정리 |

PATCH에서는 필드 생략과 명시적 `null`이 다르다. 생략하면 유지, `village_id: null`이면 배정 해제다. 마을 배정이 바뀌면 CONFIG와 ACL을 다시 배포한다.

설치 위치 필드는 `road_address`, `jibun_address`, `address_detail`, `lat`, `lng`다. 좌표가 없으면 지도 API가 zone, village 순으로 fallback한다.

### 6.4 credential

| Method | Path | 권한 | 설명 |
|---|---|---|---|
| POST | `/api/devices/credential` | `super_admin` | MAC을 알기 전 신규 등록용 password 값 생성 |
| POST | `/api/devices/{mac}/credential` | `super_admin` | 등록 단말 계정 조회·발급·재발급 |

신규 값 응답:

```json
{"password":"aB3#kP9_","server_host":"example.invalid"}
```

등록 단말 요청·응답:

```json
{"reissue":false}
```

```json
{
  "username":"58e6c5f2cc74",
  "password":"aB3#kP9_",
  "server_host":"example.invalid",
  "issued":false
}
```

기본은 기존 값을 재사용한다. `reissue=true`는 생산 라인 재작업에만 사용한다. 현장 단말 비밀번호를 서버에서만 바꾸면 단말이 다시 접속할 수 없게 된다.

## 7. 주소·지도·대시보드 API

### 7.1 주소 검색

`GET /api/geo/address?q=<검색어>` — 로그인

- 검색어 2~100자
- Kakao REST key를 브라우저에 노출하지 않도록 서버가 프록시
- 도로명 주소, 지번 주소, 법정동코드, 위도·경도를 한 번에 반환
- 반환값을 선택한 뒤 DB에 저장해야 하며 Kakao가 정본은 아님

### 7.2 대시보드 요약

`GET /api/dashboard/summary` — 로그인·범위 적용

```json
{
  "scope": {"all_villages": false, "village_ids": [1]},
  "devices": {"total": 10, "online": 8, "offline": 2, "unassigned": 0},
  "alerts": [],
  "active_broadcasts": [],
  "recent_events": []
}
```

- 이상 단말 최대 20개
- 최근 방송 이벤트 최대 10개
- 진행 중 방송 포함
- `village_admin`의 recent event 필터는 현재 `target_scope=village`이고 자신의 DB 마을 ID가 `target_ids`에 직접 포함된 이벤트를 기준으로 한다. 전체 방송이나 zone/device로 펼친 참여 이력을 완전히 포착하지 못하므로 전용 이력 API 구현 때 `device_events` 기반으로 보완해야 한다.

### 7.3 지도

`GET /api/dashboard/map` — 로그인·범위 적용

응답은 `kakao_js_key`, `villages`, `pins`, `missing_location`을 포함한다.

- 단말 좌표 선택: device → zone → village
- `position_source`: `device`, `zone`, `village`
- marker: `normal`, `offline`, `unassigned`
- village에는 GeoJSON `boundary`가 포함될 수 있음
- 어떤 계층에도 좌표가 없으면 pin 대신 `missing_location`에 MAC을 넣음

## 8. 파일·TTS API

파일함은 고객 스택 내부 공용이며 마을별로 분리하지 않는다.

| Method | Path | 권한 | 설명 |
|---|---|---|---|
| GET | `/api/files` | 로그인 | 파일 목록 |
| POST | `/api/files` | 로그인 | multipart MP3 업로드 |
| DELETE | `/api/files/{id}` | 로그인 | 미사용 파일 삭제 |
| GET | `/api/files/{id}/audio` | 로그인 또는 query token | 관리자 미리듣기 |
| GET | `/api/tts/voices` | 로그인 | 언어·voice 목록 |
| POST | `/api/files/tts` | 로그인 | 합성 후 파일함 등록 |
| GET | `/dl/{token}` | 단말 token | 단말 MP3 다운로드 |

업로드는 MP3만 허용하며 50 MiB 상한을 적용한다. 2.5 MiB와 600초 상한은 방송 시작 시 검사한다. 서버가 SHA-256과 duration을 계산하고 실제 파일은 `FILE_ROOT/upload`, TTS는 `FILE_ROOT/tts` 아래에 둔다.

TTS 요청:

```json
{
  "text": "오늘 오후 두 시에 마을 회의가 있습니다.",
  "language": "ko-KR",
  "voice": "ko-KR-Neural2-A",
  "filename": "마을 회의 안내"
}
```

- text 1~1,000자
- language 기본 `ko-KR`
- voice 생략 시 언어 기본 voice
- 동일한 `(text, language, voice)`가 있으면 외부 합성을 다시 호출하지 않고 `cached=true`
- 합성은 파일함 등록까지만 수행하며 자동 방송하지 않는다.

`/dl/<token>`은 로그인 없이 동작하고, 만료·오류를 모두 404로 숨긴다. 운영에서는 백엔드가 토큰과 경로를 검증한 뒤 `X-Accel-Redirect`를 반환하고 nginx가 Range를 포함한 파일 bytes를 보낸다.

## 9. 방송 API

### 9.1 공통 대상

`target_scope`와 `target_ids` 조합:

| scope | IDs | 비고 |
|---|---|---|
| `device` | MAC 문자열 목록 | 담당 마을 단말만 |
| `zone` | DB zone ID를 문자열로 | 소속 온라인 단말로 확장 |
| `village` | DB village ID를 문자열로 | 여러 마을 가능 |
| `all` | `[]` | `super_admin` 전용 |

`target_ids` 최대 200개다. 실제 대상은 시작 시 온라인이고 credential이 있는 단말이다. 한 대도 없으면 `NO_ONLINE_TARGET`이다. 진행 중 방송과 단말이 겹치면 `BROADCAST_OVERLAP`이며 자동으로 기존 방송을 중지하지 않는다.

### 9.2 파일 방송

`POST /api/broadcast/file/start` — 로그인·범위 적용

```json
{
  "file_id": 12,
  "target_scope": "village",
  "target_ids": ["1", "2"],
  "store_flash": false,
  "autoplay": true
}
```

현재 API 기본은 `store_flash=false`, `autoplay=true`다.

`POST /api/broadcast/file/stop`

```json
{"broadcast_id":901}
```

중지 호출은 `broadcast_events.id`를 받는다. `job_id`를 API 입력으로 받지 않는다.

### 9.3 실시간 방송

`POST /api/broadcast/live/start` — 로그인·범위 적용

```json
{
  "target_scope": "village",
  "target_ids": ["1"],
  "record_flash": true
}
```

현재 API 기본은 `record_flash=true`다. 10분을 넘길 방송은 false로 선택해야 한다.

응답의 `stream_url`은 단말용, `ingest_path`는 브라우저용이다. WSS 업링크가 기본 30초 안에 한 번도 붙지 않으면 서버가 방송을 자동 종료한다.

`POST /api/broadcast/live/stop`

```json
{"broadcast_id":902}
```

서버는 STOP 발행 후 기본 10초 동안 결과를 기다리고 source를 닫는다.

### 9.4 조회

| Method | Path | 설명 |
|---|---|---|
| GET | `/api/broadcast/active` | 접근 범위의 진행 방송 |
| GET | `/api/broadcast/{event_id}` | 한 방송과 단말별 결과 |

현재 `village_admin`의 방송 조회·중지 가시성은 `target_scope=village`이고 `target_ids`에 담당 마을이 직접 들어 있는 경우만 허용한다. 따라서 담당 마을의 `device` 또는 `zone` 대상으로 방송을 시작할 수는 있지만, 시작 후 active 목록·상세·stop에서는 해당 방송이 보이지 않는 구현 제한이 있다. 대상 MAC의 소속 마을로 판정하도록 백엔드를 보완하기 전까지 운영에서는 village 단위 방송을 사용하거나 `super_admin`이 중지를 담당해야 한다.

방송 응답 주요 구조:

```json
{
  "id": 902,
  "job_id": 101,
  "event_type": "LIVE_START",
  "target_scope": "village",
  "target_ids": ["1"],
  "file_id": null,
  "file_name": null,
  "triggered_at": "2026-09-06T00:00:00+00:00",
  "ended_at": null,
  "stop_requested_at": null,
  "expected_count": 8,
  "playing_since": null,
  "phase": "송출 중",
  "bytes_estimated": null,
  "target_count": 8,
  "results": [
    {
      "mac": "58e6c5f2cc74",
      "label": "회관 스피커",
      "result_type": "LIVE_READY",
      "ok": true,
      "reason": "OK",
      "live": "PLAYING",
      "stats": "...",
      "received_at": "2026-09-06T00:00:03+00:00"
    }
  ],
  "stream_url": "https://example.invalid/live/101",
  "ingest_path": "/ingest?session=902",
  "uplink_connected": true
}
```

`phase`는 UI 표시용 서버 계산값이다. FILE은 전송 중·재생 중·중지 중·종료, LIVE는 준비 중·송출 중·중지 중·종료를 구분한다.

방송 종료 시 `bytes_estimated`를 계산한다.

- FILE: 파일 크기 × 결과를 남긴 고유 단말 수
- LIVE: 방송 시간 × 3,000 bytes/sec × 결과를 남긴 고유 단말 수

단말이 실제 byte 수를 보고하지 않아 결과를 한 번이라도 남긴 단말을 수신자로 간주한 비용·트래픽 참고값이다. 명령은 받았지만 결과를 보내지 못한 단말의 전송량은 빠질 수 있다.

## 10. WebSocket 마이크 업링크

연결:

```text
wss://<host>/ingest?session=<broadcast_events.id>
```

연결 후 10초 안에 첫 텍스트 frame으로 인증한다.

```json
{"type":"auth","token":"<JWT>"}
```

성공 응답:

```json
{"type":"ready","mount":"/live/101"}
```

그 뒤 브라우저는 Ogg/Opus binary chunk를 보낸다. 한 chunk 상한은 64 KiB이며 초과 chunk는 무시한다. 텍스트 frame은 heartbeat 용도로 무시한다. 같은 세션에 업링크 두 개를 허용하지 않는다.

| close code | 의미 |
|---:|---|
| 4001 | 인증 실패 |
| 4004 | 세션 없음 또는 이미 종료 |
| 4009 | 다른 업링크가 이미 연결됨 |

업링크가 끊겨도 세션은 즉시 종료되지 않고 화면에 `uplink_connected=false`가 표시된다. 마지막 오디오 수신 후 `LIVE_UPLINK_GRACE_SEC` 기본 30초 동안 오디오가 없으면 grace watchdog이 방송을 종료한다. 한 번도 연결되지 않은 경우도 같은 기준이다.

## 11. 현재 없는 API

다음 path를 클라이언트에서 호출하면 안 된다.

| 영역 | 현재 상태 | 구현 시 필요한 최소 범위 |
|---|---|---|
| 전체 이력 | 전용 router 없음 | 검색·기간·종류·대상·성공 필터, 페이지네이션, 단말 결과 상세 |
| 스케줄 | DB 모델만 있음 | CRUD, 활성화, 실행 결과, 중복 방지 |
| OTA | protocol 상수와 저장 디렉터리만 있음 | package·서명 관리, 작업 시작·조회, 재부팅 후 버전 판정 |
| 비용 | DB 모델과 외부 Slack script만 있음 | 일별 집계, Cost Explorer 결합, 조회 API |

새 API를 추가할 때는 현재 에러 envelope, JWT·scope 의존성, `target_ids` 규칙, 방송 service의 겹침 검사를 재사용한다.
