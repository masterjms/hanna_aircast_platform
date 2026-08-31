"""카카오 로컬 API — 주소 검색(주소 → 도로명·지번·법정동코드·WGS84 좌표).

호출은 등록·수정 시 1회뿐이고 결과는 DB 가 정본이다(지도 설계 §3.2) —
지도를 여는 것으로는 이 모듈이 불리지 않는다.

REST 키는 서버 전용 비밀값이라 프론트가 직접 카카오를 부르지 않고
GET /api/geo/address 프록시를 거친다.

httpx 등을 새로 들이지 않고 표준 라이브러리(urllib)를 스레드에서 돌린다 —
등록 빈도의 호출량에서 의존성 하나가 더 비싸다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from pydantic import BaseModel

from app.config import settings
from app.errors import ApiError

log = logging.getLogger(__name__)

_SEARCH_URL = "https://dapi.kakao.com/v2/local/search/address.json"
_TIMEOUT_SEC = 10
#: 검색 결과 상한. 주소 검색은 보통 1~2건이고, 화면 드롭다운에 그 이상은 소음이다.
_MAX_RESULTS = 10


class KakaoKeyMissing(ApiError):
    status_code = 503
    code = "KAKAO_KEY_MISSING"
    message = "카카오 REST API 키가 설정되지 않았습니다 (.env KAKAO_REST_API_KEY)."


class KakaoSearchFailed(ApiError):
    status_code = 502
    code = "KAKAO_SEARCH_FAILED"
    message = "카카오 주소 검색에 실패했습니다."


class AddressResult(BaseModel):
    """검색 결과 한 건 — 화면이 고르면 이 값들이 그대로 DB 에 들어간다."""

    #: 대표 표기. 도로명이 있으면 도로명, 없으면 지번 (리 단위 검색은 도로명이 없다).
    address_name: str
    road_address: str | None
    jibun_address: str | None
    #: 법정동코드 10자리. 행정구역 개편 직후 등 카카오가 못 줄 수도 있어 선택.
    b_code: str | None
    lat: float
    lng: float


def _request_kakao(query: str) -> dict:
    """동기 HTTP 호출 — asyncio.to_thread 로 감싸서 쓴다."""
    url = _SEARCH_URL + "?" + urllib.parse.urlencode({"query": query, "size": _MAX_RESULTS})
    req = urllib.request.Request(
        url, headers={"Authorization": f"KakaoAK {settings.kakao_rest_api_key}"}
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT_SEC) as resp:
        return json.load(resp)


async def search_address(query: str) -> list[AddressResult]:
    if not settings.kakao_rest_api_key:
        raise KakaoKeyMissing()

    try:
        data = await asyncio.to_thread(_request_kakao, query)
    except urllib.error.HTTPError as e:
        # 401=키 오류, 429=쿼터 소진(첫 활성화 앱이 아니거나 무료분 초과), 403=사용 설정 OFF
        log.warning("카카오 주소 검색 실패 HTTP %s: %s", e.code, e.read()[:200])
        raise KakaoSearchFailed(
            f"카카오 주소 검색이 거부됐습니다 (HTTP {e.code}). "
            "앱의 [카카오맵] 사용 설정과 쿼터를 확인하세요."
        ) from e
    except OSError as e:
        raise KakaoSearchFailed("카카오 주소 검색 서버에 연결하지 못했습니다.") from e

    results: list[AddressResult] = []
    for doc in data.get("documents", []):
        jibun = doc.get("address") or {}
        road = doc.get("road_address") or {}
        try:
            lat, lng = float(doc["y"]), float(doc["x"])
        except (KeyError, TypeError, ValueError):
            continue  # 좌표 없는 행은 우리 용도(마커)에 쓸 수 없다
        results.append(
            AddressResult(
                address_name=doc.get("address_name")
                or road.get("address_name")
                or jibun.get("address_name")
                or "",
                road_address=road.get("address_name") or None,
                jibun_address=jibun.get("address_name") or None,
                b_code=jibun.get("b_code") or None,
                lat=lat,
                lng=lng,
            )
        )
    return results
