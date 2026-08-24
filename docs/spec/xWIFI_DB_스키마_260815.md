xWIFI 운영서버 DB 스키마 (v1)

80점짜리 1차 스키마다. 정규화나 인덱스 튜닝을 완벽하게 하려 하지 않고, 지금까지 정한 기능(단말 관리, 마을/구역, 역할별 권한, 방송 제어, 파일 라이브러리, 이력, 스케줄, OTA, CONFIG)을 돌리는 데 필요한 만큼만 잡았다. 나중에 실제로 부족해지면 그때 테이블을 쪼개거나 컬럼을 추가하면 된다 — 지금부터 완벽하게 만들려고 하지 않는다.

Postgres 기준. DDL 순서는 FK 의존성을 고려해 배치했다.

## 1. 마을 / 구역

```sql
CREATE TABLE villages (
    id              SERIAL PRIMARY KEY,   -- MQTT village_id는 앱에서 LPAD(id::text, 8, '0')로 변환해서 사용
    name            VARCHAR(100) NOT NULL,
    sido            VARCHAR(50),
    sigungu         VARCHAR(50),
    address_detail  VARCHAR(255),         -- 카카오 주소검색 결과 또는 수동 입력
    lat             DOUBLE PRECISION,
    lng             DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE zones (
    id              SERIAL PRIMARY KEY,
    village_id      INTEGER NOT NULL REFERENCES villages(id) ON DELETE CASCADE,
    name            VARCHAR(100) NOT NULL,   -- 예: 마을회관, OO공원
    address_detail  VARCHAR(255),
    lat             DOUBLE PRECISION,
    lng             DOUBLE PRECISION,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

village_id를 정수 PK로 두고 MQTT로 나갈 때만 8자리 문자열로 바꾸는 게 가장 단순하다. DB 안에 굳이 문자열 버전을 따로 저장/동기화하지 않는다.

## 2. 계정 / 권한

```sql
CREATE TABLE users (
    id              SERIAL PRIMARY KEY,
    username        VARCHAR(50) UNIQUE NOT NULL,
    password_hash   VARCHAR(255) NOT NULL,
    role            VARCHAR(20) NOT NULL CHECK (role IN ('super_admin', 'village_admin')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_villages (
    user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    village_id      INTEGER NOT NULL REFERENCES villages(id) ON DELETE CASCADE,
    PRIMARY KEY (user_id, village_id)
);
```

super_admin은 user_villages 안 봐도 전체 접근(코드에서 role 체크로 처리). village_admin은 담당 마을이 여러 개일 수 있어서 다대다로 뒀다.

## 3. 단말

```sql
CREATE TABLE devices (
    mac               VARCHAR(12) PRIMARY KEY,   -- 콜론 없는 소문자, 예: 58e6c5f2cc74
    label             VARCHAR(100),
    village_id        INTEGER REFERENCES villages(id) ON DELETE SET NULL,  -- NULL = 미배정
    zone_id           INTEGER REFERENCES zones(id) ON DELETE SET NULL,     -- 구역까지는 선택
    firmware_version  VARCHAR(50),
    last_status       JSONB,          -- 최근 STATUS payload 그대로 캐시 (대시보드용, 매번 이력 조회 안 하려고)
    last_seen_at      TIMESTAMPTZ,
    registered_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

zone_id는 nullable — 마을까지만 배정하고 구역은 나중에 정해도 됨. village_id도 nullable(미배정 단말이 STATUS로 먼저 잡히는 케이스 지원).

## 4. 파일 라이브러리

```sql
CREATE TABLE files (
    id            SERIAL PRIMARY KEY,
    filename      VARCHAR(255) NOT NULL,
    size_bytes    BIGINT NOT NULL,
    sha256        CHAR(64) NOT NULL,
    source        VARCHAR(20) NOT NULL DEFAULT 'upload' CHECK (source IN ('upload', 'tts')),
    tts_text      TEXT,           -- source='tts'일 때만 사용
    tts_lang      VARCHAR(10),
    uploaded_by   INTEGER REFERENCES users(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## 5. 자동방송 스케줄

```sql
CREATE TABLE schedules (
    id            SERIAL PRIMARY KEY,
    months        INTEGER[] NOT NULL,     -- 예: {1,2,3}
    weekdays      INTEGER[] NOT NULL,     -- 0=일 ~ 6=토
    times         TIME[] NOT NULL,        -- 예: {10:00, 16:00}
    file_id       INTEGER NOT NULL REFERENCES files(id),
    target_scope  VARCHAR(20) NOT NULL CHECK (target_scope IN ('device','zone','village','all')),
    target_ids    JSONB NOT NULL DEFAULT '[]',  -- scope에 맞는 id 목록(마을 여러 곳 가능), all이면 []
    enabled       BOOLEAN NOT NULL DEFAULT true,
    created_by    INTEGER REFERENCES users(id),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

최대 10개 제한은 DB 제약이 아니라 API 레벨에서 체크(단순하게).

## 6. 방송/이벤트 이력

```sql
CREATE TABLE broadcast_events (
    id            BIGSERIAL PRIMARY KEY,
    event_type    VARCHAR(20) NOT NULL,   -- LIVE_START/LIVE_STOP/FILE_START/FILE_STOP/OTA_START/CONFIG (OTA_APPLY는 2026-08-20 폐지)
    job_id        BIGINT,                 -- MQTT job_id 그대로 저장 (2026-08-20 session_id/cmd_id/job_id 통일 완료, §통신 사양 참고)
    target_scope  VARCHAR(20) NOT NULL CHECK (target_scope IN ('device','zone','village','all')),
    target_ids    JSONB NOT NULL DEFAULT '[]',
    file_id       INTEGER REFERENCES files(id),
    schedule_id   INTEGER REFERENCES schedules(id),   -- 스케줄에 의한 자동 실행이면 채움, 수동이면 NULL
    triggered_by  INTEGER REFERENCES users(id),         -- 수동이면 채움, 스케줄이면 NULL
    triggered_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE device_events (
    id            BIGSERIAL PRIMARY KEY,
    event_id      BIGINT REFERENCES broadcast_events(id) ON DELETE CASCADE,
    mac           VARCHAR(12) NOT NULL REFERENCES devices(mac),
    result_type   VARCHAR(20),    -- LIVE_READY/FILE_END/FILE_ABORT/FILE_STOP_RESULT/OTA_STATUS/STATUS 등
    payload       JSONB NOT NULL, -- 원본 MQTT payload 그대로 저장 (파싱은 필요할 때 조회 시점에)
    received_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

한 번의 방송 명령(broadcast_events 1행)이 여러 단말(device_events 여러 행)에 결과를 남기는 구조. payload를 JSONB로 원본 그대로 저장해서, 필드가 바뀌어도(예: OTA reason 코드 추가) 스키마 변경 없이 대응 가능 — 이 부분이 단순화 포인트.

## 7. 현재 CONFIG (싱글턴)

```sql
CREATE TABLE current_config (
    id                       INTEGER PRIMARY KEY DEFAULT 1 CHECK (id = 1),  -- 항상 한 행만
    config_version           INTEGER NOT NULL DEFAULT 1,
    status_interval_sec      INTEGER NOT NULL DEFAULT 30,
    live_stats_interval_sec  INTEGER NOT NULL DEFAULT 10,
    event_qos                SMALLINT NOT NULL DEFAULT 0,
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

이 값이 진짜 정본이고, MQTT retain은 캐시일 뿐이다(§운영서버 개요의 "브로커 데이터 유실 시 CONFIG 복구" 참고). 백엔드 기동/주기 작업이 이 테이블 값을 CONFIG로 재발행한다.

## 8. 비용 요약 (일별 배치 집계)

```sql
CREATE TABLE daily_cost_summary (
    id                      BIGSERIAL PRIMARY KEY,
    summary_date            DATE NOT NULL,
    village_id              INTEGER REFERENCES villages(id) ON DELETE CASCADE,  -- NULL = 전체(계정 단위) 행
    broadcast_minutes       NUMERIC(10,2) NOT NULL DEFAULT 0,   -- 그날 방송 누적 시간(분)
    device_broadcast_count  INTEGER NOT NULL DEFAULT 0,         -- 그날 방송에 참여한 단말 연인원
    estimated_egress_mb     NUMERIC(12,2),                      -- 사용량 기반 추정 데이터 전송량
    estimated_cost_krw      NUMERIC(10,2),                      -- 사용량 기반 추정 비용(원)
    actual_total_cost_krw   NUMERIC(10,2),                      -- village_id NULL 행에만: AWS Cost Explorer 실비용
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (summary_date, village_id)
);
```

`village_id`가 NULL인 행은 "전체" 집계(그날의 AWS 실비용 `actual_total_cost_krw` 포함, super_admin 전용). `village_id`가 있는 행은 마을별 추정치만 채우고(`estimated_*`) `actual_total_cost_krw`는 NULL — AWS가 마을 단위로 청구를 쪼개주지 않으므로 추정치 이상은 낼 수 없다.

매일 새벽 배치가 전날 `broadcast_events`/`device_events`를 마을별로 집계해서 이 테이블에 upsert하고, (전체 행은) AWS Cost Explorer API로 전날 실비용을 조회해 같이 저장한다. 화면/API는 원본 이력을 재계산하지 않고 이 요약 테이블만 조회한다(§운영서버 개요의 "비용 조회 기능" 참고).

## 지금 일부러 뺀 것 (나중에 필요해지면 추가)

- soft delete(삭제 플래그) — 지금은 그냥 하드 delete
- 감사 로그(누가 언제 뭘 바꿨는지 별도 테이블) — 지금은 각 테이블의 created_at/triggered_by 정도로 대체
- 인덱스 설계 — 일단 PK/FK 기본 인덱스만. 조회 느려지면 그때 추가
- devices.village_id와 zones.village_id 간 정합성(구역이 실제로 그 단말의 village와 같은 마을 소속인지) — 앱 레벨 검증, DB 제약으로는 안 걺
- 다국어 UI 텍스트, 파일 태그/카테고리 등 — 필요해지면 추가

## 참고

이 스키마는 §운영서버 개요(`xWIFI_운영서버_구성_개요_260813.md`)의 "위치 체계 및 ID 발급", "역할별 권한 분리", "MQTT 브로커 데이터 유실 시 CONFIG 복구" 절과 짝을 이룬다. API 설계(다음 단계) 시 각 엔드포인트가 여기 어느 테이블을 건드리는지 매핑하면서 진행한다.
