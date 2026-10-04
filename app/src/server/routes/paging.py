"""The list shape every collection route returns.

It follows OpenAI's list convention: ``limit``, ``order`` and an ``after``
cursor in; ``object: "list"``, ``data``, ``first_id``, ``last_id`` and
``has_more`` out. For the next page, pass the previous page's ``last_id`` as
``after``.
"""

from typing import Generic, Literal, Optional, Sequence, TypeVar

from fastapi import HTTPException
from pydantic import BaseModel

Item = TypeVar("Item")


class Page(BaseModel, Generic[Item]):
    object: Literal["list"] = "list"
    data: list[Item]
    first_id: Optional[str]
    last_id: Optional[str]
    has_more: bool


def page_of(ascending: Sequence[Item], limit: int, order: str,
            after: Optional[str]) -> Page[Item]:
    """The ``limit`` items after the one with id ``after``.

    ``ascending`` is every item, oldest first; each has an ``id``.
    """
    ordered = list(reversed(ascending)) if order == "desc" else list(ascending)
    start = 0
    if after is not None:
        ids = [item.id for item in ordered]
        if after not in ids:
            raise HTTPException(status_code=400, detail=f"nothing with id {after} to page after")
        start = ids.index(after) + 1
    data = ordered[start:start + limit]
    return Page(data=data,
                first_id=data[0].id if data else None,
                last_id=data[-1].id if data else None,
                has_more=start + limit < len(ordered))
