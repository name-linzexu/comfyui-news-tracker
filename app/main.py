from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlencode, urlparse

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .collector import collect_once
from .digest import render_markdown_digest
from .rss import render_digest_rss, render_opml, render_rss
from .settings import settings
from .sources import load_sources
from .storage import Storage


app = FastAPI(title=settings.app_name, version="0.1.0")
storage = Storage()

app.mount("/static", StaticFiles(directory=settings.static_dir), name="static")

CHANNEL_PATTERN = "^(official|github|rss|community|releases|x|bilibili|youtube|models|discord|forum)$"
MODE_PATTERN = "^(selected|all|daily)$"
SORT_PATTERN = "^(score|latest)$"
DEFAULT_NEWS_HOURS = 24 * 7
SELECTED_PER_SOURCE_LIMIT = 4
SKILL_PATH = Path(settings.static_dir).parent / "skills" / "comfyui-news" / "SKILL.md"


class SourceSubmission(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    name: str = Field(min_length=2, max_length=120)
    reason: str = Field(min_length=15, max_length=1000)
    contact: str = Field(default="", max_length=120)


class FeedbackSubmission(BaseModel):
    message: str = Field(min_length=3, max_length=2000)
    contact: str = Field(default="", max_length=120)


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(settings.static_dir / "index.html")


@app.get("/daily", include_in_schema=False)
def daily_page() -> FileResponse:
    return FileResponse(settings.static_dir / "index.html")


@app.get("/daily/{day}", include_in_schema=False)
def dated_daily_page(day: str) -> FileResponse:
    return FileResponse(settings.static_dir / "index.html")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/refresh")
async def refresh(background_tasks: BackgroundTasks, wait: bool = Query(False)) -> dict[str, object]:
    if wait:
        result = await collect_once(storage)
        return result.__dict__
    background_tasks.add_task(collect_once, storage)
    return {"status": "scheduled"}


@app.get("/api/items")
def items(
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    page: Annotated[int | None, Query(ge=1)] = None,
    category: str | None = None,
    channel: str | None = Query(None, pattern=CHANNEL_PATTERN),
    tier: str | None = None,
    source_id: str | None = None,
    featured: bool | None = None,
    q: str | None = None,
    hours: Annotated[int | None, Query(ge=1, le=24 * 90)] = None,
    include_raw: bool = False,
    sort: str = Query("score", pattern=SORT_PATTERN),
) -> dict[str, object]:
    return item_page(
        limit=limit,
        offset=offset,
        page=page,
        category=category,
        channel=channel,
        tier=tier,
        source_id=source_id,
        featured=featured,
        query=q,
        since=relative_since(hours=hours),
        include_raw=include_raw,
        sort=sort,
    )


@app.get("/api/feed")
def feed(
    mode: str = Query("selected", pattern=MODE_PATTERN),
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    day: str | None = None,
    q: str | None = None,
    category: str | None = None,
    tier: str | None = None,
    hours: Annotated[int | None, Query(ge=1, le=24 * 90)] = None,
) -> dict[str, object]:
    if mode == "daily":
        data = storage.daily_digest(day=day, limit=limit)
        return {"mode": mode, **data}
    rows = storage.list_items(
        limit=limit,
        query=q,
        category=category,
        tier=tier,
        featured=True if mode == "selected" else None,
        since=relative_since(hours=hours or (DEFAULT_NEWS_HOURS if mode == "selected" else None)),
        sort="score" if mode == "selected" else "latest",
        per_source_limit=SELECTED_PER_SOURCE_LIMIT if mode == "selected" else None,
    )
    return {"mode": mode, "items": rows, "limit": limit}


@app.get("/api/clusters")
def clusters(
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
    q: str | None = None,
    category: str | None = None,
    channel: str | None = Query(None, pattern=CHANNEL_PATTERN),
    tier: str | None = None,
    source_id: str | None = None,
    featured: bool | None = None,
    hours: Annotated[int | None, Query(ge=1, le=24 * 90)] = None,
) -> dict[str, object]:
    since = datetime.now(UTC) - timedelta(hours=hours) if hours else None
    rows = storage.list_clusters(
        limit=limit,
        category=category,
        channel=channel,
        tier=tier,
        source_id=source_id,
        featured=featured,
        query=q,
        since=since,
    )
    return {"clusters": rows, "limit": limit}


@app.get("/api/digest")
def digest(day: str | None = None, limit: Annotated[int, Query(ge=1, le=100)] = 30) -> dict[str, object]:
    return storage.daily_digest(day=day, limit=limit)


@app.get("/api/daily/latest")
def latest_digest(limit: Annotated[int, Query(ge=1, le=100)] = 30) -> dict[str, object]:
    dates = storage.available_digest_dates(limit=1)
    day = dates[0] if dates else None
    return storage.daily_digest(day=day, limit=limit)


@app.get("/api/daily/dates")
def digest_dates(limit: Annotated[int, Query(ge=1, le=365)] = 30) -> dict[str, object]:
    return {"dates": storage.available_digest_dates(limit=limit)}


@app.get("/api/daily/archive")
def daily_archive(limit: Annotated[int, Query(ge=1, le=365)] = 30) -> dict[str, object]:
    return {"days": storage.daily_archive(limit=limit), "limit": limit}


@app.get("/api/stats")
def stats() -> dict[str, object]:
    sources, _ = load_sources()
    data = storage.stats()
    data["configured_sources"] = [
        {
            "id": source.id,
            "name": source.name,
            "type": source.type,
            "category": source.category,
            "tier": source.tier,
            "weight": source.weight,
        }
        for source in sources
    ]
    return data


@app.get("/api/sources")
def sources() -> dict[str, object]:
    configured, _ = load_sources()
    stats_data = storage.stats()
    counts = stats_data["sources"]
    last_run = stats_data.get("last_collect_result") or {}
    run_by_id = {row["id"]: row for row in last_run.get("source_results", [])}
    by_tier: dict[str, list[dict[str, object]]] = {}
    for source in configured:
        last_source_run = run_by_id.get(source.id)
        row = {
            "id": source.id,
            "name": source.name,
            "type": source.type,
            "category": source.category,
            "tier": source.tier,
            "weight": source.weight,
            "items": counts.get(source.name, 0),
            "last_run": last_source_run,
        }
        by_tier.setdefault(source.tier, []).append(row)
    return {"sources": [item for rows in by_tier.values() for item in rows], "by_tier": by_tier}


@app.get("/api/source-wall")
def source_wall(include_pending: bool = True) -> dict[str, object]:
    configured, _ = load_sources()
    return storage.source_wall(configured, include_pending=include_pending)


@app.post("/api/source-submissions")
def submit_source(payload: SourceSubmission) -> dict[str, object]:
    url = normalize_http_url(payload.url)
    row = storage.submit_source(
        url=url,
        name=payload.name.strip(),
        reason=payload.reason.strip(),
        contact=payload.contact.strip(),
    )
    return {"submission": row}


@app.get("/api/source-submissions")
def source_submissions(
    status: str | None = Query(None, pattern="^(pending|approved|rejected)$"),
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> dict[str, object]:
    return {"submissions": storage.list_source_submissions(status=status, limit=limit)}


@app.get("/api/source-health")
def source_health(runs: Annotated[int, Query(ge=1, le=100)] = 20) -> dict[str, object]:
    return storage.source_health(runs=runs)


@app.post("/api/feedback")
def feedback(payload: FeedbackSubmission) -> dict[str, object]:
    row = storage.record_feedback(message=payload.message.strip(), contact=payload.contact.strip())
    return {"feedback": row}


@app.get("/api/public")
def public_api_index(request: Request) -> dict[str, object]:
    base = str(request.base_url).rstrip("/")
    return {
        "name": "ComfyUI News Tracker Public API",
        "description": "Anonymous local REST/RSS/Skill access for ComfyUI ecosystem signals.",
        "version": app.version,
        "auth": "none",
        "endpoints": {
            "items": f"{base}/api/public/items?mode=selected&take=30&hours={DEFAULT_NEWS_HOURS}",
            "daily": f"{base}/api/public/daily",
            "daily_by_date": f"{base}/api/public/daily/YYYY-MM-DD",
            "dailies": f"{base}/api/public/dailies?take=30",
            "daily_archive": f"{base}/api/public/daily/archive?take=30",
            "briefing": f"{base}/api/public/briefing?hours={DEFAULT_NEWS_HOURS}&take=12",
            "sources": f"{base}/api/public/sources",
            "health": f"{base}/api/public/health",
        },
        "rss": {
            "selected": f"{base}/selected.xml",
            "all": f"{base}/all.xml",
            "daily": f"{base}/daily.xml",
            "digest_archive": f"{base}/digests.xml",
            "opml": f"{base}/feeds.opml",
        },
        "skill": f"{base}/comfyui-skill/",
        "openapi": f"{base}/openapi.json",
    }


@app.get("/api/public/briefing")
def public_briefing(
    request: Request,
    hours: Annotated[int, Query(ge=1, le=24 * 90)] = DEFAULT_NEWS_HOURS,
    take: Annotated[int, Query(ge=1, le=50)] = 12,
    q: str | None = None,
    category: str | None = None,
    channel: str | None = Query(None, pattern=CHANNEL_PATTERN),
    tier: str | None = None,
    source_id: str | None = None,
    featured: bool | None = True,
) -> dict[str, object]:
    since = relative_since(hours=hours)
    items = storage.list_items(
        limit=take,
        category=category,
        channel=channel,
        tier=tier,
        source_id=source_id,
        featured=featured,
        query=q,
        since=since,
        sort="score",
        per_source_limit=SELECTED_PER_SOURCE_LIMIT if featured else None,
    )
    clusters = storage.list_clusters(
        limit=min(take, 8),
        category=category,
        channel=channel,
        tier=tier,
        source_id=source_id,
        featured=featured,
        query=q,
        since=since,
    )
    facets = storage.item_facets(
        category=category,
        channel=channel,
        tier=tier,
        source_id=source_id,
        featured=featured,
        query=q,
        since=since,
    )
    stats_data = storage.stats()
    last_run = stats_data.get("last_collect_result") or {}
    base = str(request.base_url).rstrip("/")
    filters = {
        "q": q,
        "category": category,
        "channel": channel,
        "tier": tier,
        "source_id": source_id,
        "featured": featured,
    }
    briefing = {
        "type": "comfyui_news_briefing",
        "generated_at": datetime.now(UTC).isoformat(),
        "window": {
            "hours": hours,
            "since": since.isoformat(),
            "until": datetime.now(UTC).isoformat(),
        },
        "filters": filters,
        "summary": facets,
        "refresh": compact_refresh(last_run),
        "top_items": [brief_item(item) for item in items],
        "clusters": [brief_cluster(cluster) for cluster in clusters],
        "links": {
            "items": public_items_url(base, hours=hours, take=take, filters=filters),
            "daily": f"{base}/api/public/daily",
            "health": f"{base}/api/public/health",
            "sources": f"{base}/api/public/sources",
        },
    }
    briefing["markdown"] = render_briefing_markdown(briefing)
    return briefing


@app.get("/api/public/items")
def public_items(
    mode: str = Query("selected", pattern="^(selected|all)$"),
    take: Annotated[int | None, Query(ge=1, le=200)] = None,
    limit: Annotated[int | None, Query(ge=1, le=200)] = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    page: Annotated[int | None, Query(ge=1)] = None,
    q: str | None = None,
    category: str | None = None,
    channel: str | None = Query(None, pattern=CHANNEL_PATTERN),
    tier: str | None = None,
    source_id: str | None = None,
    featured: bool | None = None,
    since: datetime | None = None,
    hours: Annotated[int | None, Query(ge=1, le=24 * 90)] = None,
    sort: str | None = Query(None, pattern=SORT_PATTERN),
) -> dict[str, object]:
    effective_limit = take or limit or 50
    effective_featured = featured if featured is not None else (True if mode == "selected" else None)
    effective_sort = sort or ("score" if mode == "selected" else "latest")
    effective_hours = hours if hours is not None else (DEFAULT_NEWS_HOURS if mode == "selected" else None)
    effective_since = relative_since(hours=effective_hours) or since
    response = item_page(
        limit=effective_limit,
        offset=offset,
        page=page,
        category=category,
        channel=channel,
        tier=tier,
        source_id=source_id,
        featured=effective_featured,
        query=q,
        since=effective_since,
        sort=effective_sort,
        per_source_limit=SELECTED_PER_SOURCE_LIMIT if mode == "selected" else None,
    )
    response.update(
        {
            "mode": mode,
            "take": effective_limit,
            "filters": {
                "q": q,
                "category": category,
                "channel": channel,
                "tier": tier,
                "source_id": source_id,
                "featured": effective_featured,
                "since": effective_since.isoformat() if effective_since else None,
                "hours": effective_hours,
                "sort": effective_sort,
            },
        }
    )
    return response


@app.get("/api/public/daily")
def public_daily(
    take: Annotated[int | None, Query(ge=1, le=100)] = None,
    limit: Annotated[int | None, Query(ge=1, le=100)] = None,
    day: str | None = None,
) -> dict[str, object]:
    effective_limit = take or limit or 30
    digest_day = day or latest_digest_day()
    data = storage.daily_digest(day=digest_day, limit=effective_limit)
    data["take"] = effective_limit
    return data


@app.get("/api/public/dailies")
def public_dailies(
    take: Annotated[int | None, Query(ge=1, le=365)] = None,
    limit: Annotated[int | None, Query(ge=1, le=365)] = None,
) -> dict[str, object]:
    effective_limit = take or limit or 30
    return {"dates": storage.available_digest_dates(limit=effective_limit), "take": effective_limit}


@app.get("/api/public/daily/archive")
def public_daily_archive(
    take: Annotated[int | None, Query(ge=1, le=365)] = None,
    limit: Annotated[int | None, Query(ge=1, le=365)] = None,
) -> dict[str, object]:
    effective_limit = take or limit or 30
    return {"days": storage.daily_archive(limit=effective_limit), "take": effective_limit}


@app.get("/api/public/daily/{day}")
def public_daily_by_date(day: str, take: Annotated[int, Query(ge=1, le=100)] = 30) -> dict[str, object]:
    data = storage.daily_digest(day=day, limit=take)
    data["take"] = take
    return data


@app.get("/api/public/sources")
def public_sources(include_pending: bool = True) -> dict[str, object]:
    configured, _ = load_sources()
    return storage.source_wall(configured, include_pending=include_pending)


@app.get("/api/public/health")
def public_health(runs: Annotated[int, Query(ge=1, le=20)] = 5) -> dict[str, object]:
    stats_data = storage.stats()
    health_data = storage.source_health(runs=runs)
    last_run = stats_data.get("last_collect_result") or {}
    return {
        "status": "ok",
        "total": stats_data.get("total", 0),
        "featured": stats_data.get("featured", 0),
        "latest_fetched_at": stats_data.get("latest_fetched_at"),
        "last_refresh": {
            "finished_at": last_run.get("finished_at"),
            "fetched": last_run.get("fetched"),
            "inserted": last_run.get("inserted"),
            "updated": last_run.get("updated"),
            "unchanged": last_run.get("unchanged"),
            "succeeded_sources": last_run.get("succeeded_sources"),
            "failed_sources": last_run.get("failed_sources"),
        },
        "source_health": {
            "run_count": health_data["run_count"],
            "sources": health_data["sources"],
        },
    }


@app.get("/rss.xml", response_class=PlainTextResponse)
def rss(request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 50) -> Response:
    rows = storage.list_items(limit=limit)
    xml = render_rss(rows, site_url=str(request.base_url))
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8")


@app.get("/feed", response_class=PlainTextResponse)
def rss_feed_alias(request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 50) -> Response:
    return rss(request, limit=limit)


@app.get("/rss", response_class=PlainTextResponse)
def rss_alias(request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 50) -> Response:
    return rss(request, limit=limit)


@app.get("/feed.xml", response_class=PlainTextResponse)
def rss_feed_xml_alias(request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 50) -> Response:
    return rss(request, limit=limit)


@app.get("/rss/selected.xml", response_class=PlainTextResponse)
def rss_selected(request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 50) -> Response:
    rows = storage.list_items(
        limit=limit,
        featured=True,
        since=relative_since(hours=DEFAULT_NEWS_HOURS),
        sort="score",
        per_source_limit=SELECTED_PER_SOURCE_LIMIT,
    )
    xml = render_rss(
        rows,
        site_url=str(request.base_url),
        title="ComfyUI Selected Signals",
        description="High-signal ComfyUI updates selected by score and source tier.",
    )
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8")


@app.get("/rss/all.xml", response_class=PlainTextResponse)
def rss_all(request: Request, limit: Annotated[int, Query(ge=1, le=200)] = 100) -> Response:
    rows = storage.list_items(limit=limit, sort="latest")
    xml = render_rss(
        rows,
        site_url=str(request.base_url),
        title="ComfyUI All Signals",
        description="Full ComfyUI signal feed ordered by publish time.",
    )
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8")


@app.get("/rss/daily.xml", response_class=PlainTextResponse)
def rss_daily(request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 30) -> Response:
    data = storage.daily_digest(limit=limit)
    xml = render_rss(
        data["items"],
        site_url=str(request.base_url),
        title=f"ComfyUI Daily Digest {data['date']}",
        description="Daily ComfyUI digest grouped from official, ecosystem, model, workflow and community signals.",
    )
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8")


@app.get("/rss/digests.xml", response_class=PlainTextResponse)
def rss_digest_archive(request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 30) -> Response:
    days = storage.daily_archive(limit=limit)
    xml = render_digest_rss(days, site_url=str(request.base_url))
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8")


@app.get("/selected.xml", response_class=PlainTextResponse)
def rss_selected_short(request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 50) -> Response:
    return rss_selected(request, limit=limit)


@app.get("/all.xml", response_class=PlainTextResponse)
def rss_all_short(request: Request, limit: Annotated[int, Query(ge=1, le=200)] = 100) -> Response:
    return rss_all(request, limit=limit)


@app.get("/daily.xml", response_class=PlainTextResponse)
def rss_daily_short(request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 30) -> Response:
    return rss_daily(request, limit=limit)


@app.get("/digests.xml", response_class=PlainTextResponse)
def rss_digest_archive_short(request: Request, limit: Annotated[int, Query(ge=1, le=100)] = 30) -> Response:
    return rss_digest_archive(request, limit=limit)


@app.get("/rss/feeds.opml", response_class=PlainTextResponse)
def opml_feeds(request: Request) -> Response:
    xml = render_opml(feed_definitions(str(request.base_url)))
    return Response(content=xml, media_type="text/x-opml; charset=utf-8")


@app.get("/feeds.opml", response_class=PlainTextResponse)
def opml_feeds_short(request: Request) -> Response:
    return opml_feeds(request)


@app.get("/api/export/markdown", response_class=PlainTextResponse)
def export_markdown(day: str | None = None) -> str:
    return render_markdown_digest(storage, day=day, limit=50)


@app.get("/skill/comfyui-news/SKILL.md", include_in_schema=False)
def skill_file() -> FileResponse:
    return FileResponse(SKILL_PATH)


@app.get("/skill", include_in_schema=False)
def skill_short_file() -> FileResponse:
    return FileResponse(SKILL_PATH)


@app.get("/skill/", include_in_schema=False)
def skill_short_dir() -> FileResponse:
    return FileResponse(SKILL_PATH)


@app.get("/comfyui-skill/", include_in_schema=False)
def comfyui_skill_dir() -> FileResponse:
    return FileResponse(SKILL_PATH)


@app.get("/comfyui-skill/SKILL.md", include_in_schema=False)
def comfyui_skill_file() -> FileResponse:
    return FileResponse(SKILL_PATH)


def item_page(
    *,
    limit: int = 50,
    offset: int = 0,
    page: int | None = None,
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
) -> dict[str, Any]:
    effective_offset = (page - 1) * limit if page else offset
    rows = storage.list_items(
        limit=limit,
        offset=effective_offset,
        category=category,
        channel=channel,
        tier=tier,
        source_id=source_id,
        featured=featured,
        query=query,
        since=since,
        include_raw=include_raw,
        sort=sort,
        per_source_limit=per_source_limit,
    )
    total = storage.count_items(
        category=category,
        channel=channel,
        tier=tier,
        source_id=source_id,
        featured=featured,
        query=query,
        since=since,
    )
    current_page = (effective_offset // limit) + 1
    pages = max(1, (total + limit - 1) // limit)
    next_offset = effective_offset + limit if effective_offset + limit < total else None
    prev_offset = max(effective_offset - limit, 0) if effective_offset > 0 else None
    return {
        "items": rows,
        "limit": limit,
        "offset": effective_offset,
        "page": current_page,
        "pages": pages,
        "total": total,
        "next_offset": next_offset,
        "prev_offset": prev_offset,
        "next_page": current_page + 1 if current_page < pages else None,
        "prev_page": current_page - 1 if current_page > 1 else None,
    }


def relative_since(*, hours: int | None) -> datetime | None:
    return datetime.now(UTC) - timedelta(hours=hours) if hours else None


def latest_digest_day() -> str | None:
    dates = storage.available_digest_dates(limit=1)
    return dates[0] if dates else None


def compact_refresh(last_run: dict[str, Any]) -> dict[str, object]:
    if not last_run:
        return {"status": "never", "finished_at": None}
    failed = int(last_run.get("failed_sources") or 0)
    skipped = int(last_run.get("skipped_sources") or 0)
    return {
        "status": "partial" if failed else "ok",
        "finished_at": last_run.get("finished_at"),
        "fetched": last_run.get("fetched"),
        "inserted": last_run.get("inserted"),
        "updated": last_run.get("updated"),
        "unchanged": last_run.get("unchanged"),
        "succeeded_sources": last_run.get("succeeded_sources"),
        "failed_sources": failed,
        "skipped_sources": skipped,
    }


def brief_item(item: dict[str, Any]) -> dict[str, object]:
    return {
        "guid": item["guid"],
        "title": item["title"],
        "url": item["url"],
        "summary": item["summary"],
        "published_at": item["published_at"],
        "score": item["score"],
        "featured": item["featured"],
        "source": {
            "id": item["source_id"],
            "name": item["source_name"],
            "type": item["source_type"],
            "tier": item["source_tier"],
            "category": item["category"],
        },
        "reason": item.get("reason", ""),
        "tags": item.get("tags", []),
    }


def brief_cluster(cluster: dict[str, Any]) -> dict[str, object]:
    return {
        "key": cluster["cluster_key"],
        "title": cluster["cluster_title"],
        "max_score": cluster["max_score"],
        "item_count": cluster["item_count"],
        "latest_published_at": cluster["latest_published_at"],
        "sources": cluster["sources"],
        "categories": cluster["categories"],
        "tiers": cluster["tiers"],
        "items": [brief_item(item) for item in cluster.get("items", [])],
    }


def public_items_url(base: str, *, hours: int, take: int, filters: dict[str, Any]) -> str:
    params: dict[str, Any] = {"mode": "all", "take": take, "hours": hours, "sort": "score"}
    for key in ("q", "category", "channel", "tier", "source_id"):
        value = filters.get(key)
        if value:
            params[key] = value
    if filters.get("featured") is not None:
        params["featured"] = str(filters["featured"]).lower()
    return f"{base}/api/public/items?{urlencode(params)}"


def render_briefing_markdown(briefing: dict[str, Any]) -> str:
    summary = briefing["summary"]
    refresh = briefing["refresh"]
    lines = [
        f"# ComfyUI Briefing - last {briefing['window']['hours']}h",
        "",
        f"- Total signals: {summary['total']}",
        f"- Featured: {summary['featured']}",
        f"- Top score: {summary['top_score']}",
        f"- Refresh status: {refresh['status']}",
    ]
    if summary["categories"]:
        category_summary = ", ".join(f"{key}: {value}" for key, value in summary["categories"].items())
        lines.append(f"- Categories: {category_summary}")
    lines.extend(["", "## Top Signals"])
    if briefing["top_items"]:
        for index, item in enumerate(briefing["top_items"], start=1):
            source = item["source"]
            lines.append(
                f"{index}. [{item['title']}]({item['url']}) - "
                f"{source['name']} / {source['tier']} / score {item['score']}"
            )
            if item["reason"]:
                lines.append(f"   - {item['reason']}")
    else:
        lines.append("No matching ComfyUI signals in this window.")
    if briefing["clusters"]:
        lines.extend(["", "## Event Clusters"])
        for cluster in briefing["clusters"]:
            lines.append(f"- {cluster['title']} ({cluster['item_count']} items, top {cluster['max_score']})")
    return "\n".join(lines)


def feed_definitions(base_url: str) -> list[dict[str, str]]:
    base = base_url.rstrip("/")
    return [
        {
            "title": "ComfyUI Selected Signals",
            "xml_url": f"{base}/selected.xml",
            "html_url": base,
        },
        {
            "title": "ComfyUI All Signals",
            "xml_url": f"{base}/all.xml",
            "html_url": base,
        },
        {
            "title": "ComfyUI Daily Items",
            "xml_url": f"{base}/daily.xml",
            "html_url": f"{base}/daily",
        },
        {
            "title": "ComfyUI Daily Digest Archive",
            "xml_url": f"{base}/digests.xml",
            "html_url": f"{base}/daily",
        },
    ]


def normalize_http_url(value: str) -> str:
    url = value.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail="source URL must be http or https")
    return url
