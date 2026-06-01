from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable
from contextlib import contextmanager
from dataclasses import replace
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import NewsItem
from .settings import settings


FTS_TOKEN_PATTERN = re.compile(r"[\w]+", re.UNICODE)


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


def normalize_fts_query(value: str | None) -> str | None:
    if not value:
        return None
    tokens = FTS_TOKEN_PATTERN.findall(value.lower())
    if not tokens:
        return None
    return " ".join(f'"{token}"' for token in tokens[:12])


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
                    SELECT title, summary, url, published_at, score, featured, tags,
                           source_tier, reason, score_breakdown, cluster_key, cluster_title, author, raw
                    FROM items
                    WHERE guid = ?
                    """,
                    (item.guid,),
                ).fetchone()
                item = self._merge_existing_enrichment(existing, item)
                values = self._item_values(item)
                conn.execute(
                    """
                    INSERT INTO items (
                        guid, source_id, source_name, source_type, category, title, summary, url,
                        published_at, fetched_at, score, featured, tags, source_tier, reason,
                        score_breakdown, cluster_key, cluster_title, author, raw
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        featured=excluded.featured,
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

    def rescore_items(self, sources_by_id: dict[str, Any], keywords: dict[str, list[str]]) -> int:
        from .sources import build_item

        changed = 0
        with self.connection() as conn:
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
                    if int(row["score"] or 0) == 0 and not bool(row["featured"]):
                        continue
                    conn.execute(
                        """
                        UPDATE items
                        SET
                            score = 0,
                            featured = 0,
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
                if not self._row_changed(row, item):
                    continue
                conn.execute(
                    """
                    UPDATE items
                    SET
                        score = ?,
                        featured = ?,
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

    def daily_digest(self, *, day: str | None = None, limit: int = 30) -> dict[str, Any]:
        if day:
            start = f"{day}T00:00:00+00:00"
            end = f"{day}T23:59:59+00:00"
        else:
            today = utc_now().date().isoformat()
            start = f"{today}T00:00:00+00:00"
            end = f"{today}T23:59:59+00:00"

        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM items
                WHERE published_at BETWEEN ? AND ?
                ORDER BY score DESC, published_at DESC
                LIMIT ?
                """,
                (start, end, limit),
            ).fetchall()
            categories = conn.execute(
                """
                SELECT category, COUNT(*) AS count
                FROM items
                WHERE published_at BETWEEN ? AND ?
                GROUP BY category
                ORDER BY count DESC
                """,
                (start, end),
            ).fetchall()

        items = [self._row_to_dict(row) for row in rows]
        return {
            "date": start[:10],
            "total": sum(row["count"] for row in categories),
            "categories": {row["category"]: row["count"] for row in categories},
            "sections": self._digest_sections(items),
            "items": items,
        }

    def daily_archive(self, *, limit: int = 30) -> list[dict[str, Any]]:
        with self.connection() as conn:
            day_rows = conn.execute(
                """
                SELECT
                    substr(published_at, 1, 10) AS day,
                    COUNT(*) AS total,
                    SUM(featured) AS featured,
                    MAX(score) AS top_score,
                    MAX(published_at) AS latest_published_at
                FROM items
                GROUP BY day
                ORDER BY day DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            archive = []
            for row in day_rows:
                category_rows = conn.execute(
                    """
                    SELECT category, COUNT(*) AS count
                    FROM items
                    WHERE substr(published_at, 1, 10) = ?
                    GROUP BY category
                    ORDER BY count DESC
                    """,
                    (row["day"],),
                ).fetchall()
                top_item = conn.execute(
                    """
                    SELECT *
                    FROM items
                    WHERE substr(published_at, 1, 10) = ?
                    ORDER BY score DESC, published_at DESC
                    LIMIT 1
                    """,
                    (row["day"],),
                ).fetchone()
                archive.append(
                    {
                        "date": row["day"],
                        "total": int(row["total"] or 0),
                        "featured": int(row["featured"] or 0),
                        "top_score": int(row["top_score"] or 0),
                        "latest_published_at": row["latest_published_at"],
                        "categories": {item["category"]: item["count"] for item in category_rows},
                        "top_item": self._row_to_dict(top_item) if top_item else None,
                    }
                )
        return archive

    def available_digest_dates(self, *, limit: int = 30) -> list[str]:
        with self.connection() as conn:
            rows = conn.execute(
                """
                SELECT substr(published_at, 1, 10) AS day
                FROM items
                GROUP BY day
                ORDER BY day DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [row["day"] for row in rows]

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
                    },
                )
                row["runs"] += 1
                if source.get("status") == "skipped":
                    row["skipped"] += 1
                    row["last_skip_reason"] = row["last_skip_reason"] or source.get("reason", "")
                    continue
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
        data["tags"] = json.loads(data["tags"] or "[]")
        data["score_breakdown"] = json.loads(data.get("score_breakdown") or "{}")
        if include_raw:
            data["raw"] = json.loads(data["raw"] or "{}")
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
            if item["category"] == "community":
                sections["community"].append(item)
        return sections

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

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
        llm = existing_raw.get("llm") if isinstance(existing_raw, dict) else None
        if not llm:
            return item
        raw = dict(item.raw or {})
        raw.setdefault("llm", llm)
        return replace(item, raw=raw)

    @staticmethod
    def _row_changed(row: sqlite3.Row, item: NewsItem) -> bool:
        expected = {
            "title": item.title,
            "summary": item.summary,
            "url": item.url,
            "published_at": item.published_at.isoformat(),
            "score": item.score,
            "featured": 1 if item.featured else 0,
            "tags": json.dumps(item.tags, ensure_ascii=False),
            "source_tier": item.source_tier,
            "reason": item.reason,
            "score_breakdown": json.dumps(item.score_breakdown or {}, ensure_ascii=False),
            "cluster_key": item.cluster_key,
            "cluster_title": item.cluster_title,
            "author": item.author,
        }
        return any(row[key] != value for key, value in expected.items())
