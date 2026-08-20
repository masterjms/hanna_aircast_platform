"""환경 변수 → 설정 객체.

여기가 환경 의존성의 유일한 입구다. 다른 모듈은 os.environ 을 직접 읽지 않는다.
온프레미스 전환 시 바꾸는 값도 전부 여기 모여 있다(DATABASE_URL, MQTT_HOST, FILE_ROOT).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py → backend/ → 리포지토리 루트
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 개발 편의를 위한 자리표시자. 운영에서 이 값이면 기동을 막는다.
DEFAULT_JWT_SECRET = "dev-only-secret-change-me-in-production-32b+"
#: HS256 권장 최소 키 길이 (RFC 7518 §3.2).
MIN_JWT_SECRET_BYTES = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 앱 ──────────────────────────────────────────────
    app_env: str = "dev"
    log_level: str = "INFO"
    public_base_url: str = "http://localhost:8000"
    #: 쉼표로 구분한 문자열로 받는다. pydantic-settings 는 list 타입 환경변수를
    #: JSON 으로 파싱하려 들기 때문에 원문을 받아서 cors_origins 프로퍼티로 편다.
    cors_origins_raw: str = Field(default="", validation_alias="CORS_ORIGINS")

    # ── DB ──────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://xwifi:xwifi-dev-pw@localhost:5432/xwifi"

    # ── 인증 ────────────────────────────────────────────
    #: prod 에서는 아래 _guard_prod_secrets 가 기본값/짧은 값을 거부한다.
    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720

    # ── MQTT ────────────────────────────────────────────
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_tls: bool = False
    mqtt_topic_root: str = "iotradio"

    # ── Icecast (Phase 4) ──────────────────────────────
    icecast_host: str = "localhost"
    icecast_port: int = 8100
    icecast_source_user: str = "source"
    icecast_source_password: str = "hackme"
    #: 단말이 스트림을 받아가는 공개 주소. 마운트가 이 뒤에 붙는다.
    icecast_public_base_url: str = "http://localhost:8100"

    # ── TTS ─────────────────────────────────────────────
    #: polly | dev.  dev 는 ffmpeg 으로 톤을 만드는 가짜 엔진이라 개발에서만 쓴다.
    tts_engine: str = "polly"
    aws_region: str = "ap-northeast-2"

    # ── 파일 저장 ───────────────────────────────────────
    file_root: Path = Path("./data/files")
    download_token_ttl_sec: int = 600
    ota_token_ttl_sec: int = 7200

    # ── 운영 파라미터 ───────────────────────────────────
    device_online_threshold_sec: int = 300
    config_reconcile_interval_sec: int = 3600

    @model_validator(mode="after")
    def _guard_prod_secrets(self) -> Settings:
        """운영에서 기본 시크릿으로 뜨는 사고를 기동 시점에 막는다.

        토큰 위조가 가능해지는 문제라 경고로 넘기지 않고 기동을 실패시킨다.
        """
        if self.app_env != "prod":
            return self
        if self.jwt_secret == DEFAULT_JWT_SECRET:
            raise ValueError(
                "APP_ENV=prod 에서는 JWT_SECRET 을 반드시 교체해야 합니다. "
                'python -c "import secrets;print(secrets.token_urlsafe(48))"'
            )
        if len(self.jwt_secret.encode("utf-8")) < MIN_JWT_SECRET_BYTES:
            raise ValueError(
                f"JWT_SECRET 은 최소 {MIN_JWT_SECRET_BYTES}바이트여야 합니다 "
                f"(현재 {len(self.jwt_secret.encode('utf-8'))}바이트)."
            )
        return self

    @property
    def cors_origins(self) -> list[str]:
        """CORS_ORIGINS=a,b → ["a", "b"]. 비어 있으면 CORS 미들웨어를 아예 안 붙인다."""
        return [item.strip() for item in self.cors_origins_raw.split(",") if item.strip()]

    @property
    def is_prod(self) -> bool:
        return self.app_env == "prod"

    @property
    def upload_dir(self) -> Path:
        return self.file_root / "upload"

    @property
    def tts_dir(self) -> Path:
        return self.file_root / "tts"

    @property
    def update_dir(self) -> Path:
        return self.file_root / "update"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
