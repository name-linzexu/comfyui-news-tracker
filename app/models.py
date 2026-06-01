from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class NewsItem:
    guid: str
    source_id: str
    source_name: str
    source_type: str
    category: str
    title: str
    summary: str
    url: str
    published_at: datetime
    fetched_at: datetime
    score: int
    featured: bool
    tags: list[str]
    source_tier: str = "T2"
    reason: str = ""
    score_breakdown: dict[str, int] | None = None
    cluster_key: str = ""
    cluster_title: str = ""
    author: str | None = None
    raw: dict[str, Any] | None = None
