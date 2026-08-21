# Icecast 스트리밍 서버 구성 작업 (Windows 10) — 최종본

**목표:** FFmpeg → Icecast → VLC 로 오디오 청취 가능 상태 만들기 (사인파 440Hz 테스트 신호 사용)
**용도:** 임시 데모

## 확정 조건

- **Icecast 설치 위치:** `C:\icecast` (전통적 방식)
- **Icecast 버전:** 2.4.4 포터블 zip
- **작업 폴더 `D:\xWIFI_Icecast`:** 기본적으로 여기에 파일을 만들지 않음. **지시서에서 명시적으로 요구할 때만** 이 폴더에 파일 생성.

---

## ⚠️ 반드시 지킬 제약 조건

1. **기존에 설치된 Python 등 패키지를 임의로 업데이트하지 말 것.** 이 PC는 VSC에서 ESP32 빌드에 사용 중이라 전체 시스템 변동이 빌드를 깨뜨릴 수 있음. winget/chocolatey 설치가 다른 패키지를 건드릴 가능성이 있으면, **실행 전에 무엇이 바뀌는지 설명하고 사용자 확인을 받은 뒤** 진행할 것.
2. **`D:\xWIFI_Icecast`에는 지시서가 명시할 때만 파일 생성.** 그 외 임의 생성 금지.
3. **각 STEP 완료 후 성공/실패를 확인**하고, 실패 시 **원인과 해결책을 먼저 설명한 뒤** 다음으로 진행.

---

## [STEP 1] FFmpeg 확인 및 설치

- 터미널에서 `ffmpeg -version` 실행.
- 없으면 순서대로 시도:
  1. `winget install Gyan.FFmpeg`
  2. winget 없으면 chocolatey
  3. 둘 다 안 되면 수동 다운로드(https://www.gyan.dev/ffmpeg/builds/) 후 PATH 설정 안내
- **주의:** 설치 명령 실행 전 제약 조건 1번 확인. winget/choco로 PATH가 갱신되면 **새 터미널을 열어야** 반영됨.
- 설치 후 `ffmpeg -version`으로 재확인.

## [STEP 2] Icecast 확인 및 설치

- `C:\icecast` 폴더 및 `icecast.exe` 존재로 설치 여부 확인.
- 없으면 **2.4.4 포터블 zip**을 다운로드하여 `C:\icecast`에 압축 해제.
  - 공식 다운로드: https://icecast.org/download/
- **실제 실행 파일 경로 확인** — win32 빌드는 `C:\icecast\icecast.exe`가 아니라 `C:\icecast\bin\icecast.exe`에 있을 수 있음. 이후 STEP의 명령을 실제 경로에 맞출 것.

## [STEP 3] icecast.xml 생성

`C:\icecast\icecast.xml`을 아래 내용으로 생성. `<paths>`는 `C:\icecast` 기준. logs / web / admin 폴더가 실제로 존재하는지 확인(없으면 생성).

```xml
<icecast>
  <limits>
    <clients>500</clients>
    <sources>5</sources>
    <queue-size>102400</queue-size>
    <burst-size>0</burst-size>
  </limits>
  <authentication>
    <source-password>source123</source-password>
    <relay-password>relay123</relay-password>
    <admin-user>admin</admin-user>
    <admin-password>admin123</admin-password>
  </authentication>
  <hostname>localhost</hostname>
  <listen-socket>
    <port>8000</port>
  </listen-socket>
  <http-headers>
    <header name="Access-Control-Allow-Origin" value="*" />
  </http-headers>
  <paths>
    <basedir>C:\icecast</basedir>
    <logdir>C:\icecast\logs</logdir>
    <webroot>C:\icecast\web</webroot>
    <adminroot>C:\icecast\admin</adminroot>
  </paths>
  <logging>
    <accesslog>access.log</accesslog>
    <errorlog>error.log</errorlog>
    <loglevel>3</loglevel>
  </logging>
</icecast>
```

## [STEP 4] Icecast 실행

- 백그라운드 실행 (PowerShell, 실제 exe 경로 사용):

```powershell
Start-Process -FilePath "C:\icecast\icecast.exe" -ArgumentList '-c','C:\icecast\icecast.xml'
```

- 포트 확인: `netstat -ano | findstr :8000`
- 응답 확인: `curl http://localhost:8000`
- 실패 시 `C:\icecast\logs\error.log`를 열어 원인(경로 오류, 포트 점유 등) 분석 후 수정.

## [STEP 5] Windows 방화벽 8000 인바운드 허용

- localhost 청취만이면 필수는 아니나, **LAN 기기(ESP32 등) 접속을 위해** 허용 권장.
- 관리자 권한 터미널에서:

```
netsh advfirewall firewall add rule name="Icecast 8000" protocol=TCP dir=in localport=8000 action=allow
```

## [STEP 6] FFmpeg 테스트 오디오 전송

**Windows에서는 한 줄로 실행** (리눅스용 `\` 줄바꿈 제거함):

```
ffmpeg -re -f lavfi -i "sine=frequency=440:sample_rate=16000" -c:a libopus -b:a 24k -ac 1 -ar 16000 -application voip -content_type application/ogg -f ogg icecast://source:source123@localhost:8000/live
```

- 로그에 스트리밍 진행(시간·bitrate 갱신)이 보이는지 확인.
- 인증 오류(source password) / 연결 거부(서버 미실행)면 원인 설명 후 수정.

## [STEP 7] 최종 상태 확인

- `netstat -ano | findstr :8000` — 연결 확인
- `curl -I http://localhost:8000/live` — 스트림 응답 확인
- 아래 출력:

```
=== 테스트 완료 ===
VLC에서 아래 URL로 청취 테스트:
  미디어 → 네트워크 스트림 열기 → http://localhost:8000/live → 재생
===================
```
