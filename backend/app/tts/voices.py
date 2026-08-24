"""TTS 보이스 목록.

화면의 드롭다운과 서버 검증이 같은 목록을 봐야 해서 여기 한 곳에 둔다.
**Google Cloud Text-to-Speech 기준**이다(2026-08 AWS Polly 에서 전환).

보이스 이름이 곧 Google API 의 `voice.name` 이다. 이름 안에 언어·품질·성별이
전부 들어 있어서(`ko-KR-Neural2-A`) 따로 성별을 넘길 필요가 없다.

품질 등급:
    Neural2   가장 자연스럽다. 한국어·영어·일본어에 있다.
    Wavenet   그다음. Neural2 가 없는 언어(중국어)에 쓴다.
    Standard  가장 싸지만 기계음이 티난다. 안내방송에는 안 쓴다.

마을 안내방송은 문구가 짧고 반복이 많아 캐시가 잘 들으므로, 요금보다
품질을 택해 Neural2/Wavenet 을 기본으로 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass

#: 화면에 노출할 언어. 사양의 한/영/일/중.
#: 키는 Google 의 languageCode 를 그대로 쓴다(중국어는 cmn-CN).
LANGUAGES: dict[str, str] = {
    "ko-KR": "한국어",
    "en-US": "영어",
    "ja-JP": "일본어",
    "cmn-CN": "중국어",
}


@dataclass(frozen=True, slots=True)
class Voice:
    #: Google API 의 voice.name 그대로.
    id: str
    label: str
    language: str
    #: 품질 등급. 화면에 표시하고, 요금을 가늠하는 데 쓴다.
    engine: str = "Neural2"


#: 보이스 카탈로그. 언어별 첫 항목이 그 언어의 기본값이다.
VOICES: tuple[Voice, ...] = (
    # 한국어 — 안내방송의 주력이라 여성/남성을 모두 둔다.
    Voice("ko-KR-Neural2-A", "여성 A (밝은 톤)", "ko-KR"),
    Voice("ko-KR-Neural2-B", "여성 B (차분한 톤)", "ko-KR"),
    Voice("ko-KR-Neural2-C", "남성 C", "ko-KR"),
    # 영어
    Voice("en-US-Neural2-F", "여성 F", "en-US"),
    Voice("en-US-Neural2-D", "남성 D", "en-US"),
    # 일본어
    Voice("ja-JP-Neural2-B", "여성 B", "ja-JP"),
    Voice("ja-JP-Neural2-C", "남성 C", "ja-JP"),
    # 중국어 — Neural2 가 없어 Wavenet 을 쓴다.
    Voice("cmn-CN-Wavenet-A", "여성 A", "cmn-CN", engine="Wavenet"),
    Voice("cmn-CN-Wavenet-B", "남성 B", "cmn-CN", engine="Wavenet"),
)

_BY_ID = {v.id: v for v in VOICES}


def get_voice(voice_id: str) -> Voice | None:
    return _BY_ID.get(voice_id)


def default_voice(language: str) -> Voice | None:
    """언어의 기본 보이스. 화면이 언어만 고르고 보이스를 생략할 때 쓴다."""
    return next((v for v in VOICES if v.language == language), None)


def voices_for(language: str | None = None) -> list[Voice]:
    if language is None:
        return list(VOICES)
    return [v for v in VOICES if v.language == language]
