# ESP32 ↔ Icecast 연동 사양 및 검증 가이드 (1차 LAN 데모)

> **접속 사양(§1)은 통합본으로 이관됨:** `작업지시서/spec/xWIFI_통신_사양_최종_260813.md` §3 참고.
> 이 문서는 브링업 검증 절차(§3 단계별 가이드)용으로 계속 사용.

**대상:** ESP32-P4(오디오/디코딩) + C6(WiFi)가 PC의 Icecast에서 실시간 오디오를 수신
**범위:** TLS/WSS/HTTPS를 잠시 제외한 **평문 HTTP LAN 데모.** TLS·nginx는 서버 준비 후 2차에서 진행.
**참고:** ESP 쪽 TLS/WSS/HTTPS 제거는 별도(코덱스)에서 처리. 이 문서는 **PC 접속 사양 + 검증 순서 가이드**만 정리.

---

## 최종 목표 아키텍처 (참고)

이번 1차는 아래 중 "실시간 오디오(icecast)" 경로만 평문으로 먼저 검증하는 단계.

```
방송 명령/상태 : MQTTS   (추후)
실시간 오디오   : Icecast (이번 1차 대상)
파일 다운로드   : HTTPS 표준 (추후)
```

---

## 1. PC 서버 접속 사양 (ESP가 맞춰야 할 값)

| 항목 | 값 |
|---|---|
| 프로토콜 | 일반 HTTP GET 스트리밍 (WebSocket X, TLS X) |
| PC 주소 | `192.168.0.5` (localhost 아님, PC의 LAN IPv4) |
| 포트 | `8000` |
| 마운트 | `/live` |
| 전체 URL | `http://192.168.0.5:8000/live` |
| 컨테이너/코덱 | Ogg / Opus |
| 비트레이트 | 24 kbps |
| 채널 | mono (1ch) |
| 샘플레이트 | 16000 Hz |
| content_type | `application/ogg` |
| 수신 인증 | **없음** (청취는 비밀번호 불필요) |

> `source123`은 FFmpeg→Icecast **송출용** 비밀번호. 수신 클라이언트(ESP)와는 무관.

### ESP 수신 동작 개념
- TCP 소켓 open → `GET /live HTTP/1.1` + `Host` 헤더 전송 → 응답 헤더 수신 후 **바디를 스트림으로 계속 read**.
- 응답은 끝나지 않는 스트림. Content-Length 없음(chunked/continuous). 연결을 유지하며 Ogg/Opus 페이지를 순차 파싱.
- AWS의 `wss://.../audio`(WebSocket 프레이밍)와 달리, 여기선 **프레이밍 없이 순수 바이트 스트림**. WebSocket 핸드셰이크/마스킹 로직 불필요.

---

## 2. PC 쪽 사전 준비 (이 단계는 PC에서 확인)

이번 데모에서 PC 변경은 최소. 아래만 확인/조정.

1. **PC LAN IP 확인·고정**
   - `ipconfig` → IPv4 주소가 `192.168.0.5`인지 확인. 다르면 그 값으로 아래 URL 전부 치환.
   - DHCP가 IP를 바꾸면 ESP 접속이 깨지므로 **공유기 DHCP 예약 또는 고정 IP** 권장.
2. **Icecast 실행 상태**
   - `netstat -ano | findstr :8000` 로 LISTENING 확인.
   - 없으면: `Start-Process -FilePath "C:\icecast\icecast.exe" -ArgumentList '-c','C:\icecast\icecast.xml'`
3. **hostname(선택)**
   - `C:\icecast\icecast.xml`의 `<hostname>localhost</hostname>` → `192.168.0.5`로 바꾸면 상태 페이지 링크가 정상화됨(접속 자체엔 필수 아님).
4. **방화벽**
   - 1차에서 8000 인바운드 허용을 이미 추가함. 미설정 시(관리자 터미널):
     `netsh advfirewall firewall add rule name="Icecast 8000" protocol=TCP dir=in localport=8000 action=allow`
5. **FFmpeg 송출**
   - 사인파 또는 마이크 소스로 `/live` 송출 중이어야 함(수신 테스트 전 반드시 송출이 살아 있어야 함).
   - 마이크: `ffmpeg -f dshow -i audio="마이크장치명" -c:a libopus -b:a 24k -ac 1 -ar 16000 -application voip -content_type application/ogg -f ogg icecast://source:source123@localhost:8000/live`
6. **PC와 ESP가 같은 LAN(같은 공유기/서브넷)** 에 있는지 확인.

---

## 3. 단계별 검증 가이드 (ESP 개발 시 이 순서로)

각 단계가 **통과된 뒤** 다음으로. 실패하면 그 단계에서 원인부터 격리.

