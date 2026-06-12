from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import replace
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .models import NewsItem
from .scoring import apply_llm_triage
from .settings import settings


FTS_TOKEN_PATTERN = re.compile(r"[\w]+", re.UNICODE)
DEFAULT_DIGEST_TIMEZONE = "Asia/Shanghai"
DAILY_DIGEST_MIN_SCORE = 50

FEATURED_TIER_RANK = {"T1": 3, "T1.5": 2, "T2": 1}
# Per digest-day featured quotas by channel. Channels not listed are unlimited
# (official/T1 releases should never be cut by a quota).
DEFAULT_FEATURED_CHANNEL_QUOTAS = {
    "x": 6,
    "bilibili": 6,
    "models": 8,
    "youtube": 4,
    "community": 8,
    "forum": 6,
    "discord": 6,
}


def featured_channel_for(source_type: str, category: str) -> str:
    if source_type == "x_search":
        return "x"
    if source_type == "bilibili_search":
        return "bilibili"
    if source_type in {"huggingface_models", "civitai_models"}:
        return "models"
    if source_type in {"youtube_search", "youtube_rss"}:
        return "youtube"
    if source_type == "discord_feed":
        return "discord"
    if source_type in {"forum_json", "json_feed"}:
        return "forum"
    if source_type.startswith("github_"):
        return "github"
    if category == "community":
        return "community"
    return "core"


def featured_quotas() -> dict[str, int]:
    quotas = dict(DEFAULT_FEATURED_CHANNEL_QUOTAS)
    for part in (settings.featured_channel_quotas or "").split(","):
        name, separator, value = part.partition(":")
        if not separator:
            continue
        try:
            quotas[name.strip().lower()] = max(0, int(value))
        except ValueError:
            continue
    return quotas


@dataclass(frozen=True)
class UpsertResult:
    inserted: int
    updated: int
    unchanged: int

    @property
    def changed(self) -> int:
        return self.inserted + self.updated


def utc_now() -> datetime:
    return datetime.now(UTC)


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def digest_timezone_name() -> str:
    return settings.digest_timezone.strip() or DEFAULT_DIGEST_TIMEZONE


def digest_timezone() -> tzinfo:
    name = digest_timezone_name()
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        if name in {DEFAULT_DIGEST_TIMEZONE, "China", "PRC", "CST", "UTC+8", "UTC+08:00", "+08:00"}:
            return timezone(timedelta(hours=8), DEFAULT_DIGEST_TIMEZONE)
        return UTC


def digest_day_for(value: str | datetime | None) -> str | None:
    parsed = parse_dt(value) if isinstance(value, str) else value
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(digest_timezone()).date().isoformat()


