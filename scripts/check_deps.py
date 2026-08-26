#!/usr/bin/env python
"""pyproject 의 의존성이 Dockerfile 에도 들어 있는지 확인한다.

    python scripts/check_deps.py

왜 필요한가:
    backend/Dockerfile 은 의존성 목록을 pyproject.toml 과 **따로** 들고 있다.
    레이어 캐시 때문이다 — app/ 을 복사하기 전에 설치해야 코드가 바뀔 때마다
    의존성을 다시 깔지 않는다.

    대신 두 곳이 어긋날 수 있고, 어긋나면 **운영에서만 ImportError 로 드러난다.**
    로컬 venv 에는 깔려 있으니 테스트도 통과한다.
    (2026-08-26 google-cloud-texttospeech 누락으로 실제 발생 — TTS 호출이 실패했다.)

    CI 에서 돌려 머지 전에 잡는다.

tomllib 는 3.11+ 라서 쓰지 않는다. 의존성 줄만 읽으면 되므로 정규식으로 충분하다.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "backend" / "pyproject.toml"
DOCKERFILE = ROOT / "backend" / "Dockerfile"

#: 이미지에 안 들어가도 되는 것들. 개발·테스트 전용이라 운영 이미지와 무관하다.
SKIP_GROUPS = {"dev"}


def _package_name(spec: str) -> str:
    """'sqlalchemy[asyncio]>=2.0.36' → 'sqlalchemy'"""
    return re.split(r"[><=!~\[;\s]", spec.strip(), maxsplit=1)[0].strip()


def _collect(text: str) -> set[str]:
    """[project].dependencies 와 optional-dependencies 의 패키지 이름."""
    names: set[str] = set()

    # 목록의 끝은 "행 맨 앞의 ]" 로 찾는다.
    #   "uvicorn[standard]>=0.32" 처럼 값 안에도 ] 가 있어서
    #   가장 가까운 ] 까지 자르면 목록이 잘린다(실제로 2개만 잡혔었다).
    block = re.search(r"^dependencies\s*=\s*\[\s*$(.*?)^\]", text, re.S | re.M)
    if block:
        names |= {_package_name(q) for q in re.findall(r'"([^"]+)"', block.group(1))}

    # [project.optional-dependencies] 아래의 각 그룹
    opt = re.search(r"^\[project\.optional-dependencies\](.*?)(?=^\[|\Z)", text, re.S | re.M)
    if opt:
        for group, body in re.findall(
            r"^(\w+)\s*=\s*\[\s*$(.*?)^\]", opt.group(1), re.S | re.M
        ):
            if group in SKIP_GROUPS:
                continue
            names |= {_package_name(q) for q in re.findall(r'"([^"]+)"', body)}

    return {n for n in names if n}


def main() -> int:
    wanted = _collect(PYPROJECT.read_text(encoding="utf-8"))

    # 주석은 빼고 본다. Dockerfile 주석에 패키지 이름이 적혀 있으면
    # 실제로는 설치하지 않는데도 "있다"고 오판한다 — 이 검사기가 처음에
    # 그 이유로 TTS 누락을 놓쳤다.
    docker = chr(10).join(
        line for line in DOCKERFILE.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("#")
    )

    missing = sorted(n for n in wanted if n not in docker)
    if missing:
        print("!! Dockerfile 에 빠진 의존성:", ", ".join(missing))
        print("   운영 이미지에만 없어서 로컬 테스트로는 안 잡힌다.")
        print(f"   {DOCKERFILE.relative_to(ROOT)} 의 pip install 목록에 추가할 것.")
        return 1

    print(f"의존성 동기화 OK ({len(wanted)}개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
