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
from app.constants import AUDIO_CHANNELS, AUDIO_SAMPLE_RATE
from app.errors import ApiError
from app.tts.voices import Voice

log = logging.getLogger(__name__)

#: 단말이 받는 mp3 파라미터. 업로드 파일과 맞춰 P4 디코더가 한 가지만 다루게 한다.
#: 표본율·채널은 통신 사양 고정이고, 비트레이트만 설정에서 고른다(문제점 30번).
#: 이 값이 어긋나면 단말 디코더가 시작 지점에서 잡음("퍽")을 낸다.
OUTPUT_SAMPLE_RATE = AUDIO_SAMPLE_RATE
#: 설정을 못 읽는 경로(개발용 엔진 등)에서 쓰는 기본값.
DEFAULT_BITRATE_KBPS = 24

#: mp3 muxer 에 항상 붙이는 옵션.
#:
#: `-write_xing 0` — LAME 은 파일 맨 앞에 Xing/Info 헤더 프레임을 하나 넣는다. 16kHz
#:   16kbps 프레임은 72바이트뿐이라 태그가 안 들어가서, **그 프레임만 40kbps 로 올려**
#:   180바이트를 만든다. 오디오는 16kbps 가 맞는데 파일의 첫 프레임이 40kbps 라고
#:   말하게 되고, 첫 프레임만 읽는 도구(탐색기 속성 등)는 40kbps 라고 보고한다
#:   (2026-09-07 단말팀 보고 — 문제점 30번. 24kbps 로 뽑던 때도 첫 프레임은 40이었다).
#:   CBR 이라 Xing 이 없어도 길이는 크기 ÷ 비트레이트로 나온다. 대신 LAME tag 가 알려주던
#:   인코더 지연(약 0.1초 무음)이 다듬어지지 않고 그대로 남는다 — 안내방송에서는 들리지 않고
#:   재생 길이도 0.1초만 길게 잡힌다. 첫 프레임이 거짓말하는 쪽이 더 나쁘다.
#: `-id3v2_version 0` — `-map_metadata -1` 로도 44바이트짜리 빈 ID3v2 껍데기가 남는다.
#:   "변환 시 내부 tag 등 정보는 전부 삭제"(문제점 31번)를 글자대로 지킨다.
MP3_MUXER_ARGS = ["-write_xing", "0", "-id3v2_version", "0"]

#: mp3 를 만드는 **방식**이 바뀔 때마다 올린다. TTS 캐시 키에 들어가므로, 올리면
#: 예전 방식으로 만든 캐시 파일이 자동으로 무효가 되고 다음 합성 때 새로 만든다.
#:
#: 이게 없으면 방식을 고쳐도 이미 만들어 둔 파일이 계속 나간다 — 같은 문구·언어·
#: 보이스·비트레이트면 키가 같기 때문이다. 2026-09-09 Xing 수정이 현장에서 안 보이던
#: 이유가 이것이다(문제점 30번).
#:
#:   1 → 2  Xing 헤더 프레임과 빈 ID3v2 제거 (2026-09-09)
MP3_FORMAT_VERSION = 2

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
                 "-ar", str(OUTPUT_SAMPLE_RATE), "-ac", str(AUDIO_CHANNELS),
                 "-b:a", f"{DEFAULT_BITRATE_KBPS}k", *MP3_MUXER_ARGS, str(out)],
                capture_output=True, check=True, timeout=60,
            )
            return out.read_bytes()


def normalize_mp3(raw: bytes, bitrate_kbps: int = DEFAULT_BITRATE_KBPS) -> bytes:
    """엔진 출력을 단말이 기대하는 mp3 파라미터로 맞춘다.

    16kHz · mono 는 고정이고 비트레이트는 설정값을 받는다(문제점 30번). 어긋난
    파일을 보내면 단말 디코더가 재생 시작 지점에서 "퍽" 하는 잡음을 낸다.

    시작부에 짧은 페이드인도 넣는다 — 첫 프레임부터 최대 진폭이 나오면 앰프가
    켜지는 순간과 겹쳐 같은 증상이 남는다.

    **실패하면 원본을 쓰지 않고 던진다.** 예전에는 ffmpeg 이 없거나 변환이 실패하면
    합성 엔진 출력을 그대로 파일함에 넣고 로그 한 줄만 남겼다. 그러면 규격 밖 파일이
    아무도 모르게 단말로 나간다 — 업로드는 규격 밖 파일을 거절하는데(문제점 31번)
    TTS 만 통과시킬 이유가 없다. 만들고 나서 실제 파라미터도 다시 재서 확인한다.
    """
    exe = shutil.which("ffmpeg")
    if exe is None:
        raise TtsUnavailable(
            "오디오 변환 도구(ffmpeg)가 없어 방송 규격에 맞는 음성을 만들 수 없습니다."
        )

    with tempfile.TemporaryDirectory() as tmp:
        src, dst = Path(tmp) / "in.mp3", Path(tmp) / "out.mp3"
        src.write_bytes(raw)
        try:
            subprocess.run(
                [exe, "-y", "-i", str(src),
                 "-af", f"afade=t=in:st=0:d={FADE_IN_SEC}",
                 # 합성 엔진이 붙인 tag 를 남기지 않는다(문제점 31번과 같은 방침).
                 "-map_metadata", "-1",
                 "-ar", str(OUTPUT_SAMPLE_RATE), "-ac", str(AUDIO_CHANNELS),
                 "-b:a", f"{bitrate_kbps}k", *MP3_MUXER_ARGS, str(dst)],
                capture_output=True, check=True, timeout=60,
            )
        except Exception as exc:  # noqa: BLE001
            log.exception("mp3 정규화 실패")
            raise TtsUnavailable("음성을 방송 규격으로 변환하지 못했습니다.") from exc

        out = dst.read_bytes()
        _assert_spec(dst, bitrate_kbps)
        return out


def _assert_spec(path: Path, bitrate_kbps: int) -> None:
    """만든 파일이 정말 그 규격인지 다시 잰다.

    "설정은 16인데 파일은 40" 같은 신고가 다시 오면 여기서 먼저 걸린다. 인코더가
    조용히 다른 값을 쓰거나 옵션이 무시되는 경우를 눈으로 확인하지 않고 잡는다.
    ffprobe 가 없으면 검사를 건너뛴다 — 도구가 없다고 합성을 막지는 않는다.
    """
    from app.modules.file.service import probe_audio  # 순환 import 회피

    spec = probe_audio(path)
    if spec is None:
        log.info("ffprobe 없음 — 합성 결과 규격 검사를 건너뛴다")
        return
    ok = (
        spec.sample_rate == AUDIO_SAMPLE_RATE
        and spec.channels == AUDIO_CHANNELS
        and abs(spec.kbps - bitrate_kbps) <= 1
    )
    if not ok:
        log.error(
            "합성 결과가 규격과 다르다: 기대 %dkHz mono %dkbps, 실제 %s",
            AUDIO_SAMPLE_RATE // 1000, bitrate_kbps, spec.describe(),
        )
        raise TtsUnavailable(
            f"만들어진 음성이 방송 규격과 다릅니다 (기대 "
            f"{AUDIO_SAMPLE_RATE // 1000}kHz mono {bitrate_kbps}kbps, 실제 {spec.describe()})."
        )


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
