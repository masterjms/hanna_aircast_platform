# 브로커 운영 확인 — 서버 회신 2026-09-03

`SERVER_BROKER_QUESTIONS_2026-09-01.md` 네 문항에 대한 답이다. 전부 현재 코드 기준이고, 확인 명령을 같이 적었다.

| # | 질문 | 답 |
|---|---|---|
| 1 | 비밀번호 원본 위치 | **서버 DB(평문)**. 브로커 파일은 DB에서 생성되는 파생물 |
| 2 | `all/cmd` 발행 제한 | **ACL로 서버 계정만**. 사람에게는 MQTT 계정 자체가 없다 |
| 3 | `LimitNOFILE` | systemd 아님(Docker). **65536으로 명시** (2026-09-03) |
| 4 | 등록·운영 같은 서버 | **한 대가 맞다** |

---

## 1. 비밀번호 원본 — 서버 DB가 쥐고 있다

**요청하신 「최소 대안」이 이미 현재 구조다.**

| | |
|---|---|
| 원본 | `devices.mqtt_password` — **평문** 8자 |
| 발급 | 단말 등록 시 서버가 난수 생성. 등록 화면 표시·시리얼 투입(`@MQTTPW=`)에 그대로 쓴다 |
| 브로커 반영 | 서버가 DB 전체에서 mosquitto passwd 파일을 **통째로 재생성**(PBKDF2-SHA512, `$7$101$…`) → 공유 볼륨 → mosquitto 컨테이너의 감시 루프가 설치 + `SIGHUP` |
| ACL | 같은 방식. DB의 마을 배정에서 단말별 `topic read iotradio/village/<id>/cmd` 를 생성 |

평문으로 두는 이유는 시리얼 투입 때 사람이 읽어야 해서다. 해시만 있으면 등록 화면에 보여줄 수 없다. DB 접근 통제(RDS, 서버 접근 자체가 Session Manager 한정)로 막는다는 판단이고, 암호화 저장으로 바꾸는 것은 서버 내부 변경이라 단말 영향 없이 가능하다.

### 브로커를 바꿔야 할 때

DB에서 다시 만들면 끝난다. **서버가 기동할 때마다 이미 그렇게 한다** — passwd·ACL 을 DB 기준으로 재생성해 내보낸다(DB 복구·볼륨 재생성 뒤에도 브로커가 DB와 같아지게). 새 브로커에 이 볼륨만 물리면 단말 재발행은 없다.

DB 연동 인증 플러그인은 검토하지 않았다. 지금 규모(수천 대)에서 파일 재생성은 초 단위라 필요가 없고, 플러그인은 브로커 교체 시 이식성이 오히려 나빠진다.

### 확인

```bash
docker exec xwifi-mosquitto grep -c '^[0-9a-f]\{12\}:' /mosquitto/data/passwd   # 단말 계정 수 = DB 단말 수
```

---

## 2. `iotradio/all/cmd` — ACL에서 서버 계정만 쓴다

**두 겹이다. 화면이 아니라 ACL과 API다.**

### 브로커 ACL (생성된 파일)

```
user xwifi-server
topic readwrite iotradio/#           ← 서버 계정만 all/cmd 에 쓸 수 있다

pattern read iotradio/all/cmd        ← 모든 단말 계정: 읽기만
pattern read iotradio/all/config
pattern write iotradio/device/%u/result
pattern write iotradio/device/%u/status
pattern read  iotradio/device/%u/cmd
pattern read  iotradio/device/%u/config

user <mac>                            ← 배정된 단말마다
topic read iotradio/village/<id8>/cmd
```

단말 계정은 `all/cmd` 에 **write 권한이 없다.** 어떤 단말 계정으로 발행을 시도해도 브로커가 버린다.

### 사람은 MQTT 계정이 없다

이장님 계정은 **웹 로그인 계정**이고 MQTT 자격증명이 아니다. 방송은 전부 웹 API → 서버가 `xwifi-server` 계정으로 발행한다. 그래서 「MQTT 클라이언트를 직접 써서 우회」할 자격증명 자체가 사람에게 없다. 8883은 단말별 계정만 받고, 평문 1883은 호스트로 노출하지 않는다(2026-09-02 폐쇄).

API 쪽에서는 `scope=all` 요청을 **super_admin 만** 통과시킨다(`village_admin` 은 403). 화면에서 버튼을 숨기는 것은 그 위에 얹은 편의일 뿐이다.

### 다만 「생산 라인 위의 보드」 문제는 실제다

