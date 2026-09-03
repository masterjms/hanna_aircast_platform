#!/usr/bin/env python3
"""마을 경계 넣기 — 「구역의 도형」 SHP → 서버 DB.

담당자 PC 에서 돌린다. 서버에 파일을 올릴 필요가 없다 — 이 스크립트가 로컬에서
변환까지 끝내고 관리자 API 로 밀어 넣는다.

    pip install pyshp pyproj
    python scripts/import_boundaries.py --shp "…/12000/TL_SCCO_LI" \
        --server https://hanna-aircast.co.kr --user admin

무엇을 하나
    1. TL_SCCO_LI.shp 에서 리 경계를 읽는다 (LI_CD = 법정동코드 10자리)
    2. EPSG:5179(UTM-K) → WGS84 재투영. 원본에 .prj 가 없어 좌표계를 직접 준다
       (「도로명주소 공간데이터 활용가이드」 확인, 2026-09-03)
    3. 화면에 안 보일 만큼의 점을 줄인다(Douglas-Peucker)
    4. 서버에 등록된 마을 중 b_code 가 맞는 것만 PATCH /api/villages/{id}

왜 리포에 GeoJSON 을 커밋하지 않나
    · 고객마다 다른 데이터다(판매 형태가 고객당 서버 한 벌)
    · 원본이 월별로 갱신돼서 커밋하면 이력이 계속 부풀어 오른다
    · 필요한 건 등록된 마을 몇 개뿐인데 원본은 시도당 수천 개다
    지도 설계 §4.8 참고.

출처 표시
    이 데이터는 행정안전부 주소정보제공(공공누리 제1유형)이다. 지도 화면에
    출처를 표시한다 — 제1유형의 유일한 의무다.
"""

from __future__ import annotations

import argparse
import getpass
import json
import math
import sys
import urllib.error
import urllib.request

# 도형 계산(simplify·ring_area·shape_to_geometry)은 이 두 패키지 없이도 돌아간다.
# 그래야 테스트가 SHP 리더 없이 그 부분만 검증할 수 있다 — 계산이 틀리면 경계가
# 조용히 일그러지므로 여기가 가장 검증이 필요한 자리다.
try:
    import shapefile  # pyshp
    from pyproj import Transformer
except ImportError:  # pragma: no cover - 실행할 때만 필요하다
    shapefile = None
    Transformer = None

#: 원본 좌표계. 「구역의 도형」에는 .prj 가 없어서 직접 지정해야 한다.
SOURCE_CRS = "EPSG:5179"  # UTM-K (GRS80)
TARGET_CRS = "EPSG:4326"  # WGS84 위경도 — 지도 SDK 가 받는 형식

#: 경계 단순화 허용 오차(도). 위도 1도 ≈ 111km 이므로 1e-5 ≈ 약 1.1m.
#: 마을 경계를 보는 축척에서 1m 차이는 화면에 안 보이는데, 점 수는 크게 준다.
DEFAULT_TOLERANCE = 2e-5

#: 이보다 작은 조각은 버린다(제곱도). 섬·자투리 필지가 점 하나로 찍히는 것을 막는다.
MIN_RING_AREA = 1e-9


