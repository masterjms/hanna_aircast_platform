# xWIFI 운영서버

ESP32(P4+C6) 마을방송 단말을 **MQTT + Icecast + HTTP** 로 제어하는 운영서버.
백엔드·프론트·인프라를 한 리포지토리에 둔다.

사양 문서는 `작업지시서/spec/xWIFI_운영서버_사양통합_260819.md` 를 따른다.

---

## 지금 구현된 범위

| Phase | 내용 | 상태 |
|---|---|---|
| 0 | 리포지토리 · Docker Compose 골격 · mock 단말 스크립트 | 완료 |
| 1 | DDL + Alembic · 인증 · 권한 의존성 · MQTT 워커 · CONFIG 재조정 · `/health` | 완료 |
| 2 | 조회계 API · 프론트 전체(로그인 · 대시보드 · 단말 · 마을 · 계정 · 설정) | 완료 |
| 3 | 파일 업로드 · `/dl` 토큰 서빙 · `FILE_START` 발행 · 겹침 검사 409 · 파일함 · 방송 제어 | 완료 |
| 4 | WSS `/ingest` · Icecast 세션별 마운트 · `LIVE_START` 발행 · 마이크 업링크 | 완료 |
| 5 | AWS Polly TTS · 캐시 · 파일함 통합 | 완료 |
| 6~8 | 스케줄 · OTA · 비용 | 미착수 |

프론트 사이드바에서 `준비` 로 표시된 메뉴가 아직 구현 전이다.

### TTS 흐름 (Phase 5)

```
[브라우저] --POST /api/files/tts {text, language, voice}--> [백엔드]
                                        │ 캐시 확인 sha256(text|lang|voice)
                                        │ 없으면 Polly 호출 → mp3 → 정규화
                                        │ 디스크 저장 + files 행 생성
                          ◄─────────────┘
[브라우저] <audio src=/api/files/<id>/audio>   미리듣기
[관리자]   방송 제어에서 이 파일을 골라 송출  → 기존 FILE_START 경로
```

**합성과 방송을 나눈다.** 합성이 곧바로 방송으로 이어지면 오타가 마을 스피커
300대로 그대로 나간다. 파일함에 먼저 넣고 들어본 뒤, 방송 제어에서 고른다.
같은 이유로 만든 문구를 재방송하거나 스케줄에 걸 수 있고, 겹침 검사·권한·이력이
붙어 있는 방송 경로를 그대로 재사용한다.

브라우저가 Polly 를 직접 부르지 않는다 — 자격증명이 노출되고, 미리듣기와 송출본이
별개 합성이 되어 요금이 두 배 나가면서 내용까지 달라질 수 있다.

### 실시간 방송 흐름 (Phase 4)

```
[관리자] 방송 제어 화면에서 대상 선택 → 실시간 방송 시작
   → 서버: 겹침 검사 → session_id 발번 → Icecast source 연결(HTTP PUT)
   → MQTT: LIVE_START { session_id, stream_url, codec, frame_ms, sample_rate }
   → 브라우저: opus-recorder(WASM) → WSS /ingest?session=<id>
   → 서버: 받은 Ogg 페이지를 그대로 Icecast 로 흘려보냄 (파싱·재인코딩 없음)
   → 단말: GET http://<host>/live/00000001/43 → LIVE_READY status=0
```

**마운트는 세션마다 갈라진다.**

```
http://<서버>/live/00000001/43
                  ↑        ↑
              마을 8자리   세션 id
```

마운트가 하나뿐이면 나중 방송이 앞 방송을 덮어써서 마을 동시 방송이 안 된다.
전체(all) 대상 방송은 마을을 정할 수 없어 `/live/all/<세션id>` 를 쓴다 —
마을 토큰은 항상 8자리 숫자라 `all` 과 겹치지 않는다.

### 파일 방송 흐름 (Phase 3)

