#!/usr/bin/env python
"""개발용 시드 데이터.

    cd backend && .venv/Scripts/python ../scripts/seed.py

멱등이다. 여러 번 돌려도 중복이 생기지 않는다.
운영에서는 super_admin 1개만 만들고 나머지는 화면에서 등록한다.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select  # noqa: E402

from app.constants import Role  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.db import engine, session_scope  # noqa: E402
from app.models.org import User, UserVillage, Village, Zone  # noqa: E402

SUPER_ADMIN = ("admin", "admin1234!")
VILLAGE_ADMIN = ("sindong", "village1234!")

VILLAGES = [
    # (이름, 시도, 시군구, 위도, 경도, [구역...])
    ("신동마을", "경상북도", "안동시", 36.5684, 128.7294, ["마을회관", "정자나무", "버스정류장"]),
    ("하회마을", "경상북도", "안동시", 36.5390, 128.5180, ["입구", "강변"]),
]


async def main() -> None:
    async with session_scope() as db:
        # ── 마을 · 구역 ─────────────────────────────────────────────
        village_ids: list[int] = []
        for name, sido, sigungu, lat, lng, zone_names in VILLAGES:
            village = await db.scalar(select(Village).where(Village.name == name))
            if village is None:
                village = Village(
                    name=name, sido=sido, sigungu=sigungu, lat=lat, lng=lng
                )
                db.add(village)
                await db.flush()
                print(f"  + 마을 {name} (id={village.id})")
            village_ids.append(village.id)

            for zone_name in zone_names:
                exists = await db.scalar(
                    select(Zone.id).where(Zone.village_id == village.id, Zone.name == zone_name)
                )
                if exists is None:
                    db.add(Zone(village_id=village.id, name=zone_name))
                    print(f"      + 구역 {name}/{zone_name}")
            await db.flush()

        # ── 계정 ────────────────────────────────────────────────────
        username, password = SUPER_ADMIN
        if await db.scalar(select(User.id).where(User.username == username)) is None:
            db.add(
                User(
                    username=username,
                    password_hash=hash_password(password),
                    role=Role.SUPER_ADMIN.value,
                )
            )
            print(f"  + 계정 {username} / {password}  (super_admin)")

        username, password = VILLAGE_ADMIN
        user = await db.scalar(select(User).where(User.username == username))
        if user is None:
            user = User(
                username=username,
                password_hash=hash_password(password),
                role=Role.VILLAGE_ADMIN.value,
            )
            db.add(user)
            await db.flush()
            # 첫 번째 마을만 담당시킨다 — 범위 제한이 실제로 동작하는지 보려면
            # 담당 밖 마을이 하나는 있어야 한다.
            db.add(UserVillage(user_id=user.id, village_id=village_ids[0]))
            print(f"  + 계정 {username} / {password}  (village_admin → {VILLAGES[0][0]})")

    # 엔진을 명시적으로 닫는다. 안 닫으면 Windows + asyncio 에서
    # 이벤트 루프가 먼저 닫히며 SSL transport 정리 에러가 시끄럽게 뜬다.
    await engine.dispose()
    print("시드 완료")


if __name__ == "__main__":
    asyncio.run(main())
