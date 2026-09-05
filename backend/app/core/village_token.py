"""MQTT 로 나가는 village_id 문자열 — 레지스트리 사양 §2.4 (2026-08-30 개정).

    법정동코드(10) + 마을 연번(2) = 12자리        예) 1281033021 01 → "128103302101"

단말은 값을 해석하지 않고 `iotradio/village/<값>/cmd` 에 그대로 끼워 구독한다(§2.1).
숫자 8~16자리를 받고, **전부 0 이면 미배정**으로 보아 마을 topic 을 구독하지 않는다(§2.2).
그래서 형식은 서버가 정하며, 사양이 정한 기본이 위의 12자리다 — 법정동 기반이라
운영자가 topic 만 보고도 어느 리인지 읽을 수 있다(문제점 리스트 16번).

연번은 "단말 번호"가 아니라 **"방송 그룹 번호"**다(§2.3). 같은 리에 방송 그룹이 둘
(마을회관/경로당)일 때만 02 가 생기고, 대부분 01 이다.

**한 번 정한 코드는 바꾸지 않는다**(§2, 행정구역 개편 시에도). 코드는 b_code 가 처음
채워질 때 한 번 만들어지고, 그 뒤 b_code 가 바뀌어도 그대로 둔다 — 바꾸면 그 마을
전 단말을 재설정하고 과거 이력이 끊긴다.

주소가 없어 b_code 가 비어 있는 마을(초기 시험 마을 등)은 코드를 만들 수 없다. 그런
마을은 예전 방식(DB id 8자리 제로패딩)을 그대로 쓴다 — 단말은 자릿수를 가리지 않으므로
두 형식이 섞여도 동작한다. 주소를 넣으면 그때 12자리로 바뀌고 CONFIG 가 재발행된다.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import VILLAGE_ID_WIDTH
from app.errors import ApiError
from app.models.org import Village

#: 미배정. 자릿수는 무관하지만(§2.2) 12자리 형식과 나란히 보이게 12개로 맞춘다.
UNASSIGNED_TOKEN = "0" * 12

#: 연번 상한. 한 리에 방송 그룹이 99개를 넘을 일은 없다.
_MAX_SEQ = 99


def legacy_token(village_id: int) -> str:
    """예전 방식 — DB id 8자리 제로패딩. b_code 가 없는 마을에만 쓴다."""
    return str(village_id).zfill(VILLAGE_ID_WIDTH)


def token_for(village_id: int, village_code: str | None) -> str:
    """이 마을이 MQTT 에서 쓰는 village_id 문자열."""
    return village_code or legacy_token(village_id)


def next_village_code(b_code: str, taken: Iterable[str]) -> str:
    """같은 리(b_code) 안에서 비어 있는 첫 연번으로 12자리 코드를 만든다.

    삭제된 마을의 연번은 재사용한다 — 연번은 식별자가 아니라 "몇 번째 방송 그룹"이다.
    """
    if len(b_code) != 10 or not b_code.isdigit():
        raise ApiError(f"법정동코드 형식이 아닙니다: {b_code!r}", code="BAD_B_CODE")
    used = set(taken)
    for seq in range(1, _MAX_SEQ + 1):
        code = f"{b_code}{seq:02d}"
        if code not in used:
            return code
    raise ApiError(
        f"한 리({b_code})에 방송 그룹이 {_MAX_SEQ}개를 넘었습니다.", code="VILLAGE_CODE_EXHAUSTED"
    )


async def village_tokens(db: AsyncSession, village_ids: Iterable[int]) -> dict[int, str]:
    """마을 id 들 → MQTT 문자열. 발행·ACL·불일치 검사가 전부 이걸 거친다."""
    ids = list({int(v) for v in village_ids})
    if not ids:
        return {}
    rows = await db.execute(
        select(Village.id, Village.village_code).where(Village.id.in_(ids))
    )
    return {vid: token_for(vid, code) for vid, code in rows.all()}