3채널이 전부 맞물려 돌아가는 것을 목 단말로 확인했다:

```
[관리자] 방송 제어 화면에서 대상 + 파일 선택
   → 서버: 대상 해석 → 겹침 검사(409) → job_id 발번 → 단기 토큰 발급
   → MQTT: iotradio/village/<id8>/cmd 로 FILE_START (280B / 1024B 한계)
   → 단말: https_url(/dl/<token>) 로 HTTP GET → sha256 검증
   → MQTT: iotradio/device/<mac>/result 로 FILE_END(verify_ok=1)
   → 서버: device_events 적재 → 화면에 완료 1/1 표시
```

---

## 디렉터리

```
xwifi-server/
├── backend/                 FastAPI (단일 프로세스: REST + MQTT 워커 + 스케줄러)
│   ├── run.py               개발 서버 진입점 (Windows 이벤트 루프 때문에 필요)
│   ├── app/
│   │   ├── main.py          앱 조립만 — 비즈니스 로직 없음
│   │   ├── config.py        환경 의존성의 유일한 입구
│   │   ├── db.py            엔진 · 세션
│   │   ├── constants.py     단말 프로토콜과 공유하는 상수
│   │   ├── errors.py        API 에러 규약 (code/message/detail)
│   │   ├── core/            scope · security · deps · ids
│   │   ├── models/          SQLAlchemy — 테이블 모양만
│   │   ├── schemas/         Pydantic — 요청/응답
│   │   ├── modules/         도메인별 router + service
│   │   │   ├── auth/  org/  device/  system/
│   │   │   ├── file/        업로드 · 다운로드 토큰 · /dl 서빙
│   │   │   ├── broadcast/   대상 해석 · 겹침 검사 · FILE/LIVE START·STOP
│   │   │   └── dashboard/   읽기 전용 집계(read model)
│   │   ├── live/            mount · icecast(source) · registry · ingest(WSS)
│   │   ├── tts/             engine(polly/dev) · voices · service(캐시)
│   │   ├── mqtt/            topics · connection · publisher · handlers
│   │   └── tasks/           config_reconcile
│   ├── alembic/versions/    0001_initial_schema.py
│   └── tests/
├── frontend/                React 18 + TypeScript + Vite
│   └── src/{api,auth,components,pages,hooks,styles}/
├── infra/                   mosquitto · icecast · nginx 설정
├── scripts/                 seed.py · mock_device.py
├── docker-compose.yml       운영 스택
└── docker-compose.dev.yml   로컬 개발 (Postgres + Mosquitto 만)
```

### 모듈 경계 규칙

- 다른 모듈의 테이블을 직접 조회하지 않는다. `service.py` 함수로 호출한다.
  (예외: `modules/dashboard` 는 읽기 전용 집계라 가로질러 읽는다. 쓰기는 절대 안 한다.)
- **MQTT 발행은 `app/mqtt/publisher.py` 한 곳만 거친다.** payload 크기(1024B)·QoS/retain·
  권한 재확인이 전부 거기 걸려 있다.
- ID 발번은 DB 시퀀스(`job_id_seq`)를 쓴다. 프로세스 메모리 카운터를 쓰지 않는다.
- MQTT 워커·스케줄러 코드를 REST 핸들러에 섞지 않는다.

---

## 로컬 실행

### 빠른 방법 — `dev.ps1`

PowerShell 에서 리포 루트에 대고 실행한다.

```powershell
.\dev.ps1 setup    # 최초 1회 (venv · 의존성 · 마이그레이션 · 시드)
.\dev.ps1 up       # 전부 실행
.\dev.ps1 status   # 무엇이 떠 있는지
.\dev.ps1 down     # 전부 종료
```

`up` 을 하면 백엔드 · 프론트 · 목단말이 각각 **별도 창**으로 뜬다. 로그를
따로 보고 Ctrl+C 로 개별 종료하기 위해서다. 옵션은 `.\dev.ps1 help`.

