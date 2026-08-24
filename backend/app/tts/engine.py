"""TTS 엔진.

엔진을 갈아끼울 수 있게 프로토콜로 두었다. 두 가지 이유가 있다:

  1. 온프레미스(폐쇄망) 배포에서는 Google TTS 를 부를 수 없다. 그때 로컬
     엔진으로 바꿔 끼울 자리가 필요하다.
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
#: 단말이 기대하는 mp3 파라미터 — 통신 사양의 오디오 규격(16kHz · mono · 24kbps).
#: 이 값이 어긋나면 단말 디코더가 시작 지점에서 잡음("퍽")을 낸다.
OUTPUT_SAMPLE_RATE = 16_000
OUTPUT_BITRATE = "24k"

#: 재생 시작 직후의 팝 노이즈를 없애는 짧은 페이드인(초).
#: 파일 첫 프레임부터 최대 진폭이 나오면 앰프가 켜지는 순간과 겹쳐 "퍽" 소리가 난다.
#: 20ms 면 귀에 들리지 않으면서 그 전이를 부드럽게 만든다.
FADE_IN_SEC = 0.02


class TtsUnavailable(ApiError):
    status_code = 503
    code = "TTS_UNAVAILABLE"
    message = "음성 합성 엔진을 사용할 수 없습니다."


class TtsEngine(Protocol):
    """텍스트 → mp3 바이트."""

    name: str

    def synthesize(self, text: str, voice: Voice) -> bytes: ...


class GoogleEngine:
    """Google Cloud Text-to-Speech.

    자격증명은 google-auth 기본 체인을 따른다:
        1. GOOGLE_APPLICATION_CREDENTIALS 가 가리키는 서비스 계정 JSON
        2. gcloud CLI 로 로그인한 사용자 자격증명
        3. GCE/GKE/Cloud Run 의 메타데이터 서버(붙어 있는 서비스 계정)

    운영에서는 3번(인스턴스에 붙인 서비스 계정)이 가장 안전하다 — 키 파일을
    서버에 두지 않는다. 개발 PC 에서는 1번이 편하다.

    필요한 권한은 `roles/cloudtts.user` 하나다.
    """

    name = "google"

    def __init__(self) -> None:
        try:
            from google.cloud import texttospeech
        except ImportError as exc:  # pragma: no cover - 설치돼 있어야 정상
            raise TtsUnavailable(
                "google-cloud-texttospeech 가 설치되어 있지 않습니다. "
                "backend 에서 pip install google-cloud-texttospeech 를 실행하세요."
            ) from exc

        self._tts = texttospeech
        try:
            self._client = texttospeech.TextToSpeechClient()
        except Exception as exc:  # noqa: BLE001 - 자격증명 실패를 그대로 보여준다
            log.error("Google TTS 클라이언트 생성 실패: %s", exc)
            raise TtsUnavailable(
                "Google TTS 자격증명을 찾지 못했습니다. "
                "GOOGLE_APPLICATION_CREDENTIALS 를 서비스 계정 JSON 경로로 지정하세요."
            ) from exc

    def synthesize(self, text: str, voice: Voice) -> bytes:
        from google.api_core import exceptions as gexc

        tts = self._tts
        try:
            res = self._client.synthesize_speech(
                input=tts.SynthesisInput(text=text),
                # voice.name 안에 언어·품질·성별이 다 들어 있어서
                # ssml_gender 를 따로 넘기지 않는다.
                voice=tts.VoiceSelectionParams(
                    language_code=voice.language, name=voice.id
                ),
                audio_config=tts.AudioConfig(
                    audio_encoding=tts.AudioEncoding.MP3,
                    # 단말 디코더에 맞춘 값. 여기서 맞춰두면 리샘플링을 한 번 아낀다.
                    sample_rate_hertz=OUTPUT_SAMPLE_RATE,
                ),
            )
            return res.audio_content
        except gexc.InvalidArgument as exc:
            # 문구가 너무 길거나 SSML 이 깨진 경우다. 사용자가 고칠 수 있다.
            raise ApiError(
                "합성할 수 없는 문구입니다. 길이와 특수문자를 확인해 주세요.",
                code="TTS_INVALID_TEXT",
            ) from exc
        except (gexc.Unauthenticated, gexc.PermissionDenied) as exc:
            log.error("Google TTS 권한 오류: %s", exc)
            raise TtsUnavailable(
                "Google TTS 권한이 없습니다. 서비스 계정에 Cloud TTS 사용 권한이 "
                "있는지, 프로젝트에서 API 가 켜져 있는지 확인해 주세요."
            ) from exc
        except gexc.ResourceExhausted as exc:
            log.error("Google TTS 할당량 초과: %s", exc)
            raise TtsUnavailable(
                "Google TTS 할당량을 초과했습니다. 잠시 후 다시 시도해 주세요."
            ) from exc
        except gexc.GoogleAPICallError as exc:
            log.error("Google TTS 호출 실패: %s", exc)
            raise TtsUnavailable(f"Google TTS 호출에 실패했습니다. ({exc.message})") from exc
        except Exception as exc:  # noqa: BLE001 - 네트워크 단절 등
            log.error("Google TTS 연결 실패: %s", exc)
            raise TtsUnavailable(
                "Google TTS 에 연결하지 못했습니다. 네트워크를 확인해 주세요."
            ) from exc


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
                "개발용 TTS 엔진에는 ffmpeg 이 필요합니다. TTS_ENGINE=google 로 바꾸거나 "
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

    통신 사양의 오디오 규격(16kHz · mono · 24kbps)으로 맞춘다. 어긋난 파일을 보내면
    단말 디코더가 재생 시작 지점에서 "퍽" 하는 잡음을 낸다.

    시작부에 짧은 페이드인도 넣는다 — 첫 프레임부터 최대 진폭이 나오면 앰프가
    켜지는 순간과 겹쳐 같은 증상이 남는다.

    ffmpeg 이 없으면 원본을 그대로 쓴다 — 길이 계산과 같은 방침이다.
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
                 "-af", f"afade=t=in:st=0:d={FADE_IN_SEC}",
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
    if name == "google":
        return GoogleEngine()
    raise TtsUnavailable(f"알 수 없는 TTS 엔진입니다: {settings.tts_engine}")
