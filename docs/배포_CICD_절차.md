# 배포 · CI/CD 절차

도메인 `hanna-aircast.co.kr` / 인스턴스 `i-0c54ab6e287cf26d0`

---

## 1. 서버 접속

```bash
cd ~/Downloads
```

```bash
chmod 400 hanna-aircast.pem
```

```bash
ssh -i hanna-aircast.pem ubuntu@13.124.26.85
```

프롬프트가 `ubuntu@ip-...:~$` 로 바뀌면 서버 안이다.

```bash
cd ~/xwifi-server
```

---

## 2. 배포

```bash
git pull
```

```bash
bash scripts/deploy.sh
```

끝. 스크립트가 방송 확인 → 빌드 → 마이그레이션 → 교체 → nginx 재시작 → 헬스체크까지 한다.

### 옵션

```bash
bash scripts/deploy.sh --force
```
방송 중이어도 진행 (방송이 끊긴다)

```bash
bash scripts/deploy.sh --no-pull
```
코드 그대로 재빌드만

### 되돌리기

배포 실패 시 화면에 나오는 명령을 그대로 실행한다:

```bash
git checkout <이전SHA> && bash scripts/deploy.sh --no-pull
```

---

## 3. 상태 확인

```bash
docker compose ps
```

```bash
curl -s https://hanna-aircast.co.kr/health
```

`{"status":"ok","database":true,"mqtt":true}` 가 정상.

```bash
docker compose logs backend --tail 50
```

```bash
docker compose logs -f backend
```

```bash
docker compose logs mosquitto --tail 50
```

---

## 4. 자주 쓰는 명령

```bash
docker compose restart nginx
```

```bash
docker compose restart backend
```

```bash
docker compose exec backend python -m alembic upgrade head
```

```bash
docker compose exec backend python -m app.seed
```

```bash
docker compose exec backend python -m alembic current
```

---

## 5. CI (GitHub Actions)

푸시하면 자동 실행. 설정 불필요.

| 잡 | 내용 |
|---|---|
| backend | ruff · pytest · 의존성 동기화 |
| migration | upgrade → downgrade → upgrade |
| frontend | npm run build |
| stack | compose 전체 기동 + health 확인 |

결과: GitHub 저장소 → **Actions** 탭

로컬에서 미리 확인:

```bash
cd backend && .venv/Scripts/python -m ruff check app tests
```

```bash
cd backend && .venv/Scripts/python -m pytest tests -q
```

```bash
python scripts/check_deps.py
```

```bash
cd frontend && npm run build
```

---

## 6. CD (다음 단계, 미구축)

1. EC2 에 IAM 역할 부여 (`AmazonSSMManagedInstanceCore`)
2. CI 가 이미지를 GHCR 에 `:<커밋sha>` 로 푸시
3. 릴리스 태그 → GitHub Environment 승인 대기
4. SSM Run Command 로 서버에서 `deploy.sh` 실행

---

## 7. 최초 구축 시에만 (재실행 불필요)

```bash
sudo bash scripts/setup-certs.sh hanna-aircast.co.kr <이메일>
```

```bash
bash scripts/setup-mqtt-users.sh
```

```bash
sudo bash scripts/setup-tts-credentials.sh
```

인증서 갱신 동작 확인:

```bash
sudo certbot renew --dry-run
```
