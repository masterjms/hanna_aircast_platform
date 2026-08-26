# 사양서 기준 Python 3.12. 로컬 개발은 3.10 이어도 코드가 호환된다.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# ffmpeg: TTS 후처리(-ar 22050 -ac 1 -b:a 64k)와 업로드 파일 길이 측정에 쓴다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

# 의존성 레이어를 소스와 분리해서 코드만 바뀔 때 재설치하지 않게 한다.
COPY pyproject.toml ./
# ⚠ 이 목록은 pyproject.toml 의 dependencies + optional-dependencies.tts 와 같아야 한다.
#   따로 들고 있는 이유는 레이어 캐시다 — app/ 복사 전에 설치해야 코드가 바뀔 때마다
#   의존성을 다시 깔지 않는다. 어긋나면 운영에서만 ImportError 가 난다
#   (2026-08-26 google-cloud-texttospeech 누락으로 실제 발생).
#   scripts/check_deps.py 가 CI 에서 어긋남을 잡는다.
RUN pip install --no-cache-dir \
      "fastapi>=0.115" "uvicorn[standard]>=0.32" "pydantic>=2.9" "pydantic-settings>=2.6" \
      "sqlalchemy[asyncio]>=2.0.36" "asyncpg>=0.30" "alembic>=1.14" "aiomqtt>=2.3" \
      "apscheduler>=3.10,<4" "pyjwt>=2.10" "bcrypt>=4.2" "python-multipart>=0.0.17" \
      "google-cloud-texttospeech>=2.16"

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app

# 파일 스토리지 마운트 지점. compose 에서 볼륨을 붙인다.
RUN mkdir -p /var/lib/iotradio/files

# root 로 돌리지 않는다.
RUN useradd --system --uid 10001 xwifi \
    && chown -R xwifi:xwifi /srv /var/lib/iotradio
USER xwifi

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
