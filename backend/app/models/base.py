"""SQLAlchemy 선언적 베이스.

모델은 테이블 모양만 정의한다. 비즈니스 로직은 각 모듈의 service.py 로 간다.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def created_at_column() -> Mapped[dt.datetime]:
    """생성 시각 컬럼. 기본값은 DB 의 now() 를 쓴다(앱 시계에 의존하지 않음)."""
    return mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
