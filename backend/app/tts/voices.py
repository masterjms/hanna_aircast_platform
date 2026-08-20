"""TTS 보이스 목록.

화면의 드롭다운과 서버 검증이 같은 목록을 봐야 해서 여기 한 곳에 둔다.
AWS Polly 기준이며, 엔진을 바꾸면 이 표를 그 엔진 것으로 갈아끼운다.

neural 은 자연스럽지만 표준 대비 요금이 비싸다. 마을 안내방송은 짧고
반복이 많아 캐시가 잘 들으므로 neural 을 기본으로 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass

#: 화면에 노출할 언어. 사양의 한/영/일/중.
LANGUAGES: dict[str, str] = {
    "ko-KR": "한국어",
    "en-US": "영어",
    "ja-JP": "일본어",
    "cmn-CN": "중국어",
}


@dataclass(frozen=True, slots=True)
class Voice:
    id: str
    label: str
    language: str
    #: Polly 엔진. neural 미지원 보이스는 "standard".
    engine: str = "neural"


#: Polly 보이스 카탈로그. 언어별 첫 항목이 기본값이다.
VOICES: tuple[Voice, ...] = (
    Voice("Seoyeon", "서연 (여성)", "ko-KR"),
    Voice("Jihye", "지혜 (여성)", "ko-KR"),
    Voice("Joanna", "Joanna (여성)", "en-US"),
    Voice("Matthew", "Matthew (남성)", "en-US"),
    Voice("Tomoko", "Tomoko (여성)", "ja-JP"),
    Voice("Takumi", "Takumi (남성)", "ja-JP"),
    Voice("Zhiyu", "Zhiyu (여성)", "cmn-CN"),
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
