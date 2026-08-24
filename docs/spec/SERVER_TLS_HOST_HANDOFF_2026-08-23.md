# 서버 구현 지시서: TLS 전환

원본: `HOST_CONFIRMED_TLS_ROLLOUT_CHECKLIST_2026-08-23.md`의 "2. 서버에서 할 일"을 서버 구현용으로 뽑았다.
단말(P4+C6) 쪽 작업은 별도로 진행 중이며, 아래 내용대로 서버가 구현되면 단말도 붙는다.

서버는 외부 팀이 아니라 같은 프로젝트 안에서 함께 결정하는 부분이라, 아래는 "확인 요청"이 아니라 **확정된 구현 지시**다.

## 1. 인프라 구성 (확정)

**AWS EC2 인스턴스 1대**에 다음을 모두 올린다.

```text
- MQTT 브로커 (mosquitto 등), mqtts 리스너 (기본 8883)
- Icecast, https로 노출 (nginx 리버스 프록시 등으로 TLS 종단)
- 파일 다운로드 서버, https로 노출 (nginx 등)
```

ALB/NLB/ACM 같은 AWS 관리형 로드밸런서 인증서 방식은 쓰지 않는다. MQTT는 TCP 패스스루가 필요해 구성이 늘어나고, 지금 규모(단일 서버, 소규모 커뮤니티 방송)에는 과하다. EC2 인스턴스에 직접 인증서 파일을 두고 mosquitto/nginx 양쪽에 물리는 방식으로 간다.

- 실제 도메인 확정 및 DNS 설정(해당 EC2로 연결).
- 보안그룹에서 mqtts(8883), https(443 또는 지정 포트) 오픈.

## 2. URL 구조 (확정: 서브도메인 없이 단일 도메인 + path)

`live.xxxxxx.co.kr`, `file.xxxxxx.co.kr` 같은 서브도메인 방식은 쓰지 않는다. **도메인 하나 + path**로 서비스를 구분한다.

이유:

- 서브도메인 방식은 와일드카드 인증서(`*.xxxxxx.co.kr`)가 필요하고, Let's Encrypt로 와일드카드를 받으려면 DNS-01 인증(도메인 등록업체 API 키를 서버에 넣어 TXT 레코드 자동 생성)이 필요해 설정이 한 단계 늘어난다. 단일 도메인 + path 방식은 일반 인증서 + HTTP-01 인증으로 충분하다.
- 지금은 모든 서비스가 EC2 한 대에 몰려 있어(§1) 서브도메인으로 나눠도 실질적으로 같은 IP를 가리킨다. 서브도메인이 유리해지는 시점은 나중에 Icecast/파일서버를 다른 서버로 분리할 때다 — 지금은 아니다.

`xxxxxx.co.kr`로 확정됐다고 가정한 예시:

```text
[MQTT]
mqtts://xxxxxx.co.kr:8883
(경로 없음. 토픽은 iotradio/device/<mac_nocolon>/cmd 등으로 MQTT 프로토콜 안에서 별도로 오간다)

[Icecast 실시간 방송 - 지금 테스트 중인 고정 mount, icecast_base_url 기본값]
https://xxxxxx.co.kr/live

[Icecast 실시간 방송 - 다음 단계 마을별 mount, 서버가 LIVE_START.stream_url로 매번 완성해서 전달]
https://xxxxxx.co.kr/live/00000001/9      (마을 00000001, job_id 9)
https://xxxxxx.co.kr/live/all/9           (전체 방송, job_id 9)

[파일 다운로드 - 서버가 FILE_START.https_url로 매번 완성해서 전달]
https://xxxxxx.co.kr/file/notice-1780000001-W.mp3
```

포트 표기: mqtts는 `:8883`을 명시(기존 코드 관례), https 쪽(Icecast/파일)은 nginx가 443으로 받아 내부적으로 지금 테스트 포트(8000, 9002)로 프록시하므로 클라이언트가 보는 URL에는 포트를 붙이지 않는다.

## 3. 인증서 (확정: Let's Encrypt)

**CA는 Let's Encrypt로 확정한다.** EC2 인스턴스에서 certbot으로 발급/자동 갱신한다.

- 도메인 하나에 인증서 1개를 발급해서 mosquitto(mqtts)와 nginx(https, Icecast/파일서버) **양쪽에 동일하게 적용**한다. 서비스별로 다른 CA/인증서를 쓸 이유가 없다.
- certbot 자동 갱신을 반드시 cron/systemd timer로 설정한다. Let's Encrypt 인증서는 유효기간 90일이라, 갱신이 안 되면 만료 시 모든 단말이 한꺼번에 연결 실패한다.
- mosquitto/nginx 설정 모두 **체인 전체(`fullchain.pem`)**를 물린다. 리프 인증서만 물리면 중간 인증서가 빠져서 단말이 신뢰 경로를 완성하지 못할 수 있다.
- 단말 쪽은 Let's Encrypt의 루트 인증서(ISRG Root X1)만 펌웨어에 pinning한다 — CA가 안 바뀌는 한(리프 인증서가 90일마다 갱신돼도) 단말 쪽 재작업은 필요 없다.

## 4. URL 발행 방식 변경

- `LIVE_START.stream_url`, `FILE_START.https_url`을 지금처럼 `http://192.168.0.x` 테스트값이 아니라 실제 `https://` URL로 발행하도록 서버 로직 반영.
- `SERVER_VILLAGE_STREAM_URL_POLICY_2026-08-21.md` §7에 이미 남겨진 미해결 사항(서버측 동적 mount 생성 로직, 마을/전체 방송 mount 분리)도 이 시점에 같이 처리하는 게 자연스럽다 — 지금 PC 테스트 구성(`Icecast_시작.bat`, 고정 mount)은 그대로 못 쓴다.

## 5. 단말에 최종 전달해야 할 값

- broker_url: `mqtts://<host>:8883` 형태의 완성된 문자열
- icecast_base_url: `https://<host>/live` 형태의 완성된 문자열 (마을별 mount 분리 전 fallback 기본값, §2 예시 참고)
- 위 값들은 단말 NVS에 그대로 저장되는 값이므로, 최종 확정 전까지는 여러 번 안 바뀌는 게 좋다(바뀔 때마다 단말 재설정 필요).

## 6. 참고

- 단말은 host 외 다른 부분은 바꾸지 않는다 — scheme(`mqtts://`, `https://`)과 URL 경로 구조는 이미 단말 코드에 고정돼 있다.
- TLS 적용 이후 단말은 평문(`mqtt://`, `http://`)으로 폴백하지 않는다. 인증서 문제가 있으면 연결 실패로 처리되니, 3절의 인증서 요건(Let's Encrypt, 자동 갱신, 전체 체인)을 꼭 지켜달라.