지적하신 대로 **전체 방송은 배정 전 단말에도 간다.** 계정을 발급받은 단말은 `all/cmd` 를 구독하고, 서버는 전체 방송을 `all/cmd` 한 토픽에 발행한다.

서버만 바꿔서 막을 수 있는 방법이 있다: **전체 방송을 `all/cmd` 가 아니라 배정된 마을 토픽 전부에 fan-out** 하는 것이다. 단말 변경이 없고, 마을 수만큼 발행이 늘어날 뿐이다(수십 건). 그러면 미배정 단말은 방송을 받을 길이 없어지고, `all/*` 는 CONFIG 전용으로 남는다.

**이렇게 바꿔도 되는지 의견 부탁드린다.** 「전체」의 의미가 "등록된 전부"에서 "배정된 전부"로 바뀌는 것이라 단말 쪽 의도와 맞는지 확인이 필요하다. 동의하시면 서버 작업으로 처리한다.

---

## 3. `LimitNOFILE` — systemd가 아니라 Docker다

mosquitto는 `eclipse-mosquitto:2` 컨테이너로 돈다. `/etc/systemd/system/mosquitto.service.d/` 는 해당 없다.

**2026-09-03 compose에 명시했다:**

```yaml
mosquitto:
  ulimits:
    nofile:
      soft: 65536
      hard: 65536
```

### 확인

```bash
docker exec xwifi-mosquitto sh -c 'grep "open files" /proc/1/limits'
```

배포 뒤 `65536 65536` 이 나와야 한다.

### 같은 규모 기준으로 맞춰 둔 것

| 구성 | 값 | |
|---|---|---|
| nginx | `worker_rlimit_nofile 65536`, `worker_connections 16384` × 워커 | 라이브 스트림은 nginx 를 거쳐 Icecast 로 간다 |
| Icecast | `clients 4000` | 전체 방송 시 등록 단말 전부가 동시에 붙는다 |
| 백엔드 STATUS 처리 | MAC별 최신값을 모아 1초에 한 트랜잭션 | 3000대에서 초당 100건 — 실측 11,900 msg/s 여유 |

### 재시작 때 TLS 핸드셰이크 폭주

말씀하신 대로 mosquitto 가 싱글 스레드라 이건 서버가 막을 수 없다. **단말 재접속에 지터(무작위 지연)가 들어가 있는지** 알려주시면 좋겠다. 없다면 재시작 직후 3000대가 같은 초에 붙는 것을 단말 쪽에서 흩어주는 것이 유일한 완화책이다. 인증서 갱신은 3개월마다라 자주는 아니다.

---

## 4. 한 대가 맞다

서버 쪽 전제도 **고객당 서버 한 대**, 등록과 운영이 같은 서버다. 분리를 전제로 설계한 부분은 없다.

- 신규 단말 등록 화면은 운영 서버(`hanna-aircast.co.kr`)의 계정을 발급하고 그 주소를 시리얼로 넣는다
- 등록 확인(재부팅 후 30초 대기)은 **같은 서버의 DB** 에서 `last_seen_at` 을 본다 — 운영 브로커에 붙었다는 증명이다
- 비밀번호가 두 곳에 존재하는 문제(1번)는 이 구조에서 생기지 않는다

말씀하신 중첩 걱정 셋 중 ①마을 배정·②로그인 권한은 되어 있고, ③`all/cmd` 는 2번의 fan-out 제안이 답이 될 수 있다.

---

## 추가 (2026-09-05)

- **village_id 형식**: 레지스트리 사양 §2.4대로 **법정동코드(10)+연번(2) 12자리**로 바꿨다(문제점 16번). 주소 없는 시험 마을만 예전 8자리를 유지한다. 배포 후 기동 시 재조정이 새 값을 CONFIG로 내린다.
- **CONFIG 해제**: 빈 retain 대신 `village_id` 전부 0 + 올린 `config_version`을 발행한다(문제점 18번, 단말 요청대로).
- **LIVE_READY BUSY 재배달**: 이미 `ok=true`를 받은 단말의 BUSY는 버린다(문제점 17번).

## 관련

| | |
|---|---|
| `SERVER_BROKER_QUESTIONS_2026-09-01.md` | 원 질문 |
| `SERVER_BROADCAST_STOP_SEQUENCE_2026-09-03.md` | 별건 — 방송 종료 순서. 서버 반영 완료(`xWIFI_API_설계_260815.md` §4) |
| `xWIFI_API_설계_260815.md` §2 | 단말별 계정·ACL 생성 구조 |
