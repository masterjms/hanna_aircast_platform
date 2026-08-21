# Icecast 마이크 스트리밍 — 최종 테스트 가이드 (검증 완료본)

**목적:** PC 마이크 → FFmpeg → Icecast → VLC/ffplay 로 실시간 음성 청취.
**상태:** 1차(사인파 테스트)·2차(마이크 실시간)까지 전부 성공 확인. 이 문서의 명령어는 전부 이 PC에서 실제로 동작 검증됨.

**환경 확정 정보**
- Icecast 설치 경로: `C:\icecast` (2.4.4)
- **실행파일 실제 경로:** `C:\icecast\bin\icecast.exe` (설치 경로 바로 밑이 아니라 `bin` 폴더 안에 있음 — 항상 이 경로 사용)
- 설정파일: `C:\icecast\icecast.xml`
- 포트: 8000 / 마운트: `/live` / source 비밀번호: `source123`
- VLC 설치 경로: `C:\Program Files\VideoLAN\VLC\vlc.exe`
- 마이크 장치(이 PC): `마이크 배열(인텔® 스마트 사운드 기술)`
  - 한글/특수문자(®) 인코딩 문제를 피하기 위해 **GUID 대체 이름 사용 권장**:
    `@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}\wave_{0CD24361-9DBD-4CBB-A112-16BA64811C71}`

---

## STEP 1. Icecast 서버 실행 확인/시작

```powershell
netstat -ano | findstr :8000
curl.exe -s -o NUL -w "HTTP Status: %{http_code}`n" http://localhost:8000/status.xsl
```

- `LISTENING` + `HTTP Status: 200` 이면 이미 실행 중 → STEP 2로.
- 안 떠 있으면 실행:

```powershell
Start-Process -FilePath "C:\icecast\bin\icecast.exe" -ArgumentList '-c','C:\icecast\icecast.xml'
Start-Sleep -Seconds 2
netstat -ano | findstr :8000
```

**확인 포인트:** `C:\icecast\logs\error.log` 마지막 줄에 `Icecast 2.4.4 server started` 있으면 정상.
**참고:** `http://localhost:8000` (루트 경로)는 404가 정상입니다(웹 인덱스 파일 없음). 상태 확인은 `/status.xsl`로 할 것.

---

## STEP 2. 마이크 장치 이름 확인 (장치가 바뀌었을 때만)

```powershell
ffmpeg -list_devices true -f dshow -i dummy
```

- `(audio)` 라벨 붙은 장치명과 바로 아래 `Alternative name` (GUID 형식) 확인.
- 이 PC는 위 "환경 확정 정보"의 GUID를 그대로 쓰면 됨. 장치를 새로 붙였을 때만 재확인.

---

## STEP 3. 마이크 → Icecast 스트리밍 시작

```powershell
$deviceName = 'audio=@device_cm_{33D9A762-90C8-11D0-BD43-00A0C911CE86}\wave_{0CD24361-9DBD-4CBB-A112-16BA64811C71}'
$proc = Start-Process -FilePath "ffmpeg" -ArgumentList '-f','dshow','-i',$deviceName,'-c:a','libopus','-b:a','24k','-ac','1','-ar','16000','-application','voip','-flush_packets','1','-content_type','application/ogg','-f','ogg','icecast://source:source123@localhost:8000/live' -PassThru -RedirectStandardError "$env:TEMP\ffmpeg_mic.log" -WindowStyle Hidden
Write-Output "FFmpeg PID: $($proc.Id)"
Start-Sleep -Seconds 5
Get-Content "$env:TEMP\ffmpeg_mic.log" -Tail 10
```

**확인 포인트:** 로그에 `size=`, `time=`, `bitrate=` 가 계속 값이 늘어나며 갱신되면 정상.
- `-flush_packets 1` : 전송 지연을 줄이기 위해 1차 대비 추가한 옵션 (IO 버퍼가 다 찰 때까지 기다리지 않고 즉시 전송).

**자주 나는 오류:**
| 오류 | 원인 | 해결 |
|---|---|---|
| `Could not find audio only device` | 장치명 오타/인코딩 문제 | GUID 형식(Alternative name) 사용 |
| `I/O error` / `Connection refused` | Icecast 미실행 | STEP 1로 복귀 |
| `401 Unauthorized` | source 비밀번호 불일치 | `source123` 확인 |

---

## STEP 4. 청취 테스트 (VLC 또는 ffplay)

**VLC (지연 낮춤 옵션 포함):**
```powershell
Start-Process -FilePath "C:\Program Files\VideoLAN\VLC\vlc.exe" -ArgumentList '--network-caching=200','http://192.168.0.5:8000/live'
```

**ffplay (더 저지연, 진단용):**
```powershell
Start-Process -FilePath "ffplay" -ArgumentList '-fflags','nobuffer','-flags','low_delay','-probesize','32','-analyzeduration','0','-infbuf','http://192.168.0.5:8000/live'
```

**주의:** VLC와 ffplay를 동시에 열면 같은 소리가 두 번(에코처럼) 들립니다. 정상 청취 테스트 시에는 **하나만** 켤 것.

**확인 포인트:** 마이크에 말했을 때 스피커/헤드셋으로 목소리가 들리면 성공.

---

## STEP 5. 지연(latency) 참고

- 이 구성(FFmpeg → Icecast HTTP push → 클라이언트)에서 **1~3초 지연은 정상 범위**입니다. Icecast/HTTP 구조 특성상 발생하는 누적 지연이며, 데모 목적으로는 이 수준을 최종으로 확정함.
- 더 줄이고 싶으면 VLC `--network-caching` 값을 100까지 낮춰볼 수 있으나, 너무 낮추면 끊김(buffer underrun) 발생 가능.
- 진짜 수백ms 이하의 "즉시" 반응이 필요하면 Icecast가 아닌 WebRTC/RTP 같은 별도 프로토콜이 필요함 (이번 범위 밖).

---

## STEP 6. 최종 상태 확인

```powershell
netstat -ano | findstr :8000
curl.exe -s --max-time 1 -D - -o NUL http://192.168.0.5:8000/live
Get-Process ffmpeg,icecast -ErrorAction SilentlyContinue | Select-Object Id, ProcessName
```

- `ESTABLISHED` 연결(소스+리스너), `curl` 응답 `200 OK`, ffmpeg/icecast 프로세스 둘 다 떠 있으면 정상.

---

## 정리(종료) 명령

테스트 끝나고 정리할 때:

```powershell
Get-Process ffmpeg,icecast,vlc,ffplay -ErrorAction SilentlyContinue | Stop-Process -Force
netstat -ano | findstr :8000   # 결과 없어야(또는 TIME_WAIT만) 정상
```

---

## 트러블슈팅 메모 (1차 설치 시 실제 겪은 이슈)

- Icecast 2.4.4는 공식 배포에 **포터블 zip이 없음** (win32 exe 설치 프로그램만 존재). `icecast_win32_2.4.4.exe /S /D=C:\icecast` 로 무인 설치해서 사용.
- VLC를 `get.videolan.org`에서 직접 받으면 실제 설치 파일이 아니라 **다운로드 매니저 HTML 페이지**가 받아짐(파일 크기 비정상적으로 작음). `download.videolan.org/pub/videolan/vlc/<버전>/win64/vlc-<버전>-win64.exe` 같은 직접 미러 URL 사용해야 함.
- `curl -I http://localhost:8000/live` (HEAD 요청)는 Icecast 2.4.4가 스트림 마운트포인트에서 지원하지 않아 `400 Bad Request`가 남 (정상). 확인은 GET(`curl -D - -o NUL`)으로 할 것.
