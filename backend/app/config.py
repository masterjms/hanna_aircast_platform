"""환경 변수 → 설정 객체.

여기가 환경 의존성의 유일한 입구다. 다른 모듈은 os.environ 을 직접 읽지 않는다.
온프레미스 전환 시 바꾸는 값도 전부 여기 모여 있다(DATABASE_URL, MQTT_HOST, FILE_ROOT).

프로파일
--------
`.env` 를 먼저 읽고, XWIFI_PROFILE 이 있으면 `.env.<프로파일>` 을 덮어쓴다.

    XWIFI_PROFILE=local    목 단말로 개발 (전부 localhost)
    XWIFI_PROFILE=device   실물 단말과 통신 (주소가 이 PC 의 LAN IP 여야 한다)

같은 파일 하나를 두 용도로 고쳐 쓰다 보면, 목 단말로 시험할 때 주소가 남의 PC 를
가리키거나 그 반대가 된다 — 실제로 겪었다. 파일을 나눠두면 그럴 일이 없다.
"""

from __future__ import annotations

import os
import socket
from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py → backend/ → 리포지토리 루트
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 주소 대신 이 값을 쓰면 기동 시점에 이 PC 의 LAN IP 로 바꿔 넣는다.
#: 실물 단말 테스트에서 PC 마다 IP 를 손으로 고치지 않으려는 것이다.
AUTO_HOST = "auto"


def _profile_env_files() -> tuple[Path, ...]:
    """읽을 .env 목록. 뒤에 오는 파일이 앞을 덮어쓴다."""
    files = [REPO_ROOT / ".env"]
    profile = os.getenv("XWIFI_PROFILE", "").strip()
    if profile:
        files.append(REPO_ROOT / f".env.{profile}")
    return tuple(files)


