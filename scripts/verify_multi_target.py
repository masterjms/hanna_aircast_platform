"""다중 대상 방송 검증.

확인하는 시나리오 (사용자가 요구한 4가지 + 동시성):
  1. 마을 1개 라이브
  2. 마을 여러 개 라이브        ← 이번 작업의 핵심
  3. 한 마을에서 단말 몇 대만
  4. 전체 마을
  5. 서로 다른 마을의 동시 방송 (라이브 + 라이브, 라이브 + 파일)
  6. 단말 겹침은 여전히 409 로 막히는가

    cd backend && ../.venv/Scripts/python ../scripts/verify_multi_target.py
"""

from __future__ import annotations

import sys
import time

import requests

BASE = "http://127.0.0.1:8080"
ok_count = 0
fail_count = 0


def check(label: str, passed: bool, note: str = "") -> None:
    global ok_count, fail_count
    mark = "PASS" if passed else "FAIL"
    if passed:
        ok_count += 1
    else:
        fail_count += 1
    print(f"  [{mark}] {label}" + (f": {note}" if note else ""))


class _Client(requests.Session):
    """base_url 을 붙여주는 얇은 래퍼."""

    def __enter__(self) -> "_Client":
        return self

    def request(self, method, url, *a, **kw):  # type: ignore[override]
        kw.setdefault("timeout", 20)
        return super().request(method, BASE + url, *a, **kw)


def login(c: _Client) -> str:
    r = c.post("/api/auth/login", json={"username": "admin", "password": "admin1234!"})
    r.raise_for_status()
    return r.json()["access_token"]