# ── 도형 처리 ────────────────────────────────────────────────────────────
def _perpendicular_distance(pt, start, end) -> float:
    """점에서 선분까지의 거리. Douglas-Peucker 의 판정에 쓴다."""
    (x, y), (x1, y1), (x2, y2) = pt, start, end
    dx, dy = x2 - x1, y2 - y1
    if dx == 0 and dy == 0:
        return math.hypot(x - x1, y - y1)
    # 선분에 내린 수선의 발이 선분 밖이면 끝점까지의 거리로 잰다.
    t = ((x - x1) * dx + (y - y1) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(x - (x1 + t * dx), y - (y1 + t * dy))


def simplify(points: list[tuple[float, float]], tolerance: float) -> list[tuple[float, float]]:
    """Douglas-Peucker. 재귀 대신 스택을 쓴다 — 리 하나가 수천 점이라 깊이가 깊다."""
    if len(points) < 3:
        return points
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        if last <= first + 1:
            continue
        worst, worst_i = 0.0, first
        for i in range(first + 1, last):
            d = _perpendicular_distance(points[i], points[first], points[last])
            if d > worst:
                worst, worst_i = d, i
        if worst > tolerance:
            keep[worst_i] = True
            stack.append((first, worst_i))
            stack.append((worst_i, last))
    return [p for p, k in zip(points, keep) if k]


def ring_area(ring: list[tuple[float, float]]) -> float:
    """신발끈 공식. 부호는 방향(외곽/구멍)을 뜻하므로 그대로 돌려준다."""
    total = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        total += x1 * y2 - x2 * y1
    return total / 2.0


def shape_to_geometry(shape, transform, tolerance: float) -> dict | None:
    """pyshp 폴리곤 → GeoJSON geometry(WGS84).

    SHP 는 여러 고리(ring)를 parts 로 나눠 담는다. 바깥 고리와 구멍이 섞여 있고,
    섬이 있으면 바깥 고리가 여러 개다. 넓이 부호로 갈라 MultiPolygon 으로 만든다.
    """
    parts = list(shape.parts) + [len(shape.points)]
    rings: list[list[tuple[float, float]]] = []
    for start, end in zip(parts, parts[1:]):
        raw = shape.points[start:end]
        if len(raw) < 4:
            continue
        lonlat = [transform(x, y) for x, y in raw]
        ring = simplify(lonlat, tolerance)
        if len(ring) < 4:
            continue
        # GeoJSON 은 첫 점과 끝 점이 같아야 한다.
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        if abs(ring_area(ring)) < MIN_RING_AREA:
            continue
        rings.append(ring)

    if not rings:
        return None

    # 부호가 음수면 시계방향 = SHP 규약상 바깥 고리. 양수면 구멍이다.
    polygons: list[list[list[tuple[float, float]]]] = []
    for ring in rings:
        if ring_area(ring) < 0 or not polygons:
            polygons.append([ring])
        else:
            polygons[-1].append(ring)

    if len(polygons) == 1:
        return {"type": "Polygon", "coordinates": [[list(p) for p in r] for r in polygons[0]]}
    return {
        "type": "MultiPolygon",
        "coordinates": [[[list(p) for p in r] for r in poly] for poly in polygons],
    }


def load_boundaries(shp_path: str, tolerance: float) -> dict[str, dict]:
    """b_code → GeoJSON geometry."""
    reader = shapefile.Reader(shp_path, encoding="cp949")
    to_wgs84 = Transformer.from_crs(SOURCE_CRS, TARGET_CRS, always_xy=True).transform
    out: dict[str, dict] = {}
    for i, record in enumerate(reader.iterRecords()):
        code = str(record["LI_CD"]).strip()
        if not code:
            continue
        geometry = shape_to_geometry(reader.shape(i), to_wgs84, tolerance)
        if geometry is not None:
            out[code] = geometry
    return out


# ── 서버 통신 ────────────────────────────────────────────────────────────
def call(server: str, path: str, *, token: str | None = None, method: str = "GET", body=None):
    request = urllib.request.Request(f"{server.rstrip('/')}{path}", method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    try:
        with urllib.request.urlopen(request, data=data, timeout=60) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"!! {method} {path} → HTTP {exc.code}\n   {detail}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="「구역의 도형」 리 경계를 마을에 넣는다")
    parser.add_argument("--shp", required=True, help="TL_SCCO_LI 경로(확장자 없이)")
    parser.add_argument("--server", default="https://hanna-aircast.co.kr")
    parser.add_argument("--user", required=True, help="super_admin 계정")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE)
    parser.add_argument(
        "--dry-run", action="store_true", help="서버에 쓰지 않고 무엇이 들어갈지만 보여준다"
    )
    args = parser.parse_args()

    if shapefile is None or Transformer is None:
        return print("먼저 설치하세요:  pip install pyshp pyproj") or 2

    print(f"1) 경계 읽는 중: {args.shp}")
    boundaries = load_boundaries(args.shp, args.tolerance)
    print(f"   리 {len(boundaries)}개")

    password = getpass.getpass(f"{args.user} 비밀번호: ")
    login = call(
        args.server, "/api/auth/login", method="POST",
        body={"username": args.user, "password": password},
    )
    token = login["access_token"]

    villages = call(args.server, "/api/villages", token=token)
    print(f"2) 등록된 마을 {len(villages)}개")

    matched, missing_code, no_shape = [], [], []
    for village in villages:
        code = village.get("b_code")
        if not code:
            missing_code.append(village)
        elif code in boundaries:
            matched.append(village)
        else:
            no_shape.append(village)

    for village in matched:
        geometry = boundaries[village["b_code"]]
        points = sum(len(r) for r in geometry["coordinates"]) if geometry["type"] == "Polygon" else \
            sum(len(r) for poly in geometry["coordinates"] for r in poly)
        size_kb = len(json.dumps(geometry)) / 1024
        print(f"   · {village['name']} ({village['b_code']}) — {points}점, {size_kb:.1f}KB")
        if args.dry_run:
            continue
        call(
            args.server, f"/api/villages/{village['id']}",
            token=token, method="PATCH", body={"boundary": geometry},
        )

    if missing_code:
        print(f"3) b_code 없어 건너뜀 {len(missing_code)}개 — 마을 관리에서 주소를 먼저 넣으세요")
        for village in missing_code:
            print(f"   · {village['name']}")
    if no_shape:
        print(f"4) 이 SHP 에 없는 마을 {len(no_shape)}개 — 다른 시도 파일이 필요합니다")
        for village in no_shape:
            print(f"   · {village['name']} ({village['b_code']})")

    if args.dry_run:
        print("\n--dry-run 이라 서버에 쓰지 않았습니다.")
    else:
        print(f"\n완료 — 마을 {len(matched)}개에 경계를 넣었습니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
