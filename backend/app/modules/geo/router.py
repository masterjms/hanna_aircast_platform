"""주소 검색 프록시.

카카오 REST 키를 브라우저에 노출하지 않으려고 서버가 대신 부른다.
마을 등록·단말 위치 입력 화면이 쓴다 — 결과에서 하나를 고르면 도로명·지번·
법정동코드·좌표가 한꺼번에 채워진다(지도 설계 §3).

권한: 로그인만 요구한다. village_admin 도 담당 마을 단말의 주소를 고치므로
super_admin 으로 좁히지 않는다. 쓰기 없는 조회 프록시라 범위 검사도 없다.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query

from app.core.deps import CurrentUser
from app.core.kakao_geo import AddressResult, search_address

router = APIRouter(prefix="/api/geo", tags=["geo"])


@router.get("/address", response_model=list[AddressResult])
async def address_search(
    q: Annotated[str, Query(min_length=2, max_length=100)],
    _: CurrentUser,
) -> list[AddressResult]:
    """주소 검색. 리 이름만 넣어도 리 중심 좌표가 나온다(실측)."""
    return await search_address(q)