def digest_day_bounds(day: str | None = None) -> tuple[str, str, str]:
    tz = digest_timezone()
    local_day = date.fromisoformat(day) if day else utc_now().astimezone(tz).date()
    start_local = datetime.combine(local_day, time.min, tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return (
        local_day.isoformat(),
        start_local.astimezone(UTC).isoformat(),
        end_local.astimezone(UTC).isoformat(),
    )


def recent_digest_days(count: int = 3) -> list[str]:
    """Local digest days ending today, oldest first.

    Exports re-render this trailing window each run so a day's archive file
    keeps filling up until the day is actually over, instead of being frozen
    at whatever had been published by the morning run.
    """
    today = date.fromisoformat(digest_day_bounds()[0])
    return [(today - timedelta(days=offset)).isoformat() for offset in range(max(1, count) - 1, -1, -1)]


def normalize_fts_query(value: str | None) -> str | None:
    if not value:
        return None
    tokens = FTS_TOKEN_PATTERN.findall(value.lower())
    if not tokens:
        return None
    return " ".join(fts_query_term(token) for token in tokens[:12])


def fts_query_term(token: str) -> str:
    if len(token) <= 1:
        return token
    return f"{token}*"


class Storage:
    def __init__(self, db_path: Path = settings.database_path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def connection(self) -> Iterable[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS items (
                    guid TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    url TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    fetched_at TEXT NOT NULL,
                    score INTEGER NOT NULL,
                    featured INTEGER NOT NULL,
                    tags TEXT NOT NULL,
                    source_tier TEXT NOT NULL DEFAULT 'T2',
                    reason TEXT NOT NULL DEFAULT '',
                    score_breakdown TEXT NOT NULL DEFAULT '{}',
                    cluster_key TEXT NOT NULL DEFAULT '',
                    cluster_title TEXT NOT NULL DEFAULT '',
                    author TEXT,
                    raw TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_items_category ON items(category)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_items_score ON items(score DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_items_source ON items(source_id)")
            self._ensure_column(conn, "items", "source_tier", "TEXT NOT NULL DEFAULT 'T2'")
            self._ensure_column(conn, "items", "reason", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "items", "score_breakdown", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "items", "cluster_key", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(conn, "items", "cluster_title", "TEXT NOT NULL DEFAULT ''")
            if self._ensure_column(conn, "items", "featured_candidate", "INTEGER NOT NULL DEFAULT 0"):
                conn.execute("UPDATE items SET featured_candidate = featured")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_items_tier ON items(source_tier)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_items_cluster ON items(cluster_key)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS collect_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    started_at TEXT NOT NULL,
                    finished_at TEXT NOT NULL,
                    fetched INTEGER NOT NULL,
                    inserted INTEGER NOT NULL,
                    updated INTEGER NOT NULL,
                    unchanged INTEGER NOT NULL,
                    succeeded_sources INTEGER NOT NULL,
                    failed_sources INTEGER NOT NULL,
                    source_results TEXT NOT NULL,
                    errors TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_collect_runs_finished ON collect_runs(finished_at DESC)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS source_submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    name TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    contact TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    reviewed_at TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_source_submissions_status ON source_submissions(status)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_source_submissions_url ON source_submissions(url)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message TEXT NOT NULL,
                    contact TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'new',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_feedback_created ON feedback(created_at DESC)")
            conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS item_search
                USING fts5(guid UNINDEXED, title, summary, tags)
                """
            )

    def upsert_items(self, items: Iterable[NewsItem]) -> UpsertResult:
        inserted = 0
        updated = 0
        unchanged = 0
        with self.connection() as conn:
            for item in items:
                existing = conn.execute(
                    """
                    SELECT title, summary, url, published_at, score, featured, featured_candidate, tags,
                           source_tier, reason, score_breakdown, cluster_key, cluster_title, author, raw
                    FROM items
                    WHERE guid = ?
                    """,
                    (item.guid,),
                ).fetchone()
                item = self._merge_existing_enrichment(existing, item)
                item = self._apply_stored_triage(item)
                values = self._item_values(item)
                # NewsItem.featured is the per-item eligibility (candidate); the
                # final featured flag is owned by select_featured(), so conflicts
                # only refresh the candidate column.
                conn.execute(
                    """
                    INSERT INTO items (
                        guid, source_id, source_name, source_type, category, title, summary, url,
                        published_at, fetched_at, score, featured, featured_candidate, tags, source_tier, reason,
                        score_breakdown, cluster_key, cluster_title, author, raw
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(guid) DO UPDATE SET
                        source_id=excluded.source_id,
                        source_name=excluded.source_name,
                        source_type=excluded.source_type,
                        category=excluded.category,
                        title=excluded.title,
                        summary=excluded.summary,
                        url=excluded.url,
                        published_at=excluded.published_at,
                        fetched_at=excluded.fetched_at,
                        score=excluded.score,
                        featured_candidate=excluded.featured_candidate,
                        tags=excluded.tags,
                        source_tier=excluded.source_tier,
                        reason=excluded.reason,
                        score_breakdown=excluded.score_breakdown,
                        cluster_key=excluded.cluster_key,
                        cluster_title=excluded.cluster_title,
                        author=excluded.author,
                        raw=excluded.raw
                    """,
                    values,
                )
                if existing is None or self._search_row_changed(existing, item):
                    conn.execute("DELETE FROM item_search WHERE guid = ?", (item.guid,))
                    conn.execute(
                        "INSERT INTO item_search(guid, title, summary, tags) VALUES (?, ?, ?, ?)",
                        (item.guid, item.title, item.summary, " ".join(item.tags)),
                    )
                if existing is None:
                    inserted += 1
                elif self._row_changed(existing, item):
                    updated += 1
                else:
                    unchanged += 1
        return UpsertResult(inserted=inserted, updated=updated, unchanged=unchanged)

    def rescore_items(
        self,
        sources_by_id: dict[str, Any],
        keywords: dict[str, list[str]],
        *,
        since: datetime | None = None,
    ) -> int:
        from .sources import build_item

        changed = 0
        with self.connection() as conn:
            if since is not None:
                rows = conn.execute(
                    "SELECT * FROM items WHERE published_at >= ?",
                    (since.astimezone(UTC).isoformat(),),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM items").fetchall()
            for row in rows:
                source = sources_by_id.get(row["source_id"])
                if not source:
                    continue
                raw = json.loads(row["raw"] or "{}")
                github_stars = raw.get("stargazers_count") if isinstance(raw, dict) else None
                item = build_item(
                    source=source,
                    title=row["title"],
                    summary=row["summary"],
                    url=row["url"],
                    published_at=parse_dt(row["published_at"]),
                    keywords=keywords,
                    author=row["author"],
                    raw=raw,
                    github_stars=int(github_stars) if github_stars is not None else None,
                )
                if item is None:
                    if int(row["score"] or 0) == 0 and not bool(row["featured"]) and not bool(row["featured_candidate"]):
                        continue
                    conn.execute(
                        """
                        UPDATE items
                        SET
                            score = 0,
                            featured = 0,
                            featured_candidate = 0,
                            reason = ?,
                            score_breakdown = ?
                        WHERE guid = ?
                        """,
                        (
                            "filtered by current rules",
                            json.dumps({"penalty": -100}, ensure_ascii=False),
                            row["guid"],
                        ),
                    )
                    changed += 1
                    continue
                item = self._apply_stored_triage(item)
                if not self._row_changed(row, item):
                    continue
                conn.execute(
                    """
                    UPDATE items
                    SET
                        score = ?,
                        featured_candidate = ?,
                        tags = ?,
                        source_tier = ?,
                        reason = ?,
                        score_breakdown = ?,
                        cluster_key = ?,
                        cluster_title = ?
                    WHERE guid = ?
                    """,
                    (
                        item.score,
                        1 if item.featured else 0,
                        json.dumps(item.tags, ensure_ascii=False),
                        item.source_tier,
                        item.reason,
                        json.dumps(item.score_breakdown or {}, ensure_ascii=False),
                        item.cluster_key,
                        item.cluster_title,
                        row["guid"],
                    ),
                )
                changed += 1
        return changed

    def enriched_bilibili_urls(self) -> set[str]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT url FROM items
                WHERE source_type = 'bilibili_search'
                  AND json_extract(raw, '$.content_understanding') IS NOT NULL
                """
            ).fetchall()
        return {row["url"] for row in rows}

    def select_featured(self, *, since: datetime | None = None) -> dict[str, int]:
        """Finalize the featured flag from per-item candidates.

        Within each digest day: keep only the best item per event cluster, then
        apply per-channel quotas so one noisy channel cannot flood the feed.
        """
        quotas = featured_quotas()
        with self.connection() as conn:
            clauses = []
            params: list[Any] = []
            if since is not None:
                clauses.append("published_at >= ?")
                params.append(since.astimezone(UTC).isoformat())
            where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
            rows = conn.execute(
                f"""
                SELECT guid, source_type, category, source_tier, score, featured,
                       featured_candidate, cluster_key, published_at
                FROM items
                {where}
                """,
                params,
            ).fetchall()

            def rank(row: sqlite3.Row) -> tuple[int, int, str]:
                return (
                    int(row["score"] or 0),
                    FEATURED_TIER_RANK.get(row["source_tier"], 0),
                    row["published_at"] or "",
                )

            by_day: dict[str, list[sqlite3.Row]] = {}
            for row in rows:
                if not row["featured_candidate"]:
                    continue
                day = digest_day_for(row["published_at"]) or "unknown"
                by_day.setdefault(day, []).append(row)

            selected: set[str] = set()
            demoted_duplicates = 0
            demoted_quota = 0
            for day_rows in by_day.values():
                clusters: dict[str, list[sqlite3.Row]] = {}
                for row in day_rows:
                    key = row["cluster_key"] or f"guid:{row['guid']}"
                    clusters.setdefault(key, []).append(row)
                winners: list[sqlite3.Row] = []
                for group in clusters.values():
                    group.sort(key=rank, reverse=True)
                    winners.append(group[0])
                    demoted_duplicates += len(group) - 1
                by_channel: dict[str, list[sqlite3.Row]] = {}
                for row in winners:
                    channel = featured_channel_for(row["source_type"], row["category"])
                    by_channel.setdefault(channel, []).append(row)
                for channel, group in by_channel.items():
                    limit = quotas.get(channel)
                    group.sort(key=rank, reverse=True)
                    kept = group if limit is None else group[: max(0, limit)]
                    demoted_quota += len(group) - len(kept)
                    selected.update(row["guid"] for row in kept)

            changed = 0
            for row in rows:
                desired = 1 if row["guid"] in selected else 0
                if int(row["featured"] or 0) != desired:
                    conn.execute("UPDATE items SET featured = ? WHERE guid = ?", (desired, row["guid"]))
                    changed += 1
        return {
            "selected": len(selected),
            "demoted_duplicates": demoted_duplicates,
            "demoted_quota": demoted_quota,
            "changed": changed,
        }

    def list_items(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        category: str | None = None,
        channel: str | None = None,
        tier: str | None = None,
        source_id: str | None = None,
        featured: bool | None = None,
        query: str | None = None,
        since: datetime | None = None,
        include_raw: bool = False,
        sort: str = "score",
        per_source_limit: int | None = None,
    ) -> list[dict[str, Any]]:
        join, where, params = self._item_filter_parts(
            category=category,
            channel=channel,
            tier=tier,
            source_id=source_id,
            featured=featured,
            query=query,
            since=since,
        )
        order_by = (
            "i.featured DESC, i.score DESC, i.published_at DESC"
            if sort == "score"
            else "i.published_at DESC, i.score DESC"
        )
        if per_source_limit:
            sql = f"""
                SELECT *
                FROM (
                    SELECT
                        i.*,
                        ROW_NUMBER() OVER (
                            PARTITION BY i.source_id
                            ORDER BY {order_by}
                        ) AS source_rank
                    FROM items i
                    {join}
                    {where}
                )
                WHERE source_rank <= ?
                ORDER BY {order_by.replace("i.", "")}
                LIMIT ? OFFSET ?
            """
            params.extend([per_source_limit, limit, offset])
        else:
            sql = f"""
                SELECT i.*
                FROM items i
                {join}
                {where}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
            """
            params.extend([limit, offset])
        with self.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_dict(row, include_raw=include_raw) for row in rows]

    def count_items(
        self,
        *,
        category: str | None = None,
        channel: str | None = None,
        tier: str | None = None,
        source_id: str | None = None,
        featured: bool | None = None,
        query: str | None = None,
        since: datetime | None = None,
    ) -> int:
        join, where, params = self._item_filter_parts(
            category=category,
            channel=channel,
            tier=tier,
            source_id=source_id,
            featured=featured,
            query=query,
            since=since,
        )
        with self.connection() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS count
                FROM items i
                {join}
                {where}
                """,
                params,
            ).fetchone()
        return int(row["count"])

    def item_facets(
        self,
        *,
        category: str | None = None,
        channel: str | None = None,
        tier: str | None = None,
        source_id: str | None = None,
        featured: bool | None = None,
        query: str | None = None,
        since: datetime | None = None,
        source_limit: int = 8,
    ) -> dict[str, Any]:
        join, where, params = self._item_filter_parts(
            category=category,
            channel=channel,
            tier=tier,
            source_id=source_id,
            featured=featured,
            query=query,
            since=since,
        )
        with self.connection() as conn:
            totals = conn.execute(
                f"""
                SELECT
                    COUNT(*) AS total,
                    COALESCE(SUM(i.featured), 0) AS featured,
                    COALESCE(MAX(i.score), 0) AS top_score,
                    MAX(i.published_at) AS latest_published_at
                FROM items i
                {join}
                {where}
                """,
                params,
            ).fetchone()
            categories = conn.execute(
                f"""
                SELECT i.category, COUNT(*) AS count
                FROM items i
                {join}
                {where}
                GROUP BY i.category
                ORDER BY count DESC, i.category
                """,
                params,
            ).fetchall()
            tiers = conn.execute(
                f"""
                SELECT i.source_tier, COUNT(*) AS count
                FROM items i
                {join}
                {where}
                GROUP BY i.source_tier
                ORDER BY i.source_tier
                """,
                params,
            ).fetchall()
            sources = conn.execute(
                f"""
                SELECT
                    i.source_id,
                    i.source_name,
                    i.source_tier,
                    i.category,
                    COUNT(*) AS count,
                    MAX(i.score) AS top_score
                FROM items i
                {join}
                {where}
                GROUP BY i.source_id, i.source_name, i.source_tier, i.category
                ORDER BY count DESC, top_score DESC, i.source_name
                LIMIT ?
                """,
                [*params, source_limit],
            ).fetchall()
        return {
            "total": int(totals["total"] or 0),
            "featured": int(totals["featured"] or 0),
            "top_score": int(totals["top_score"] or 0),
            "latest_published_at": totals["latest_published_at"],
            "categories": {row["category"]: row["count"] for row in categories},
            "tiers": {row["source_tier"]: row["count"] for row in tiers},
            "top_sources": [dict(row) for row in sources],
        }

    def _item_filter_parts(
        self,
        *,
        category: str | None = None,
        channel: str | None = None,
        tier: str | None = None,
        source_id: str | None = None,
        featured: bool | None = None,
        query: str | None = None,
        since: datetime | None = None,
    ) -> tuple[str, str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        join = ""

        fts_query = normalize_fts_query(query)
        if fts_query:
            join = "JOIN item_search s ON s.guid = i.guid"
            clauses.append("item_search MATCH ?")
            params.append(fts_query)
        if category:
            clauses.append("i.category = ?")
            params.append(category)
        self._append_channel_clause(clauses, params, channel)
        if tier:
            clauses.append("i.source_tier = ?")
            params.append(tier)
        if source_id:
            clauses.append("i.source_id = ?")
            params.append(source_id)
        if featured is not None:
            clauses.append("i.featured = ?")
            params.append(1 if featured else 0)
        if since:
            clauses.append("i.published_at >= ?")
            params.append(since.isoformat())

        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return join, where, params

    def list_clusters(
        self,
        *,
        limit: int = 30,
        category: str | None = None,
        channel: str | None = None,
        tier: str | None = None,
        source_id: str | None = None,
        featured: bool | None = None,
        query: str | None = None,
        since: datetime | None = None,
    ) -> list[dict[str, Any]]:
        join, where, params = self._item_filter_parts(
            category=category,
            channel=channel,
            tier=tier,
            source_id=source_id,
            featured=featured,
            query=query,
            since=since,
        )
        cluster_clause = "i.cluster_key != ''"
        where = f"{where} AND {cluster_clause}" if where else f"WHERE {cluster_clause}"
        params.append(limit)
        with self.connection() as conn:
            clusters = conn.execute(
                f"""
                SELECT
                    i.cluster_key,
                    MAX(i.cluster_title) AS cluster_title,
                    MAX(i.score) AS max_score,
                    MAX(i.featured) AS featured,
                    MAX(i.published_at) AS latest_published_at,
                    COUNT(*) AS item_count,
                    GROUP_CONCAT(DISTINCT i.source_name) AS sources,
                    GROUP_CONCAT(DISTINCT i.category) AS categories,
                    GROUP_CONCAT(DISTINCT i.source_tier) AS tiers
                FROM items i
                {join}
                {where}
                GROUP BY i.cluster_key
                ORDER BY max_score DESC, latest_published_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
            result = []
            for cluster in clusters:
                item_join, item_where, item_params = self._item_filter_parts(
                    category=category,
                    channel=channel,
                    tier=tier,
                    source_id=source_id,
                    featured=featured,
                    query=query,
                    since=since,
                )
                item_where = f"{item_where} AND i.cluster_key = ?" if item_where else "WHERE i.cluster_key = ?"
                item_rows = conn.execute(
                    f"""
                    SELECT i.*
                    FROM items i
                    {item_join}
                    {item_where}
                    ORDER BY score DESC, published_at DESC
                    LIMIT 5
                    """,
                    [*item_params, cluster["cluster_key"]],
                ).fetchall()
                data = dict(cluster)
                data["featured"] = bool(data["featured"])
                data["sources"] = data["sources"].split(",") if data["sources"] else []
                data["categories"] = data["categories"].split(",") if data["categories"] else []
                data["tiers"] = data["tiers"].split(",") if data["tiers"] else []
                data["items"] = [self._row_to_dict(row) for row in item_rows]
                result.append(data)
        return result

    def daily_digest(self, *, day: str | None = None, limit: int = 30, channel: str | None = None) -> dict[str, Any]:
        digest_day, start, end = digest_day_bounds(day)
        clauses = ["i.published_at >= ?", "i.published_at < ?", "i.score >= ?"]
        params: list[Any] = [start, end, DAILY_DIGEST_MIN_SCORE]
        self._append_channel_clause(clauses, params, channel)
        where = " AND ".join(clauses)

        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT i.*
                FROM items i
                WHERE {where}
                ORDER BY score DESC, published_at DESC
                LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
            categories = conn.execute(
                f"""
                SELECT i.category, COUNT(*) AS count
                FROM items i
                WHERE {where}
                GROUP BY i.category
                ORDER BY count DESC
                """,
                params,
            ).fetchall()

        items = [self._row_to_dict(row) for row in rows]
        return {
            "date": digest_day,
            "timezone": digest_timezone_name(),
            "channel": channel,
            "total": sum(row["count"] for row in categories),
            "categories": {row["category"]: row["count"] for row in categories},
            "sections": self._digest_sections(items),
            "items": items,
        }

    def daily_archive(self, *, limit: int = 30, channel: str | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        self._append_channel_clause(clauses, params, channel)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with self.connection() as conn:
            rows = conn.execute(
                f"SELECT i.* FROM items i {where} ORDER BY i.published_at DESC",
                params,
            ).fetchall()

        archive_by_day: dict[str, dict[str, Any]] = {}
        for row in rows:
            day = digest_day_for(row["published_at"])
            if not day:
                continue
            if day not in archive_by_day:
                if len(archive_by_day) >= limit:
                    break
                archive_by_day[day] = {
                    "date": day,
                    "total": 0,
                    "featured": 0,
                    "top_score": 0,
                    "latest_published_at": row["published_at"],
                    "categories": Counter(),
                    "top_item": None,
                }
            entry = archive_by_day[day]
            score = int(row["score"] or 0)
            entry["total"] += 1
            entry["featured"] += int(row["featured"] or 0)
            entry["top_score"] = max(entry["top_score"], score)
            if row["published_at"] > entry["latest_published_at"]:
                entry["latest_published_at"] = row["published_at"]
            entry["categories"][row["category"]] += 1
            top_item = entry["top_item"]
            if (
                top_item is None
                or score > int(top_item["score"] or 0)
                or (score == int(top_item["score"] or 0) and row["published_at"] > top_item["published_at"])
            ):
                entry["top_item"] = row

        archive = []
        for entry in archive_by_day.values():
            category_counts = dict(sorted(entry["categories"].items(), key=lambda item: (-item[1], item[0])))
            archive.append(
                {
                    "date": entry["date"],
                    "total": entry["total"],
                    "featured": entry["featured"],
                    "top_score": entry["top_score"],
                    "latest_published_at": entry["latest_published_at"],
                    "categories": category_counts,
                    "top_item": self._row_to_dict(entry["top_item"]) if entry["top_item"] else None,
                }
            )
        return archive

    def available_digest_dates(self, *, limit: int = 30, channel: str | None = None) -> list[str]:
        clauses: list[str] = []
        params: list[Any] = []
        self._append_channel_clause(clauses, params, channel)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT i.published_at
                FROM items i
                {where}
                ORDER BY i.published_at DESC
                """,
                params,
            ).fetchall()
        dates = []
        seen: set[str] = set()
        for row in rows:
            day = digest_day_for(row["published_at"])
            if not day or day in seen:
                continue
            seen.add(day)
            dates.append(day)
            if len(dates) >= limit:
                break
        return dates

    def stats(self) -> dict[str, Any]:
        with self.connection() as conn:
            total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            featured = conn.execute("SELECT COUNT(*) FROM items WHERE featured = 1").fetchone()[0]
            latest = conn.execute("SELECT MAX(fetched_at) FROM items").fetchone()[0]
            category_rows = conn.execute(
                "SELECT category, COUNT(*) AS count FROM items GROUP BY category ORDER BY count DESC"
            ).fetchall()
            source_rows = conn.execute(
                "SELECT source_name, COUNT(*) AS count FROM items GROUP BY source_name ORDER BY count DESC"
            ).fetchall()
            tier_rows = conn.execute(
                "SELECT source_tier, COUNT(*) AS count FROM items GROUP BY source_tier ORDER BY source_tier"
            ).fetchall()
            last_run = conn.execute(
                "SELECT value FROM metadata WHERE key = 'last_collect_result'"
            ).fetchone()
        return {
            "total": total,
            "featured": featured,
            "latest_fetched_at": latest,
            "categories": {row["category"]: row["count"] for row in category_rows},
            "sources": {row["source_name"]: row["count"] for row in source_rows},
            "tiers": {row["source_tier"]: row["count"] for row in tier_rows},
            "last_collect_result": json.loads(last_run["value"]) if last_run else None,
        }

    def set_metadata(self, key: str, value: Any) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO metadata(key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    updated_at=excluded.updated_at
                """,
                (key, json.dumps(value, ensure_ascii=False), utc_now().isoformat()),
            )

    def record_collect_run(self, result: dict[str, Any]) -> None:
        with self.connection() as conn:
            conn.execute(
                """
                INSERT INTO collect_runs (
                    started_at, finished_at, fetched, inserted, updated, unchanged,
                    succeeded_sources, failed_sources, source_results, errors
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result["started_at"],
                    result["finished_at"],
                    int(result["fetched"]),
                    int(result.get("inserted", 0)),
                    int(result.get("updated", 0)),
                    int(result.get("unchanged", 0)),
                    int(result["succeeded_sources"]),
                    int(result["failed_sources"]),
                    json.dumps(result.get("source_results", []), ensure_ascii=False),
                    json.dumps(result.get("errors", []), ensure_ascii=False),
                ),
            )

    def collect_runs(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM collect_runs
                ORDER BY finished_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        result = []
        for row in rows:
            data = dict(row)
            data["source_results"] = json.loads(data["source_results"] or "[]")
            data["errors"] = json.loads(data["errors"] or "[]")
            result.append(data)
        return result

    def source_health(self, *, runs: int = 20) -> dict[str, Any]:
        history = self.collect_runs(limit=runs)
        sources: dict[str, dict[str, Any]] = {}
        for run in history:
            for source in run["source_results"]:
                source_id = source["id"]
                row = sources.setdefault(
                    source_id,
                    {
                        "id": source_id,
                        "name": source["name"],
                        "tier": source["tier"],
                        "category": source["category"],
                        "runs": 0,
                        "successes": 0,
                        "skipped": 0,
                        "failures": 0,
                        "fetched_total": 0,
                        "last_ok": None,
                        "last_error": "",
                        "last_skip_reason": "",
                        "duration_ms_total": 0,
                        "duration_runs": 0,
                        "last_duration_ms": None,
                    },
                )
                row["runs"] += 1
                if source.get("status") == "skipped":
                    row["skipped"] += 1
                    row["last_skip_reason"] = row["last_skip_reason"] or source.get("reason", "")
                    continue
                duration_ms = source.get("duration_ms")
                if duration_ms is not None:
                    row["duration_ms_total"] += int(duration_ms)
                    row["duration_runs"] += 1
                    if row["last_duration_ms"] is None:
                        row["last_duration_ms"] = int(duration_ms)
                row["fetched_total"] += int(source.get("fetched") or 0)
                if source.get("ok"):
                    row["successes"] += 1
                    row["last_ok"] = row["last_ok"] or run["finished_at"]
                else:
                    row["failures"] += 1
                    row["last_error"] = row["last_error"] or source.get("error", "")
        for row in sources.values():
            row["success_rate"] = row["successes"] / row["runs"] if row["runs"] else 0
            row["avg_fetched"] = row["fetched_total"] / row["runs"] if row["runs"] else 0
            row["avg_duration_ms"] = (
                int(row["duration_ms_total"] / row["duration_runs"]) if row["duration_runs"] else None
            )
            row.pop("duration_ms_total", None)
            row.pop("duration_runs", None)
        ordered = sorted(sources.values(), key=lambda item: (item["success_rate"], -item["failures"], item["name"]))
        return {"runs": history, "sources": ordered, "run_count": len(history)}

    def submit_source(self, *, url: str, name: str, reason: str, contact: str = "") -> dict[str, Any]:
        now = utc_now().isoformat()
        with self.connection() as conn:
            existing = conn.execute("SELECT * FROM source_submissions WHERE url = ?", (url,)).fetchone()
            if existing:
                row = dict(existing)
                row["duplicate"] = True
                return row
            cursor = conn.execute(
                """
                INSERT INTO source_submissions(url, name, reason, contact, status, created_at)
                VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (url, name, reason, contact, now),
            )
            row = conn.execute("SELECT * FROM source_submissions WHERE id = ?", (cursor.lastrowid,)).fetchone()
        data = dict(row)
        data["duplicate"] = False
        return data

    def list_source_submissions(self, *, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self.connection() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM source_submissions
                {where}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def source_wall(self, configured_sources: list[Any], *, include_pending: bool = True) -> dict[str, Any]:
        submissions = self.list_source_submissions(limit=200)
        visible_submissions = [
            item for item in submissions if item["status"] == "approved" or (include_pending and item["status"] == "pending")
        ]
        wall: list[dict[str, Any]] = []
        for index, source in enumerate(configured_sources, start=1):
            wall.append(
                {
                    "number": f"No. {index:03d}",
                    "kind": "configured",
                    "status": "active",
                    "id": source.id,
                    "name": source.name,
                    "url": source.url,
                    "category": source.category,
                    "tier": source.tier,
                    "type": source.type,
                    "reason": "Configured source",
                    "created_at": None,
                }
            )
        offset = len(wall)
        for index, item in enumerate(sorted(visible_submissions, key=lambda row: row["created_at"]), start=1):
            wall.append(
                {
                    "number": f"No. {offset + index:03d}",
                    "kind": "submission",
                    "status": item["status"],
                    "id": item["id"],
                    "name": item["name"],
                    "url": item["url"],
                    "category": "suggested",
                    "tier": "candidate",
                    "type": "candidate",
                    "reason": item["reason"],
                    "created_at": item["created_at"],
                }
            )
        return {
            "total": len(wall),
            "configured": len(configured_sources),
            "approved_submissions": sum(1 for item in submissions if item["status"] == "approved"),
            "pending_submissions": sum(1 for item in submissions if item["status"] == "pending"),
            "sources": wall,
        }

    def record_feedback(self, *, message: str, contact: str = "") -> dict[str, Any]:
        now = utc_now().isoformat()
        with self.connection() as conn:
            cursor = conn.execute(
                """
                INSERT INTO feedback(message, contact, created_at)
                VALUES (?, ?, ?)
                """,
                (message, contact, now),
            )
            row = conn.execute("SELECT * FROM feedback WHERE id = ?", (cursor.lastrowid,)).fetchone()
        return dict(row)

    def metadata(self, key: str) -> Any | None:
        with self.connection() as conn:
            row = conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else None

    @staticmethod
    def _item_values(item: NewsItem) -> tuple[Any, ...]:
        return (
            item.guid,
            item.source_id,
            item.source_name,
            item.source_type,
            item.category,
            item.title,
            item.summary,
            item.url,
            item.published_at.isoformat(),
            item.fetched_at.isoformat(),
            item.score,
            1 if item.featured else 0,
            1 if item.featured else 0,
            json.dumps(item.tags, ensure_ascii=False),
            item.source_tier,
            item.reason,
            json.dumps(item.score_breakdown or {}, ensure_ascii=False),
            item.cluster_key,
            item.cluster_title,
            item.author,
            json.dumps(item.raw or {}, ensure_ascii=False),
        )

    @staticmethod
    def _row_to_dict(row: sqlite3.Row, *, include_raw: bool = False) -> dict[str, Any]:
        data = dict(row)
        data.pop("source_rank", None)
        data["featured"] = bool(data["featured"])
        if "featured_candidate" in data:
            data["featured_candidate"] = bool(data["featured_candidate"])
        data["tags"] = json.loads(data["tags"] or "[]")
        data["score_breakdown"] = json.loads(data.get("score_breakdown") or "{}")
        raw = json.loads(data.get("raw") or "{}")
        engagement = raw.get("engagement") if isinstance(raw, dict) else None
        data["engagement"] = engagement if isinstance(engagement, dict) else None
        if include_raw:
            data["raw"] = raw
        else:
            data.pop("raw", None)
        return data

    @staticmethod
    def _digest_sections(items: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
        sections = {
            "official": [],
            "releases": [],
            "custom_nodes_workflows": [],
            "models": [],
            "video_image_models": [],
            "creator_deep_dives": [],
            "community": [],
        }
        for item in items:
            tags = set(item["tags"])
            if item["category"] == "official" or item["source_tier"] == "T1":
                sections["official"].append(item)
            if "release" in item["title"].lower() or item["source_type"] == "github_releases":
                sections["releases"].append(item)
            if {"custom-nodes", "workflow", "plugin"} & tags:
                sections["custom_nodes_workflows"].append(item)
            if "model" in tags:
                sections["models"].append(item)
            if {"model", "video", "image-generation", "quantization"} & tags:
                sections["video_image_models"].append(item)
            if "deep-dive" in tags:
                sections["creator_deep_dives"].append(item)
            if item["category"] == "community":
                sections["community"].append(item)
        return sections

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> bool:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            return True
        return False

    @staticmethod
    def _append_channel_clause(clauses: list[str], params: list[Any], channel: str | None) -> None:
        if not channel:
            return
        if channel == "official":
            clauses.append("(i.category = 'official' OR i.source_tier = 'T1')")
        elif channel == "github":
            clauses.append("i.source_type LIKE 'github_%'")
        elif channel == "rss":
            clauses.append("i.source_type = 'rss'")
        elif channel == "community":
            clauses.append("i.category = 'community'")
        elif channel == "releases":
            clauses.append("(i.source_type = 'github_releases' OR lower(i.title) LIKE ?)")
            params.append("%release%")
        elif channel == "x":
            clauses.append("i.source_type = 'x_search'")
        elif channel == "bilibili":
            clauses.append("i.source_type = 'bilibili_search'")
        elif channel == "youtube":
            clauses.append("i.source_type IN ('youtube_search', 'youtube_rss')")
        elif channel == "models":
            clauses.append("i.source_type IN ('huggingface_models', 'civitai_models')")
        elif channel == "discord":
            clauses.append("i.source_type = 'discord_feed'")
        elif channel == "forum":
            clauses.append("i.source_type IN ('forum_json', 'json_feed')")

    @staticmethod
    def _merge_existing_enrichment(row: sqlite3.Row | None, item: NewsItem) -> NewsItem:
        if row is None:
            return item
        try:
            existing_raw = json.loads(row["raw"] or "{}")
        except (KeyError, TypeError, json.JSONDecodeError):
            return item
        if not isinstance(existing_raw, dict):
            return item
        raw = dict(item.raw or {})
        merged = False
        for key in ("llm", "llm_triage"):
            value = existing_raw.get(key)
            if value and not raw.get(key):
                raw[key] = value
                merged = True
        if not merged:
            return item
        return replace(item, raw=raw)

    @staticmethod
    def _apply_stored_triage(item: NewsItem) -> NewsItem:
        triage = (item.raw or {}).get("llm_triage")
        if not isinstance(triage, dict):
            return item
        score, featured, reason, cluster_key, cluster_title = apply_llm_triage(
            score=item.score,
            featured=item.featured,
            reason=item.reason,
            cluster_key=item.cluster_key,
            cluster_title=item.cluster_title,
            triage=triage,
        )
        if (score, featured, reason, cluster_key, cluster_title) == (
            item.score,
            item.featured,
            item.reason,
            item.cluster_key,
            item.cluster_title,
        ):
            return item
        return replace(
            item,
            score=score,
            featured=featured,
            reason=reason,
            cluster_key=cluster_key,
            cluster_title=cluster_title,
        )

    @staticmethod
    def _search_row_changed(row: sqlite3.Row, item: NewsItem) -> bool:
        return (
            row["title"] != item.title
            or row["summary"] != item.summary
            or row["tags"] != json.dumps(item.tags, ensure_ascii=False)
        )

    @staticmethod
    def _row_changed(row: sqlite3.Row, item: NewsItem) -> bool:
        expected = {
            "title": item.title,
            "summary": item.summary,
            "url": item.url,
            "published_at": item.published_at.isoformat(),
            "score": item.score,
            "featured_candidate": 1 if item.featured else 0,
            "tags": json.dumps(item.tags, ensure_ascii=False),
            "source_tier": item.source_tier,
            "reason": item.reason,
            "score_breakdown": json.dumps(item.score_breakdown or {}, ensure_ascii=False),
            "cluster_key": item.cluster_key,
            "cluster_title": item.cluster_title,
            "author": item.author,
        }
        return any(row[key] != value for key, value in expected.items())
