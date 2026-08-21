# Icecast 실시간 마이크 스트리밍 (Windows 10) — 2차 지시서

**목표:** PC 마이크에 말하면 VLC에서 들리게 하기 (PC 마이크 → FFmpeg → Icecast → VLC)
**전제:** 1차 데모(사인파 → Icecast → VLC) 성공 완료. Icecast는 `C:\icecast`에 2.4.4 포터블로 설치되어 실행 가능한 상태.

## 변경 요약 (1차 대비)

- 입력 소스만 교체: 사인파(`-f lavfi -i sine=...`) → **Windows 마이크 캡처(dshow)**
- 실시간 입력이므로 `-re` 옵션 제거 (마이크가 이미 실시간 속도)
- 인코딩·서버·마운트(`/live`)·포트(8000) 등 나머지는 1차와 동일

---

## ⚠️ 반드시 지킬 제약 조건

1. **기존에 설치된 Python 등 패키지를 임의로 업데이트하지 말 것.** 이 PC는 VSC에서 ESP32 빌드에 사용 중이라 전체 시스템 변동이 빌드를 깨뜨릴 수 있음. 추가 설치가 다른 패키지를 건드릴 가능성이 있으면 **실행 전 설명 후 사용자 확인**을 받고 진행.
2. **`D:\xWIFI_Icecast`에는 지시서가 명시할 때만 파일 생성.** 그 외 임의 생성 금지.
3. **각 STEP 완료 후 성공/실패를 확인**하고, 실패 시 **원인과 해결책을 먼저 설명한 뒤** 다음으로 진행.

---

## [STEP 1] Icecast 실행 상태 확인

- 1차 서버가 떠 있는지 확인: `netstat -ano | findstr :8000`
- 응답 확인: `curl http://localhost:8000`
- 안 떠 있으면 다시 실행:

```powershell
Start-Process -FilePath "C:\icecast\icecast.exe" -ArgumentList '-c','C:\icecast\icecast.xml'
```

## [STEP 2] 마이크 장치 이름 확인

- FFmpeg으로 DirectShow 오디오 장치 목록 출력:

```
ffmpeg -list_devices true -f dshow -i dummy
```

- 출력에서 **오디오(audio) 장치명**을 정확히 확인 (예: `Microphone (Realtek(R) Audio)`).
- **주의:** 장치명은 대소문자·괄호·공백까지 그대로 사용해야 함. 다음 STEP의 명령에 이 이름을 그대로 넣을 것.
- 장치가 안 보이면: Windows 설정 → 개인정보 → 마이크 접근 허용 여부, 마이크 물리적 연결/음소거 상태 확인.

## [STEP 3] 마이크 스트리밍 전송

- 아래 명령의 `여기에_마이크_장치명`을 STEP 2에서 확인한 실제 이름으로 교체 후 **한 줄로 실행**:

```
ffmpeg -f dshow -i audio="여기에_마이크_장치명" -c:a libopus -b:a 24k -ac 1 -ar 16000 -application voip -content_type application/ogg -f ogg icecast://source:source123@localhost:8000/live
```

- 성공 시: 로그에 시간·bitrate가 실시간으로 갱신됨. 마이크에 말하면 입력 레벨(size/time)이 계속 증가.
- 자주 나는 오류와 원인:
  - `Could not find audio only device` → 장치명 오타. STEP 2 목록과 정확히 일치시킬 것.
  - `I/O error` / `Connection refused` → Icecast 미실행. STEP 1로 복귀.
  - `401 Unauthorized` → source 비밀번호 불일치(`source123` 확인).

## [STEP 4] VLC 청취 확인

- VLC → 미디어 → 네트워크 스트림 열기 → `http://localhost:8000/live` → 재생.
- 마이크에 말했을 때 소리가 들리면 성공.

## [STEP 5] 지연(latency) 튜닝 — 선택

기본 상태에서는 VLC 네트워크 버퍼 때문에 보통 1초 이상 늦게 들림. "말하면 거의 바로" 수준을 원하면 아래를 조정.

- **VLC 쪽 (효과 큼):** 도구 → 환경설정 → (하단) 설정 표시: 전체 → 입력/코덱 → **네트워크 캐싱(ms)** 값을 기본 1000 → 200~300으로 낮춤. 또는 커맨드라인:

```
vlc --network-caching=200 http://localhost:8000/live
```

- **FFmpeg/Icecast 쪽:** icecast.xml의 `<burst-size>0</burst-size>` 유지(이미 저지연 설정), Opus `-application voip` 유지.
- 너무 낮추면 끊김(buffer underrun) 발생하므로, 소리가 안정적으로 들리는 최소값을 찾아 조정.

## [STEP 6] 최종 상태 확인

- `netstat -ano | findstr :8000` — 연결 확인
- `curl -I http://localhost:8000/live` — 스트림 응답 확인
- 아래 출력:

```
=== 마이크 스트리밍 테스트 완료 ===
VLC에서 http://localhost:8000/live 재생 →
마이크에 말하면 들림 (지연은 STEP 5로 조정)
==================================
```