def main() -> int:
    with _Client() as c:
        token = login(c)
        h = {"Authorization": f"Bearer {token}"}

        villages = c.get("/api/villages", headers=h).json()

        def snapshot() -> tuple[list[dict], dict[int, list[str]]]:
            """지금 온라인인 배정 단말. 실물 단말이 붙었다 떨어지므로 매번 다시 센다."""
            devs = c.get("/api/devices", headers=h).json()
            on = [d for d in devs if d["online"] and d["village_id"]]
            by_v: dict[int, list[str]] = {}
            for d in on:
                by_v.setdefault(d["village_id"], []).append(d["mac"])
            return on, by_v

        online, by_village = snapshot()

        print(f"\n온라인 단말 {len(online)}대 / 마을 {len(by_village)}곳")
        for vid, macs in by_village.items():
            name = next((v["name"] for v in villages if v["id"] == vid), vid)
            print(f"  {name}({vid}): {len(macs)}대")

        if len(by_village) < 2:
            print("\n⚠ 마을 2곳 이상에 온라인 단말이 있어야 다중 대상을 검증할 수 있다.")
            return 1

        vids = sorted(by_village)
        started: list[int] = []

        def start_live(target_ids: list[str], scope: str = "village"):
            return c.post(
                "/api/broadcast/live/start",
                headers=h,
                json={"target_scope": scope, "target_ids": target_ids},
            )

        def stop(bid: int) -> None:
            c.post("/api/broadcast/live/stop", headers=h, json={"broadcast_id": bid})

        # ── 1. 마을 1개 ──────────────────────────────────────────────
        print("\n[1] 마을 1개 라이브")
        r = start_live([str(vids[0])])
        check("시작 성공", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
        if r.status_code == 200:
            b = r.json()
            started.append(b["id"])
            check("target_ids 가 목록으로 반환", b["target_ids"] == [str(vids[0])], str(b["target_ids"]))
            stop(b["id"])
            started.pop()

        # ── 2. 마을 여러 개 ──────────────────────────────────────────
        print("\n[2] 마을 여러 개 라이브 (핵심)")
        multi = [str(v) for v in vids[:2]]
        r = start_live(multi)
        check("시작 성공", r.status_code == 200, f"HTTP {r.status_code} {r.text[:160]}")
        if r.status_code == 200:
            b = r.json()
            started.append(b["id"])
            check("두 마을이 대상에 함께", set(b["target_ids"]) == set(multi), str(b["target_ids"]))
            _, now_by_v = snapshot()
            expected = len(now_by_v.get(vids[0], [])) + len(now_by_v.get(vids[1], []))
            check("대상 단말 수 = 두 마을 합", b["target_count"] == expected,
                  f"{b['target_count']} vs {expected}")
            # 응답은 즉시 오지 않는다 — 도착할 때까지 짧게 폴링한다.
            responded: set[str] = set()
            for _ in range(20):
                time.sleep(0.5)
                b2 = c.get(f"/api/broadcast/{b['id']}", headers=h).json()
                responded = {r_["mac"] for r_ in b2.get("results", [])}
                if len(responded) >= expected:
                    break
            v_responded = {v for v, macs_ in now_by_v.items() if responded & set(macs_)}
            check("두 마을 단말이 모두 응답", len(responded) >= expected,
                  f"{len(responded)}/{expected}대 응답")
            check("응답이 두 마을 모두에서 옴", v_responded >= {vids[0], vids[1]},
                  f"응답한 마을 {sorted(v_responded)}")
            stop(b["id"])
            started.pop()

        # ── 3. 단말 몇 대만 ──────────────────────────────────────────
        print("\n[3] 한 마을에서 단말 몇 대만")
        pick = by_village[vids[0]][:2]
        if len(pick) < 2:
            print("  (건너뜀 — 그 마을에 단말이 2대 미만)")
        else:
            r = start_live(pick, scope="device")
            check("시작 성공", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
            if r.status_code == 200:
                b = r.json()
                started.append(b["id"])
                check("선택한 대수만 대상", b["target_count"] == len(pick),
                      f"{b['target_count']} vs {len(pick)}")
                stop(b["id"])
                started.pop()

        # ── 4. 전체 ─────────────────────────────────────────────────
        print("\n[4] 전체 마을 라이브")
        r = start_live([], scope="all")
        check("시작 성공", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
        if r.status_code == 200:
            b = r.json()
            started.append(b["id"])
            now_online, _ = snapshot()
            check("전 온라인 단말이 대상", b["target_count"] == len(now_online),
                  f"{b['target_count']} vs {len(now_online)}")
            stop(b["id"])
            started.pop()

        # ── 5. 서로 다른 마을 동시 방송 ─────────────────────────────
        print("\n[5] 서로 다른 마을 동시 라이브 (마운트 분리)")
        r1 = start_live([str(vids[0])])
        r2 = start_live([str(vids[1])])
        check("A마을 시작", r1.status_code == 200, f"HTTP {r1.status_code}")
        check("B마을 시작 (동시)", r2.status_code == 200,
              f"HTTP {r2.status_code} {r2.text[:160]}")
        if r1.status_code == 200 and r2.status_code == 200:
            b1, b2 = r1.json(), r2.json()
            started += [b1["id"], b2["id"]]
            check("job_id 가 서로 다름", b1["job_id"] != b2["job_id"],
                  f"{b1['job_id']} vs {b2['job_id']}")
            act = c.get("/api/broadcast/active", headers=h).json()
            live_now = [a for a in act if a["event_type"] == "LIVE_START"]
            check("둘 다 진행 중으로 보임", len(live_now) >= 2, f"{len(live_now)}건")

            # ── 6. 겹침 검사 ────────────────────────────────────────
            print("\n[6] 단말 겹침은 여전히 막히는가")
            r3 = start_live([str(vids[0])])
            check("같은 마을 재시작 → 409", r3.status_code == 409,
                  f"HTTP {r3.status_code}")
            r4 = start_live([str(v) for v in vids[:2]])
            check("겹치는 다중 대상 → 409", r4.status_code == 409,
                  f"HTTP {r4.status_code}")

            for bid in (b1["id"], b2["id"]):
                stop(bid)
            started.clear()

        # 정리
        for bid in started:
            stop(bid)

    print(f"\n{'='*46}\n  통과 {ok_count} / 실패 {fail_count}\n{'='*46}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
