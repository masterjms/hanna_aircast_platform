# PC(서버) MQTT 준비 & 방송 제어 도구 구축 가이드 (1차)

**목표:** PC에 MQTT 브로커(Mosquitto)를 올리고, **번호 메뉴 방식**의 제어 도구로 방송 시작(LIVE_START)·종료(LIVE_STOP)·상태 체크를 수행.
**전제:** Icecast 1차 완료. 오디오는 FFmpeg → Icecast(`:8000/live`)로 이미 송출 가능.
**1차 범위:** 평문 MQTT(포트 1883), 익명 허용, TLS/인증 없음. **TLS·인증은 2차**(실서버).
**작업 폴더:** `D:\xWIFI_Icecast\mqtt\` (필요 파일 여기 생성)

---

## ⚠️ 반드시 지킬 제약 조건
1. **기존 Python 등 패키지를 임의 업데이트 금지.** 이 PC는 VSC ESP32 빌드에 사용 중. Mosquitto는 네이티브 C라 Python과 무관하지만, **winget 설치가 VC++ 재배포판 등 의존성을 딸려 설치할 수 있으므로 설치 전 무엇이 바뀌는지 설명하고 사용자 확인 후** 진행.
2. **파일은 `D:\xWIFI_Icecast\mqtt\`에만 생성.** 그 외 임의 생성 금지.
3. **각 STEP 완료 후 성공/실패 확인**, 실패 시 원인·해결 먼저 설명 후 진행.

---

## 참고: 이 도구가 서버에서 하는 역할
- MQTT 브로커: PC에서 실행, 단말(ESP32-C6)이 접속하는 메시지 허브.
- 제어 도구(배치 메뉴): 서버가 단말에게 명령을 **publish**하고, 단말 결과/상태를 **subscribe**로 확인.
- 오디오 자체는 MQTT가 아니라 Icecast로 흐름. MQTT는 "언제 켜고 끌지" 명령/상태만 담당.

단말(ESP32) 접속 파라미터(서버가 알아야 할 값):
```
브로커 주소 : <PC_LAN_IP>:1883   (예: 192.168.0.5:1883)
client-id   : iotradio-<mac_nocolon>   (예: iotradio-58e6c5f2cc74)
구독(cmd)   : iotradio/device/<mac_nocolon>/cmd
              iotradio/village/00000001/cmd
              iotradio/all/cmd
발행(보고)  : iotradio/device/<mac_nocolon>/result
              iotradio/device/<mac_nocolon>/status