VSCode 를 쓰면 `Ctrl+Shift+B` 로 같은 걸 실행할 수 있다(`.vscode/tasks.json`).
이쪽은 새 창 대신 VSCode 터미널 탭으로 뜬다.

> **`up` 이 "다른 Postgres 로 간다" 고 하면** — WSL 에 네이티브
> postgres/mosquitto 가 깔려 있어 같은 포트를 먼저 잡은 것이다. 둘 다 DB 이름과
> 비밀번호가 같아서 오류 없이 조용히 엉뚱한 DB 에 붙고, 데이터가 반쪽씩 갈린다.
> `wsl -e bash -lc "sudo systemctl disable --now postgresql mosquitto"` 로 해제한다.

아래는 `dev.ps1` 이 실제로 하는 일이다. 직접 단계별로 돌리고 싶을 때 참고.

### 1. 인프라

```bash
cp .env.example .env
docker compose -f docker-compose.dev.yml up -d
```

### 2. 백엔드

```bash
cd backend
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python ../scripts/seed.py
.venv/Scripts/python run.py
```

API 문서: <http://localhost:8080/docs> · 상태: <http://localhost:8080/health>

### 3. 프론트

```bash
cd frontend
npm install
npm run dev
```

<http://localhost:5173> — `/api` 는 Vite 프록시가 백엔드로 넘긴다.

### 포트 배치

| 포트 | 무엇 | 왜 이 번호인가 |
|---|---|---|
| 8080 | 백엔드 API | Icecast 에 8000 을 내주고 비켰다 |
| 8000 | Icecast | **단말 펌웨어가 `<ip>:8000/live` 로 고정 접속**한다. 문서(`docs/Icecast_구성작업_지시서_최종.md`)도 8000 기준 |
| 1883 | MQTT | 통신 사양 §3.1 |
| 5173 | 프론트 dev | Vite 기본값 |

### 실물 단말 테스트

목 단말이 아니라 실제 ESP32 를 붙일 때는 세 가지가 더 필요하다.

**1. 단말이 이 PC 를 찾을 수 있어야 한다.** 단말은 `localhost` 가 아니라 이 PC 의
LAN IP 로 접속한다. 단말과 PC 가 같은 네트워크(같은 공유기)에 있어야 한다.

```powershell
ipconfig | Select-String IPv4     # 이 IP 를 단말에 설정
```

**2. 방화벽을 열어야 한다.** 관리자 권한 PowerShell:

```powershell
netsh advfirewall firewall add rule name="xWIFI MQTT 1883" protocol=TCP dir=in localport=1883 action=allow profile=private
netsh advfirewall firewall add rule name="xWIFI Icecast 8000" protocol=TCP dir=in localport=8000 action=allow profile=private
netsh advfirewall firewall add rule name="xWIFI API 8080" protocol=TCP dir=in localport=8080 action=allow profile=private
```

> ⚠ `profile=private` 로 제한한다. 개발용 브로커는 **익명 접속을 허용**하므로
> (`infra/mosquitto/config/mosquitto.dev.conf`), 공용 네트워크에 열면 아무나
> 방송 명령을 보낼 수 있다. 네트워크 프로필이 "공용"이면 먼저 "개인"으로 바꾼다.

**3. `.env` 의 `PUBLIC_BASE_URL` 을 LAN IP 로 바꾼다.** FILE_START 의 다운로드
URL 을 만드는 값이라 `localhost` 면 단말이 자기 자신에게 받으러 간다.

```
PUBLIC_BASE_URL=http://192.168.0.5:8080
ICECAST_PUBLIC_BASE_URL=http://192.168.0.5:8000
```

그다음 아래 감시 도구를 띄우고 단말 전원을 넣는다.

### MQTT 감시 · 사양 검증

```bash
cd backend
.venv/Scripts/python ../scripts/mqtt_monitor.py
```

