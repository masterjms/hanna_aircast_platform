# xWIFI AWS 운영 인프라 계획 (2026-08-25)

개발 PC의 시스템을 실 도메인이 붙은 AWS 운영 서버로 올리기 위한 규모·구성·비용 계획.
전제: 단말 최대 300대, 마을 3~10곳, 고객당 서버 1대(운영서버 개요 A안).
기존 확정 방침(TLS 인수인계 260823: ALB 미사용·단일 도메인+path·LE 인증서 공용)을 그대로 따른다.

## 1. 권장안 요약

| 항목 | 선택 | 비고 |
|---|---|---|
| 컴퓨트 | EC2 **t3.medium** (2vCPU/4GB), 서울 | Ubuntu 24.04 LTS, Docker Compose |
| DB | **RDS Postgres** db.t4g.micro, 단일 AZ | 자동백업 7일 + PITR |
| 스토리지 | EBS gp3 **50GB** | OS+파일함, DLM 일일 스냅샷 |
| 네트워크 | Elastic IP 1개, 도메인 1개 + path | 서브도메인/와일드카드 안 씀 |
| TLS | Let's Encrypt 1장 → nginx(443) + mosquitto(8883) 공용 | certbot deploy-hook 으로 양쪽 reload |
| 월 비용 | **약 $81** (온디맨드, 방송 하루 1시간 가정) | 상세 §6 |

## 2. 규모 산정 근거

- MQTT 300 연결 + STATUS 30초 주기 → mosquitto에겐 미미
- 실시간 방송: 300대 × 24kbps ≈ **7.2Mbps** — 복제는 Icecast가 하고 CPU 거의 안 씀
- TTS ffmpeg 변환이 유일한 CPU 스파이크 → 2vCPU면 충분
- t3.small(2GB)로도 도는 부하지만, 컨테이너 4개+OS에 2GB는 OOM 여유가 없어 4GB 권장.
  월 $19 차이. 안정화 후 축소는 쉽다.
- Graviton(t4g)은 20% 싸지만 libretime/icecast 등 이미지 ARM 검증이 선행 조건 — 첫 구축은 x86.
- **병목은 서버가 아니라 송신 트래픽 요금**이다(방송 1시간 ≈ 3GB).

## 3. DB — RDS를 쓰는 이유

방송 이력·단말 배정·계정이 전부 DB다. RDS는 자동 일일 백업 + 특정 시점 복구(PITR)가
설정 하나로 켜진다(+$15~20/월). EC2 내 컨테이너 Postgres는 cron+pg_dump 스크립트를
직접 만들고 직접 검증해야 하고, EC2가 죽으면 DB도 같이 죽는다.
Multi-AZ는 비용 2배라 이 규모에선 과함 — 단일 AZ + 자동백업이면 충분.
온프레미스 전환은 양쪽 다 `DATABASE_URL` 하나로 동일(개요 문서 이식 원칙).

## 4. 보안 그룹

| 포트 | 용도 | 허용 | 기간 |
|---|---|---|---|
| 443 | 관리자 UI·API·파일·Icecast 프록시(TLS 후) | 전체 | 상시 |
| 80 | certbot HTTP-01 + 리다이렉트 | 전체 | 상시 |
| 8883 | mqtts | 전체 | 펌웨어 TLS 반영 후 |
| 1883 | mqtt 평문 | 가능하면 단말 회선 IP 제한 | **전환기만** |
| 8000 | Icecast http | 가능하면 단말 회선 IP 제한 | **전환기만** |
| 22 | SSH | 관리자 IP만 | 상시 |
| 5432 | RDS | EC2 SG에서만 (인터넷 노출 금지) | 상시 |

⚠ 전환기 제약: 현 펌웨어는 mqtts·https Icecast 미지원(ESP32 회신 260824 §3).
반영 확인 후 1883/8000 폐쇄.

## 5. 백업·복구·감시

상태를 가진 곳은 DB(RDS)와 파일함(EBS) 두 곳뿐.

- DB: RDS 자동백업 7일 + PITR
- EBS: DLM 일일 스냅샷 7일
- 서버 구성: git 저장소가 원본. `.env`(비밀값)만 담당자가 별도 안전 보관
- **복구 리허설 1회 필수**: "EC2 소실" 가정 → 새 EC2 + clone + .env + 스냅샷 볼륨 + RDS 연결 → 1시간 내 기동

알람(CloudWatch→SNS 이메일): StatusCheckFailed / CPU 15분 80% / 디스크 80% /
메모리 85%(Agent) / RDS 여유공간 2GB. 외부 생존 확인: `https://<도메인>/api/health`
1분 주기(UptimeRobot 무료면 충분). 컨테이너는 restart 정책 + Docker 로그 크기 상한.

## 6. 월 비용 추정 (서울, 온디맨드, 대략)

