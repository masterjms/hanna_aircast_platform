"""공통 스키마."""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class ApiModel(BaseModel):
    """모든 응답 모델의 베이스. ORM 객체에서 바로 만들 수 있게 한다."""

    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    """오프셋 페이지네이션.

    커서 방식이 아니라 오프셋을 쓴다 — 이력 화면이 "3페이지로 이동" 같은 임의 접근을
    쓰고, 데이터 양(단말 300대 규모)이 오프셋의 성능 문제를 만들 수준이 아니다.
    """

    items: list[T]
    total: int
    limit: int
    offset: int

    @property
    def has_more(self) -> bool:
        return self.offset + len(self.items) < self.total


class PageParams(BaseModel):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)