브로커에 흐르는 모든 메시지를 보여주고, 단말이 보낸 것을 통신 사양과 대조한다.
`--seconds 30` 으로 시간을 제한할 수 있고, Ctrl+C 로 끝내면 요약이 나온다.

잡아내는 것:

- `state` 가 IDLE/LIVE/FILE/RF/OTA/OFFLINE 밖의 값인지
- OTA state 가 6종(ACCEPTED/PREPARE/DOWNLOADING/VERIFYING/COMPLETED/FAIL) 밖인지
- **옛 ID 필드(`session_id`/`cmd_id`/`file_id`)를 쓰는지** — 보이면 단말 펌웨어가
  job_id 통일(2026-08-20) 이전 버전이다
- `village_id` 가 8자리 숫자인지, payload 의 `device` 가 토픽 MAC 과 맞는지
- `config_version` 이 서버가 보낸 값으로 바뀌었는지 (CONFIG 가 실제로 먹었는지)

### 4. 목 단말 (실물 없이 테스트)

```bash
cd backend
.venv/Scripts/python ../scripts/mock_device.py --count 5
```

실제 단말과 같은 흐름으로 동작한다:

- 미등록 MAC 자동 등록 → CONFIG(retain) 수신 → `config_version` STATUS echo
- 마을 배정을 받으면 `iotradio/village/<id8>/cmd` 를 **구독**하고, 재배정되면 이전 마을 토픽을 해제한다
- `FILE_START` 를 받으면 `https_url` 에서 실제로 파일을 받아 sha256 을 검증하고 `FILE_END` 를 보낸다
- `FILE_STOP` 에는 `FILE_ABORT(USER_CANCEL)` 또는 `FILE_STOP_RESULT(NOT_ACTIVE)` 로 답한다
- `LIVE_START` 를 받으면 `stream_url` 로 붙어 첫 바이트를 확인하고 `LIVE_READY status=0` 을 보낸다
- 파일 처리 중에 `LIVE_START` 가 오면 `LIVE_READY status=3` 으로 거절한다(채널 배타)
- Ctrl+C 로 끊으면 브로커가 LWT 를 대신 발행한다

### 시드 계정

| 아이디 | 비밀번호 | 역할 |
|---|---|---|
| `admin` | `admin1234!` | super_admin (전체) |
| `sindong` | `village1234!` | village_admin (신동마을만) |

두 계정으로 번갈아 로그인하면 권한 범위 분리가 실제로 동작하는지 확인할 수 있다.

---

## 테스트

```bash
cd backend && .venv/Scripts/python -m pytest tests -q
```

권한 범위 판정 · MAC/토픽 정규화 · payload 크기 한계 · 인증을 덮는다.
전부 DB·브로커 없이 돈다.

DB·브로커가 필요한 통합 검증은 별도 스크립트로 돌린다(백엔드가 떠 있어야 한다).
두 스크립트 모두 시작할 때 이전 실행이 남긴 상태(단말 배정 · 진행 중 방송)를
스스로 정리하므로 몇 번을 돌려도 결과가 같다.

---

## 알아둘 것

### 권한

권한은 **백엔드가 강제**한다. 프론트의 메뉴 숨김은 UX 편의일 뿐이다.

- `Scope` 의존성 → `VillageScope` 값 객체. `all_villages` 여부가 타입 안에 박혀 있어서
  "None 이 전체인지 없음인지" 헷갈릴 여지가 없다.
- 조회는 `scope.apply(stmt, column)`, 제어는 `scope.ensure_allowed(village_id)`.
- 미배정 단말(`village_id IS NULL`)은 super_admin 만 다룰 수 있다.

### CONFIG 는 DB 가 정본

브로커의 retain 은 캐시다. 브로커가 재시작되면 유실되므로 기동 시 1회 + 1시간 주기로
`current_config` 를 다시 발행한다(`app/tasks/config_reconcile.py`).

