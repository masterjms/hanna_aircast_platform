#!/usr/bin/env python
"""개발용 시드 — 실제 로직은 backend/app/seed.py 에 있다.

    cd backend && .venv/Scripts/python ../scripts/seed.py

로직을 앱 안으로 옮긴 이유: 배포 이미지에는 backend/ 안의 것만 들어가므로
저장소 루트의 이 파일은 컨테이너에서 실행할 수 없다. 운영에서는 이렇게 쓴다:

    docker compose exec backend python -m app.seed

이 래퍼는 기존 개발 흐름을 그대로 유지하려고 남겨둔다 — 예시 마을과
village_admin 계정까지 만드는 --demo 모드로 돈다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.seed import main  # noqa: E402

if __name__ == "__main__":
    argv = sys.argv[1:]
    if "--demo" not in argv:
        argv.append("--demo")
    main(argv)