```

---

## [STEP 1] Mosquitto 설치
- 설치 여부 확인: `where mosquitto` 또는 `"C:\Program Files\mosquitto\mosquitto.exe" -h`
- 없으면 설치(순서대로):
  1. `winget search mosquitto` 로 패키지 확인 후 `winget install --id EclipseFoundation.Mosquitto -e`
     - **설치 전 제약 1번 확인.** 의존성(VC++ 재배포판)이 함께 설치될 수 있음.
  2. winget 불가 시 공식 설치본: https://mosquitto.org/download/ (Windows 64-bit installer, 네이티브)
- 기본 설치 경로: `C:\Program Files\mosquitto`
- 포함 실행 파일: `mosquitto.exe`(브로커), `mosquitto_pub.exe`, `mosquitto_sub.exe`(CLI 클라이언트)
- 설치 확인: `"C:\Program Files\mosquitto\mosquitto.exe" -h` 로 버전/도움말 출력.

## [STEP 2] 브로커 설정 파일 생성
`D:\xWIFI_Icecast\mqtt\mosquitto.conf` 생성 (LAN 단말 접속 + 익명 허용):

```conf
# xWIFI 1차 데모용 (평문 / 인증·TLS 없음 → 2차에서 강화)
listener 1883 0.0.0.0
allow_anonymous true
log_type all
```

> 주의: Mosquitto 2.0 기본값은 localhost 전용 + 익명 불가. 위 conf로 실행해야 **다른 PC/단말이 LAN으로 접속** 가능.

## [STEP 3] 방화벽 1883 인바운드 허용
관리자 권한 터미널:
```
netsh advfirewall firewall add rule name="MQTT 1883" protocol=TCP dir=in localport=1883 action=allow
```

## [STEP 4] 브로커 실행
- **설치 시 자동 등록된 Mosquitto 서비스가 1883을 먼저 점유하면** 우리 conf가 안 먹으므로, 데모 중엔 서비스를 멈추고 수동 실행 권장:
  - 서비스 중지(관리자): `net stop mosquitto`
  - 수동 실행(로그 보이게): `"C:\Program Files\mosquitto\mosquitto.exe" -c "D:\xWIFI_Icecast\mqtt\mosquitto.conf" -v`
- 리스닝 확인: `netstat -ano | findstr :1883`

## [STEP 5] 브로커 동작 자체 점검 (단말 없이)
창 2개로 확인:
- 창A(구독): `"C:\Program Files\mosquitto\mosquitto_sub.exe" -h localhost -t test/hello -v`
- 창B(발행): `"C:\Program Files\mosquitto\mosquitto_pub.exe" -h localhost -t test/hello -m "ok"`
- 창A에 `test/hello ok` 가 뜨면 브로커 정상.

## [STEP 6] 결과/상태 모니터 배치 생성
`D:\xWIFI_Icecast\mqtt\monitor.bat` — 단말의 result/status를 실시간 표시(항상 켜두는 창):

```bat
@echo off
chcp 65001 >nul
title MQTT 결과/상태 모니터
set "MOSQ=C:\Program Files\mosquitto"
set "BROKER=localhost"
echo ==== MQTT 결과/상태 모니터 (Ctrl+C 로 종료) ====
"%MOSQ%\mosquitto_sub.exe" -h %BROKER% -t "iotradio/device/+/result" -t "iotradio/device/+/status" -v
```

## [STEP 7] 번호 메뉴 제어 배치 생성
`D:\xWIFI_Icecast\mqtt\control.bat` — 번호만 눌러 방송 시작/종료/상태 체크:

```bat
@echo off
setlocal
chcp 65001 >nul
title xWIFI MQTT 방송 제어

REM ===== 설정 (환경에 맞게 수정) =====
set "MOSQ=C:\Program Files\mosquitto"
set "BROKER=localhost"
set "VILLAGE=00000001"
set "TARGET=iotradio/village/%VILLAGE%/cmd"
set "TARGET_DESC=마을 %VILLAGE% (village)"
REM 오디오 송출(FFmpeg -> Icecast). 마이크 장치명을 실제 값으로 교체.
REM 소스를 이미 따로 돌리고 있으면 아래 MIC/ICECAST 사용 라인을 주석 처리.
set "MIC=마이크장치명"
set "ICECAST=icecast://source:source123@localhost:8000/live"
set "SDIR=%~dp0"
set "SFILE=%SDIR%session.txt"
if not exist "%SFILE%" (>"%SFILE%" echo 0)
REM ==================================

:menu
cls
echo ==================================
echo    xWIFI MQTT 방송 제어 (1차)
echo ==================================
echo  브로커 : %BROKER%
echo  대상   : %TARGET_DESC%
echo ----------------------------------
echo   1) 방송 시작 (오디오 송출 + LIVE_START)
echo   2) 방송 종료 (LIVE_STOP + 오디오 중단)
echo   3) 상태 체크 (STATUS/결과 조회)
echo   4) 결과 모니터 창 열기
echo   0) 종료
echo ==================================
choice /c 12340 /n /m "번호 선택: "
set "SEL=%errorlevel%"
if "%SEL%"=="1" goto start
if "%SEL%"=="2" goto stop
if "%SEL%"=="3" goto status
if "%SEL%"=="4" goto monitor
if "%SEL%"=="5" goto end
goto menu

:start
set /p SESSION=<"%SFILE%"
set /a SESSION=SESSION+1
>"%SFILE%" echo %SESSION%
REM 오디오 송출 시작(별도 창). 소스를 따로 관리하면 이 줄 삭제.
start "FFMPEG_SRC" cmd /c ffmpeg -f dshow -i audio="%MIC%" -c:a libopus -b:a 24k -ac 1 -ar 16000 -application voip -content_type application/ogg -f ogg %ICECAST%
set "TMPJSON=%TEMP%\live_start.json"
>"%TMPJSON%" echo {"type":"LIVE_START","session_id":%SESSION%,"codec":"opus","frame_ms":40,"sample_rate":16000,"record_flash":0,"file_name":"live-demo.lopus","ready_timeout_sec":30}
"%MOSQ%\mosquitto_pub.exe" -h %BROKER% -t "%TARGET%" -q 1 -f "%TMPJSON%"
echo.
echo [보냄] LIVE_START (session_id=%SESSION%) -^> %TARGET%
pause
goto menu