마을 배정은 `iotradio/device/<mac>/config` 로 단말별로 나간다. 공통 CONFIG 에 넣으면
전 단말이 같은 마을이 되어 여러 마을 운영이 불가능하다.

### 파일 저장

원본은 `FILE_ROOT` 아래 로컬 디스크에 둔다. `files.storage_path` 는 **상대 경로**다 —
절대 경로를 넣으면 온프레미스로 옮길 때 전부 깨진다.

단말에게는 서명 URL 대신 짧은 토큰(`/dl/<token>`)을 내려보낸다. MQTT CMD payload 가
1024바이트를 넘을 수 없어서다. 토큰은 기본 10분 뒤 만료된다(`DOWNLOAD_TOKEN_TTL_SEC`).

재생 시간 계산에 `ffprobe` 를 쓴다. 없으면 `duration_sec` 을 NULL 로 두고 업로드는
그대로 성공시킨다 — 운영 컨테이너에는 ffmpeg 이 들어 있다.

### TTS

캐시 키는 `sha256(문구|언어|보이스)` 다. 같은 문구를 다시 만들면 Polly 를 부르지
않고 기존 파일을 돌려주므로, 미리듣기를 몇 번 눌러도 요금은 한 번만 나간다.
합성하면 그 즉시 파일함에 저장된다 — 별도의 '저장' 단계가 없다.

엔진은 `TTS_ENGINE` 으로 바꾼다.

| 값 | 용도 |
|---|---|
| `polly` | 실제 합성. 자격증명은 boto3 기본 체인(환경변수 → `~/.aws/credentials` → EC2 인스턴스 역할). 운영에서는 **인스턴스 역할**을 쓰는 게 가장 안전하다 — 키를 서버에 두지 않는다. |
| `dev` | ffmpeg 으로 톤을 만드는 가짜 엔진. AWS 없이 업로드→캐시→방송→단말 흐름을 끝까지 돌려볼 때 쓴다. `APP_ENV=prod` 에서는 거부된다. |

⚠ **온프레미스(폐쇄망)에서는 Polly 를 부를 수 없다.** 엔진이 프로토콜로 분리돼
있으니(`app/tts/engine.py`) 로컬 엔진을 하나 더 구현해 끼우면 된다.

### 실시간 방송

오디오 바이트를 **파싱하거나 다시 자르지 않는다.** 브라우저가 만든 Ogg 페이지를
받은 그대로 Icecast 로 흘려보낸다. 중간에서 재인코딩하면 지연이 붙고 프레임
경계가 어긋나 단말 지터 버퍼가 깨진다.

인코딩은 `opus-recorder`(WASM)로 한다. 브라우저 내장 `MediaRecorder` 는 못 쓴다 —
Chrome 계열이 Ogg 컨테이너를 지원하지 않아 WebM 이 나가고, Icecast 가 그걸 Ogg 로
알고 받아 단말 디코더가 깨진다. 인코더 워커는 번들에 포함되므로 폐쇄망에서도 돈다.

인코더 파라미터(16kHz mono · 24kbps · 40ms)는 `LIVE_START` 로 단말에 보내는
`sample_rate`/`frame_ms` 와 **반드시 같아야 한다**. 한쪽만 바꾸면 소리가 깨진다.
바꿀 일이 있으면 `frontend/src/hooks/useMicUplink.ts` 와
`backend/app/mqtt/publisher.py` 의 `live_start_payload` 를 함께 고친다.

Icecast source 는 HTTP 라이브러리 없이 asyncio 스트림으로 직접 말한다.
Content-Length 도 chunked 도 쓰지 않는 프로토콜이라, aiohttp/httpx 가 붙이는
chunked 인코딩을 Icecast 가 오디오로 읽어버린다.

