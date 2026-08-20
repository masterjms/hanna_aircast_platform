"""TTS 엔진.

엔진을 갈아끼울 수 있게 프로토콜로 두었다. 두 가지 이유가 있다:

  1. 온프레미스(폐쇄망) 배포에서는 Polly 를 부를 수 없다. 그때 로컬 엔진으로
     바꿔 끼울 자리가 필요하다.
  2. AWS 자격증명 없이도 파이프라인 전체를 돌려볼 수 있어야 한다.
     (TTS_ENGINE=dev)

엔진은 mp3 바이트만 돌려준다. 캐시·파일 저장·DB 기록은 service.py 가 한다 —
엔진이 저장까지 알면 엔진을 바꿀 때마다 그 코드를 다시 써야 한다.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

from app.config import settings
from app.errors import ApiError
from app.tts.voices import Voice

log = logging.getLogger(__name__)

#: 단말이 받는 mp3 파라미터. 업로드 파일과 맞춰 P4 디코더가 한 가지만 다루게 한다.
OUTPUT_SAMPLE_RATE = 22_050
OUTPUT_BITRATE = "64k"


class TtsUnavailable(ApiError):
    status_code = 503
    code = "TTS_UNAVAILABLE"
    message = "음성 합성 엔진을 사용할 수 없습니다."


class TtsEngine(Protocol):
    """텍스트 → mp3 바이트."""

    name: str

    def synthesize(self, text: str, voice: Voice) -> bytes: ...


class PollyEngine:
    """AWS Polly.

    자격증명은 boto3 기본 체인을 따른다 — 환경변수, ~/.aws/credentials,
    EC2 인스턴스 역할 순이다. 운영에서는 인스턴스 역할을 쓰는 게 가장 안전하다
    (키를 서버에 두지 않는다).
    """

    name = "polly"

    def __init__(self) -> None:
        try:
            import boto3
        except ImportError as exc:  # pragma: no cover - 설치돼 있어야 정상
            raise TtsUnavailable("boto3 가 설치되어 있지 않습니다.") from exc

        self._client = boto3.client("polly", region_name=settings.aws_region)

    def synthesize(self, text: str, voice: Voice) -> bytes:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            # Polly 가 22050Hz mp3 를 바로 내준다. 리샘플링을 한 번 아낀다.
            res = self._client.synthesize_speech(
                Text=text,
                VoiceId=voice.id,
                LanguageCode=voice.language,
                Engine=voice.engine,
                OutputFormat="mp3",
                SampleRate=str(OUTPUT_SAMPLE_RATE),
            )
            return res["AudioStream"].read()
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in {"InvalidSsmlException", "TextLengthExceededException"}:
                raise ApiError(
                    "합성할 수 없는 문구입니다. 길이와 특수문자를 확인해 주세요.",
                    code="TTS_INVALID_TEXT",
                ) from exc
            # 자격증명·권한 문제는 운영자가 봐야 하므로 원문을 남긴다.
            log.error("Polly 호출 실패 (%s): %s", code, exc)
            detail = code or "알 수 없는 오류"
            raise TtsUnavailable(f"Polly 호출에 실패했습니다. ({detail})") from exc
        except BotoCoreError as exc:
            log.error("Polly 연결 실패: %s", exc)
            raise TtsUnavailable("Polly 에 연결하지 못했습니다. 네트워크를 확인해 주세요.") from exc


class DevEngine:
    """개발·테스트용 가짜 엔진.

    ffmpeg 로 글자 수에 비례하는 길이의 톤을 만든다. 내용은 의미가 없지만
    업로드 → 캐시 → 방송 → 단말 다운로드 흐름을 AWS 없이 끝까지 돌려볼 수 있다.

    ⚠ 운영에서는 절대 쓰지 않는다. TTS_ENGINE=dev 를 prod 에서 켜면 안내방송
      대신 삐 소리가 마을에 나간다.
    """

    name = "dev"

    def synthesize(self, text: str, voice: Voice) -> bytes:
        exe = shutil.which("ffmpeg")
        if exe is None:
            raise TtsUnavailable(
                "개발용 TTS 엔진에는 ffmpeg 이 필요합니다. TTS_ENGINE=polly 로 바꾸거나 "
                "ffmpeg 을 설치해 주세요."
            )

        # 한국어는 글자당 약 0.25초로 읽힌다. 대략만 맞춘다.
        seconds = max(1.0, min(60.0, len(text) * 0.25))
        # 보이스마다 다른 음정을 줘서 어느 보이스로 만들었는지 귀로 구분된다.
        freq = 320 + (sum(ord(c) for c in voice.id) % 6) * 40

        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "tts.mp3"
            subprocess.run(
                [exe, "-y", "-f", "lavfi",
                 "-i", f"sine=frequency={freq}:duration={seconds:.2f}",
                 "-ar", str(OUTPUT_SAMPLE_RATE), "-ac", "1", "-b:a", OUTPUT_BITRATE,
                 str(out)],
                capture_output=True, check=True, timeout=60,
            )
            return out.read_bytes()


def normalize_mp3(raw: bytes) -> bytes:
    """엔진 출력을 단말이 기대하는 mp3 파라미터로 맞춘다.

    ffmpeg 이 없으면 원본을 그대로 쓴다 — 길이 계산과 같은 방침이다.
    Polly 는 이미 22050Hz mono 를 주므로 실제로는 비트레이트만 정리된다.
    """
    exe = shutil.which("ffmpeg")
    if exe is None:
        log.info("ffmpeg 없음 — TTS 출력을 그대로 사용한다")
        return raw

    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.mp3", Path(tmp) / "out.mp3"
        src.write_bytes(raw)
        try:
            subprocess.run(
                [exe, "-y", "-i", str(src),
                 "-ar", str(OUTPUT_SAMPLE_RATE), "-ac", "1", "-b:a", OUTPUT_BITRATE,
                 str(dst)],
                capture_output=True, check=True, timeout=60,
            )
            return dst.read_bytes()
        except Exception:  # noqa: BLE001 - 정규화 실패가 합성 실패는 아니다
            log.warning("mp3 정규화 실패 — 원본을 사용한다")
            return raw


def get_engine() -> TtsEngine:
    """설정에 맞는 엔진을 만든다."""
    name = settings.tts_engine.lower()
    if name == "dev":
        if settings.is_prod:
            # 운영에서 삐 소리가 나가는 사고를 기동 시점이 아니라 호출 시점에라도 막는다.
            raise TtsUnavailable("운영 환경에서는 개발용 TTS 엔진을 쓸 수 없습니다.")
        return DevEngine()
    if name == "polly":
        return PollyEngine()
    raise TtsUnavailable(f"알 수 없는 TTS 엔진입니다: {settings.tts_engine}")