### [검증 1] 접속(연결)이 되는가
- 목표: ESP가 `192.168.0.5:8000`에 TCP 연결 + HTTP 200 응답 수신.
- 확인: ESP 로그에 소켓 연결 성공 + 응답 상태줄(`HTTP/1.0 200 OK`)과 `Content-Type: application/ogg` 헤더가 찍히는지.
- PC 교차확인: Icecast 상태 페이지 `http://192.168.0.5:8000/` 접속 시 `/live` 리스너 수가 1 증가.
- 실패 원인 후보:
  - 연결 자체 실패 → IP 오타 / 다른 서브넷 / 방화벽 / Icecast 미실행.
  - 연결은 되나 404 → 마운트명 오타(`/live` 확인) 또는 FFmpeg 송출 중단(소스 없음).
  - 401 → (수신엔 인증 불필요하므로) 요청에 불필요한 인증 헤더가 잘못 붙었는지 확인.

### [검증 2] 스트림 데이터가 나오는가
- 목표: 응답 바디에서 바이트가 **지속적으로** 들어옴.
- 확인: ESP에서 수신 바이트 카운터가 계속 증가. Ogg 매직 `OggS`(0x4F 0x67 0x67 0x53)로 시작하는 페이지가 보이면 정상.
- 실패 원인 후보:
  - 헤더는 받았는데 바디가 안 늘어남 → 송출(FFmpeg)이 실제로 흐르는지 PC에서 확인(FFmpeg 로그의 time/size 증가).
  - 조금 받고 끊김 → ESP 수신 버퍼가 작아 오버플로/조기 close. read 루프·타임아웃 점검.

### [검증 3] 소리가 나는가
- 목표: 수신한 Ogg/Opus를 P4가 디코딩 → DAC/스피커 출력.
- 확인: 실제 소리 재생. (사인파 송출이면 440Hz 톤, 마이크면 음성)
- 실패 원인 후보:
  - 데이터는 오는데 무음 → Opus 디코더 초기화 파라미터 불일치(16kHz/mono 확인), Ogg 페이지→Opus 패킷 언패킹 로직, DAC 라우팅.
  - 깨진 소리/노이즈 → 샘플레이트 불일치(16000 고정), 채널 수(mono), 패킷 경계 파싱 오류.

### [검증 4] 버퍼링 처리
- 목표: 순간적 네트워크 지연에도 끊김 없이 재생(언더런 방지)하되 지연은 최소.
- 처리 방향:
  - ESP에 **재생 지터 버퍼** 도입: 수신 패킷을 큐에 쌓아 목표 버퍼량(예: 200~500ms) 확보 후 재생 시작.
  - 버퍼 과다 → 지연 증가 / 버퍼 과소 → 언더런(끊김). 소리가 안정적으로 유지되는 **최소 버퍼**를 실측으로 조정.
  - 네트워크 read와 디코딩/재생을 분리(태스크 분리)해 한쪽 지연이 다른 쪽을 막지 않게.
  - Icecast의 `<burst-size>0>`은 이미 저지연 설정. 지연의 대부분은 수신단 버퍼에서 결정됨.

### [검증 5] 전체 디버깅 / 안정화
- 장시간 재생 시 끊김·메모리 누수·재연결 동작 확인.
- **재연결 로직:** 송출 중단·네트워크 순단 시 ESP가 자동 재접속(백오프)하도록.
- WiFi 신호/혼잡 환경에서의 언더런 빈도 측정 → 버퍼값 재조정.
- 동시 리스너(다중 ESP) 테스트 시 Icecast `<sources>`/`<clients>` 여유 확인.

### [검증 6] (서버 준비 후) TLS · nginx 전환
- 이 단계는 공인 도메인/서버가 마련된 뒤 진행.
- nginx를 443 TLS 종단으로 두고 `/audio`(WSS↔Icecast 브리지), `/file`(HTTPS 정적) 구성.
- ESP는 공개 CA bundle(`esp_crt_bundle_attach`) 복귀 + URL을 도메인/`wss://`·`https://`로 교체.
- 최종 구성: 방송 명령/상태=MQTTS, 실시간 오디오=Icecast(nginx WSS 앞단), 파일 다운로드=HTTPS 표준.

---

## 4. 빠른 점검 명령 (PC)

- 리스닝 확인: `netstat -ano | findstr :8000`
- 스트림 헤더 확인: `curl -I http://192.168.0.5:8000/live`
- 상태 페이지: 브라우저에서 `http://192.168.0.5:8000/`
- (진단용, 저지연 재생) `ffplay -fflags nobuffer -flags low_delay -probesize 32 -analyzeduration 0 -infbuf http://192.168.0.5:8000/live`

---

## 5. 이번 1차에서 하지 않는 것 (경계 명확화)

- TLS / 인증서 / 공개 CA bundle 검증 → 2차(서버 준비 후).
- nginx / WSS 브리지 / 도메인 → 2차.
- 파일 다운로드(`/file`) → 2차 nginx에서 HTTPS 표준으로 편입(1차 보류 권장).
- MQTTS(방송 명령/상태) → 별도 트랙.
- PC에 새 패키지 설치 → 이번 1차 범위엔 없음. 필요 시 **설치 전 사용자 확인**.