진행 중인 LIVE 세션은 프로세스 메모리에 있다(`app/live/registry.py`).
Icecast 연결과 WebSocket 은 DB 에 넣을 수 없어서다 — 이력만 DB 에 남는다.
서버를 재기동하면 진행 중이던 LIVE 는 스트림 정보를 잃고 화면에 "업링크 끊김"
으로 보인다. 사용자가 종료하면 정리된다.

### 동시성

방송 시작은 PostgreSQL 어드바이저리 락으로 직렬화한다
(`_lock_broadcast_start`). 겹침 검사와 이벤트 생성 사이에 다른 요청이 끼어들면
아직 커밋 안 된 앞 방송을 못 보고 통과해서, 같은 단말에 방송이 두 개 걸린다 —
실제로 32ms 차이로 재현됐다.

DB 세션은 의존성이 아니라 **미들웨어**가 관리한다. FastAPI 의 `yield` 의존성은
정리(커밋) 코드가 응답을 보낸 뒤 실행돼서, 쓰기 API 가 200 을 돌려준 직후 읽으면
커밋 전 상태가 보였다(20회 중 1회). 미들웨어의 `call_next` 다음은 응답 전송 전이라
그 역전이 생기지 않는다.

### 운영 배포 전 필수

- `JWT_SECRET` 교체 — `APP_ENV=prod` 면 기본값·32바이트 미만은 기동이 거부된다.
- `infra/certs/` 에 인증서 배치 (nginx + mosquitto 공용)
- Mosquitto `passwd` 파일 생성 (`mosquitto_passwd -c passwd xwifi-server`)
- `CORS_ORIGINS` 비우기 — nginx 가 같은 오리진으로 서빙하므로 필요 없다
- `ICECAST_PUBLIC_BASE_URL` 을 실제 도메인으로 — 단말이 받는 `stream_url` 이 이 값으로 만들어진다
- `ICECAST_SOURCE_PASSWORD` 교체
- `TTS_ENGINE=polly` 로 전환 + Polly 권한(`polly:SynthesizeSpeech`) 부여

---

## 코덱스(ESP32) 협의 대기 중

구현은 해뒀지만 단말 쪽 지원이 확인되지 않은 항목:

| 항목 | 내용 | 우선순위 |
|---|---|---|
| `iotradio/device/<mac>/config` | 단말별 CONFIG 토픽 구독. 여러 마을 운영에 필수 | 높음 |
| 공통 CONFIG 의 `village_id` 생략 | 생략 시 단말이 **기존 배정 유지**인지 `00000000` 리셋인지 | 높음 |
| **retain CONFIG 2개의 도착 순서** | 아래 참고 — 리셋 동작이면 배정이 사라질 수 있다 | 높음 |
| `LIVE_START.stream_url` | 세션별 Icecast 마운트 지정. **구현 완료 — 단말 지원 확인 필요** | 높음 |
| `job_id` 통일 | 현재 `session_id`/`cmd_id`/`job_id` 혼재 — 서버는 셋 다 받아준다 | 중간 |
| 마을 재배정 | 배정→재배정 시 이전 마을 토픽 구독 해제 동작 | 중간 |

### retain CONFIG 순서 문제

목 단말로 실측한 결과, 단말이 재접속하면 브로커가 `all/config` 와
`device/<mac>/config` 두 개의 retain 메시지를 보낸다. **MQTT 는 서로 다른 토픽 간
전달 순서를 보장하지 않는다.**

- `all/config` → `device/<mac>/config` 순서면 정상 (배정이 나중에 덮어씀)
- `device/<mac>/config` → `all/config` 순서인데 펌웨어가 "village_id 미수신 = 리셋"
  이면 **방금 받은 배정이 지워진다**

목 단말은 "미수신 시 기존 값 유지"로 구현해 뒀다. 실제 펌웨어가 어느 쪽인지 확인이
필요하고, 리셋 동작이라면 공통 CONFIG 에서 `village_id` 를 아예 다루지 않도록
(필드가 없으면 무시) 펌웨어 수정이 필요하다.