:stop
set /p SESSION=<"%SFILE%"
set "TMPJSON=%TEMP%\live_stop.json"
>"%TMPJSON%" echo {"type":"LIVE_STOP","session_id":%SESSION%}
"%MOSQ%\mosquitto_pub.exe" -h %BROKER% -t "%TARGET%" -q 1 -f "%TMPJSON%"
REM 오디오 송출 중단(ffmpeg 종료). 소스를 따로 관리하면 이 줄 삭제.
taskkill /IM ffmpeg.exe /F >nul 2>&1
echo.
echo [보냄] LIVE_STOP (session_id=%SESSION%) -^> %TARGET%
pause
goto menu

:status
echo.
echo 5초간 STATUS/결과 수신 대기...
"%MOSQ%\mosquitto_sub.exe" -h %BROKER% -t "iotradio/device/+/status" -t "iotradio/device/+/result" -v -W 5
echo.
pause
goto menu

:monitor
start "MQTT MONITOR" "%SDIR%monitor.bat"
goto menu

:end
endlocal
exit /b
```

### 제어 배치 사용 메모
- **대상 변경:** 파일 상단 `VILLAGE`/`TARGET` 값만 바꾸면 됨.
  - 특정 단말 1대: `set "TARGET=iotradio/device/58e6c5f2cc74/cmd"`
  - 전체: `set "TARGET=iotradio/all/cmd"`
- **session_id**는 `session.txt`에 저장·자동 증가. LIVE_STOP은 마지막 session_id로 전송.
- **오디오 소스를 별도 관리**(1차/2차 스크립트로 이미 송출)하면 `:start`의 `start "FFMPEG_SRC" ...`와 `:stop`의 `taskkill ...` 줄을 삭제하고, LIVE_START/STOP publish만 남길 것.
- `%MIC%`는 STEP(마이크 장치명 확인)에서 얻은 실제 이름으로 교체.

## [STEP 8] 통합 테스트 순서
1. 브로커 실행(STEP 4) → `netstat`로 1883 확인.
2. `monitor.bat` 실행(결과/상태 창 상시 유지).
3. `control.bat` 실행 → `1) 방송 시작` → monitor 창에 단말의 `LIVE_READY` 수신 확인.
4. 단말이 Icecast stream 수신 → 스피커로 소리.
5. `3) 상태 체크` → 단말 STATUS(예: state, rssi) 확인.
6. `2) 방송 종료` → monitor 창/단말에서 종료 처리 확인.

## [STEP 9] 자주 나는 문제
- **단말이 브로커에 접속 못 함** → conf의 `listener 1883 0.0.0.0` 누락 / 방화벽 1883 / 단말이 `localhost`가 아닌 `PC_LAN_IP:1883`로 접속하는지 / 같은 LAN인지.
- **publish는 되는데 단말 반응 없음** → 단말 구독 topic과 `TARGET` 불일치(village_id·mac 표기). topic은 콜론 없는 소문자 MAC.
- **명령이 두 번 처리됨** → QoS 1 중복. 단말이 session_id/cmd_id로 중복 무시하는지(스펙대로).
- **소리 안 남** → LIVE_START는 갔지만 FFmpeg 송출이 안 도는 경우. Icecast `/live`에 소스가 흐르는지 확인.
- **1883이 이미 점유** → 자동 서비스가 떠 있음. `net stop mosquitto` 후 수동 실행.

---

## 1차 / 2차 경계
- **1차(이번):** 평문 1883, 익명 허용, IP 기반. 번호 메뉴로 LIVE_START/STOP·상태 확인.
- **2차(실서버):** MQTTS(8883)+TLS/인증(계정·ACL), 도메인 기반, LWT/heartbeat 정식 운용, 다중 단말 LIVE_READY 수집 정책.

> 참고(선택): GUI로 토픽을 보고 싶으면 MQTT Explorer(`winget install thomasnordquist.MQTT-Explorer`)를 모니터 대용으로 쓸 수 있음(설치는 제약 1번 적용).