| 항목 | 월 |
|---|---|
| EC2 t3.medium | $38 |
| EBS 50GB + 스냅샷 | $7 |
| RDS micro + 20GB | $18 |
| EIP | $4 |
| 송신 90GB (하루 1시간 방송) | $11 |
| 기타 | $3 |
| **합계** | **≈ $81** |

트래픽만 사용량 비례(방송 1시간 ≈ 3GB) — 예정된 비용 조회 기능이 다룰 축.
안정화 후 Savings Plan으로 EC2·RDS 30~40% 절감 가능(처음부터 묶지 말 것).
도메인 연 2~3만원 별도.

## 7. 구축 절차 (배포 담당자 실행 — 운영 서버 직접 접근 금지 원칙)

1. 도메인 구입 + AWS 계정 정리 (루트 MFA, IAM 사용자, Budget $150 알림)
2. 네트워크: 기본 VPC, EIP, 보안그룹 2개(EC2/RDS)
3. RDS 생성 (Postgres 18 — 로컬 컨테이너와 **같은 메이저 버전**이어야 pg_dump 가 동작한다, 자동백업 7일, 퍼블릭 차단)
4. EC2 생성 (t3.medium, gp3 50GB, EIP) + Docker 설치
5. 배포: clone → 운영 .env → compose up → alembic → 시드
6. DNS A레코드 + certbot + deploy-hook
7. DLM 스냅샷 + CloudWatch 알람 + 외부 생존 확인
8. 엑셀 MAC↔마을 사전 등록 투입
9. 실단말 1대 전 과정 검증 (접속→배정→파일→라이브)
10. 복구 리허설 — 이것까지가 "구축 완료"

## 8. 로드맵

1. 구축 (관리자 https, 단말 평문 병행)
2. 운영 개시 + 2주 안정화
3. TLS 전환 (펌웨어 crt_bundle 반영 대기) → 평문 포트 폐쇄
4. 최적화 (인스턴스 크기 조정, Savings Plan, 비용 조회 기능 연동)

온프레미스 이식성: 앱 스택은 전부 Compose 위 일반 프로세스. 갈아끼울 곳은
DB·백업·감시 3곳뿐(개요 문서 대체표) — 이 계획은 그 원칙을 위반하는 요소를 넣지 않았다.

## 9. 배포 · CI/CD (2026-08-26 추가)

실제 구축을 거치며 확인된 절차를 코드로 옮겼다.

### 배포 스크립트 — `scripts/deploy.sh`

```
bash scripts/deploy.sh           평소 배포
bash scripts/deploy.sh --force   방송 중이어도 진행
bash scripts/deploy.sh --no-pull 현재 코드로 재빌드만
```

순서에 각각 이유가 있다:

1. **진행 중인 방송이 있으면 거절** — 배포는 컨테이너를 교체하므로 방송이 끊긴다.
   단말은 스트림이 끊겨도 재접속하지 않으므로(ESP32 정정 260824) 그 방송은 영영 끝난다.
2. **마이그레이션을 컨테이너 교체 전에** — 실패해도 기존 컨테이너가 살아 있어 서비스가 안 죽는다.
3. **nginx 를 반드시 함께 재시작** — 컨테이너를 재생성하면 새 IP 를 받는데 nginx 는
   upstream 호스트명을 기동 시 한 번만 해석해 캐시한다. 빼먹으면 502 (실제로 겪음).
4. **헬스체크 확인, 실패 시 되돌리는 명령을 출력**

### CI — `.github/workflows/ci.yml`

서버 없이 동작하므로 배포 자동화보다 먼저 도입한다. 4개 잡:

| 잡 | 내용 | 막는 사고 |
|---|---|---|
| backend | ruff + pytest(46건) | 회귀 |
| migration | 실제 Postgres 로 upgrade→downgrade→upgrade | 되돌릴 수 없는 마이그레이션 머지 |
| frontend | `npm run build` (tsc -b 포함) | 운영 빌드 실패 — `--noEmit` 만으로는 못 잡는다(실제로 겪음) |
| stack | 운영 compose 를 통째로 띄워 health=ok 확인 | 스테이징 서버 없이 "실제로 뜨는가" 검증 |

### CD (다음 단계)

지금은 서버에서 빌드한다. 다음 단계는 CI 에서 빌드한 이미지를 쓰는 것이다:

1. CI 가 이미지를 만들어 GHCR 에 `:<커밋sha>` 로 푸시
2. 릴리스 태그를 달면 배포 잡이 대기 → **GitHub Environment 보호 규칙으로 사람이 승인**
3. **AWS SSM Run Command** 로 서버에서 `deploy.sh` 실행 — GitHub 에 SSH 키를 두지 않고
   보안그룹 22 번을 인터넷에 열 필요도 없다
4. 실패 시 이전 이미지 태그로 롤백

트리거를 `main` 푸시가 아니라 **릴리스 태그**로 두는 이유: 마을방송은 "지금 나가도 되는
시간인가"를 사람이 판단해야 하는 서비스다.