def detect_lan_ip() -> str:
    """이 PC 가 바깥으로 나갈 때 쓰는 인터페이스의 IPv4.

    실제로 패킷을 보내지는 않는다 — UDP 소켓에 connect 만 하면 커널이
    라우팅 테이블을 보고 출발지 주소를 정해준다. 인터페이스를 일일이
    뒤지는 것보다 정확하다(가상 어댑터·VPN 을 안 고른다).
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            # 8.8.8.8 로 실제 통신하지 않는다. 경로만 물어보는 것이다.
            sock.connect(("8.8.8.8", 53))
            return str(sock.getsockname()[0])
        except OSError:
            return "127.0.0.1"

#: 개발 편의를 위한 자리표시자. 운영에서 이 값이면 기동을 막는다.
DEFAULT_JWT_SECRET = "dev-only-secret-change-me-in-production-32b+"
#: HS256 권장 최소 키 길이 (RFC 7518 §3.2).
MIN_JWT_SECRET_BYTES = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_profile_env_files(),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 앱 ──────────────────────────────────────────────
    app_env: str = "dev"
    log_level: str = "INFO"
    #: 백엔드가 듣는 포트. Icecast 가 8000 을 쓰므로 8080 으로 비켜났다.
    app_port: int = 8080
    #: 바인딩 주소. 127.0.0.1 이면 이 PC 안에서만 닿는다.
    #: 실물 단말이 파일을 받아가려면 0.0.0.0 이어야 한다 — 방화벽을 열어도
    #: 여기가 localhost 면 소용없다. 연결 요청 자체를 안 받기 때문이다.
    app_host: str = "127.0.0.1"
    public_base_url: str = "http://localhost:8080"
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
    #: 이행기 한시 — 단말 공유 계정(xwifi-device)의 비밀번호. 값이 있으면 passwd
    #: 재생성 때 공유 계정을 유지한다. 전 단말이 단말별 계정으로 넘어가면 .env 에서
    #: 지운다 → 다음 재생성 때 공유 계정이 사라지고 단말별 계정만 남는다.
    mqtt_device_password: str | None = None
    #: 단말별 MQTT 계정 passwd 파일을 내보낼 경로(공유 볼륨). 비우면 기능 꺼짐.
    #: 운영 compose 가 /var/lib/iotradio/mqtt/passwd.generated 로 지정한다.
    mosquitto_passwd_export: str | None = None

    # ── Icecast (Phase 4) ──────────────────────────────
    icecast_host: str = "localhost"
    icecast_port: int = 8100
    icecast_source_user: str = "source"
    icecast_source_password: str = "hackme"
    #: 단말이 스트림을 받아가는 공개 주소. 마운트가 이 뒤에 붙는다.
    icecast_public_base_url: str = "http://localhost:8100"

    # ── 카카오 (지도 · 지오코딩) ────────────────────────
    #: 주소 검색(주소→좌표+법정동코드) 프록시용. 서버 전용 비밀값 — 비우면 검색 비활성.
    kakao_rest_api_key: str | None = None
    #: 지도 SDK 용 공개 키(도메인 등록으로 보호). /api/dashboard/map 이 내려준다.
    kakao_js_key: str | None = None

    # ── TTS ─────────────────────────────────────────────
    #: google | dev.  dev 는 ffmpeg 으로 톤을 만드는 가짜 엔진이라 개발에서만 쓴다.
    #: "google" = Google Cloud TTS / "dev" = ffmpeg 톤 (자격증명 없이 시험할 때)
    tts_engine: str = "google"
    #: 서비스 계정 JSON 경로. google-auth 는 같은 이름의 **환경변수**만 읽으므로,
    #: .env 에 적은 값을 아래 validator 가 os.environ 으로 옮겨준다.
    #: 비워두면 google-auth 기본 체인(gcloud 로그인, 인스턴스 서비스 계정)을 쓴다.
    google_application_credentials: str = ""

    # ── 파일 저장 ───────────────────────────────────────
    file_root: Path = Path("./data/files")
    download_token_ttl_sec: int = 600
    ota_token_ttl_sec: int = 7200

    # ── 운영 파라미터 ───────────────────────────────────
    device_online_threshold_sec: int = 300
    config_reconcile_interval_sec: int = 3600
    #: 실시간 방송 시작 후 이 시간(초) 안에 마이크 업링크가 안 붙으면 자동 종료한다.
    #: 무음이 "정상 방송"처럼 나가는 게 최악이라 기본으로 켠다. 0 이면 끈다.
    live_uplink_grace_sec: int = 30

    @model_validator(mode="after")
    def _export_google_credentials(self) -> Settings:
        """.env 의 자격증명 경로를 실제 환경변수로 내보낸다.

        google-auth 는 Settings 객체를 모르고 os.environ 만 본다. 이걸 안 하면
        .env 에 경로를 적어도 "자격증명을 찾지 못했습니다" 로 실패한다 —
        설정 파일에 적었으니 될 거라 믿게 되는, 찾기 어려운 함정이다.

        이미 환경변수가 있으면 건드리지 않는다. 운영에서 인스턴스에 붙인
        서비스 계정을 쓰는 경우 이 값이 비어 있는 게 정상이다.
        """
        path = self.google_application_credentials.strip()
        if path and not os.getenv("GOOGLE_APPLICATION_CREDENTIALS"):
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path
        return self

    @model_validator(mode="after")
    def _resolve_auto_hosts(self) -> Settings:
        """주소에 들어간 `auto` 를 이 PC 의 LAN IP 로 바꾼다.

        단말은 localhost 로 서버에 올 수 없다 — 단말 입장에서 자기 자신이 된다.
        그렇다고 PC 마다 IP 를 손으로 적으면 노트북과 데스크톱을 오갈 때마다
        틀린다. `auto` 를 쓰면 뜨는 PC 에 맞춰 알아서 채워진다.
        """
        lan = None
        for field in ("public_base_url", "icecast_public_base_url"):
            value = getattr(self, field)
            if AUTO_HOST not in value:
                continue
            if lan is None:
                lan = detect_lan_ip()
            object.__setattr__(self, field, value.replace(AUTO_HOST, lan))
        return self

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
