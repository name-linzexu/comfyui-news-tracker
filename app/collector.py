from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from .digest import render_markdown_digest, webhook_payload
from .models import NewsItem
from .settings import settings
from .sources import Fetcher, Source, load_sources, resolve_source_url, source_url_env_name
from .storage import Storage, utc_now


@dataclass(frozen=True)
class CollectResult:
    fetched: int
    saved: int
    inserted: int
    updated: int
    unchanged: int
    sources: int
    succeeded_sources: int
    failed_sources: int
    skipped_sources: int
    errors: list[str]
    source_results: list[dict[str, object]]
    started_at: str
    finished_at: str
    webhook: dict[str, object] | None = None


async def collect_once(
    storage: Storage | None = None,
    *,
    include_types: set[str] | None = None,
    exclude_types: set[str] | None = None,
    source_ids: set[str] | None = None,
    skip_source_ids: set[str] | None = None,
    send_webhook: bool = True,
) -> CollectResult:
    storage = storage or Storage()
    started_at = utc_now()
    all_sources, keywords = load_sources()
    configured_source_count = len(all_sources)
    sources, filtered_results = filter_sources(
        all_sources,
        include_types=include_types,
        exclude_types=exclude_types,
        source_ids=source_ids,
        skip_source_ids=skip_source_ids,
    )
    fetcher = Fetcher()
    errors: list[str] = []
    active_sources, skipped_results = partition_sources(sources)
    skipped_results = [*filtered_results, *skipped_results]
    try:
        tasks = [fetcher.fetch_source(source, keywords) for source in active_sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        await fetcher.close()

    items: list[NewsItem] = []
    succeeded_sources = 0
    source_results: list[dict[str, object]] = []
    source_results.extend(skipped_results)
    for source, result in zip(active_sources, results, strict=False):
        if isinstance(result, Exception):
            error = str(result)
            errors.append(error)
            source_results.append(
                {
                    "id": source.id,
                    "name": source.name,
                    "tier": source.tier,
                    "category": source.category,
                    "ok": False,
                    "status": "failed",
                    "fetched": 0,
                    "error": error,
                }
            )
            continue
        succeeded_sources += 1
        source_results.append(
            {
                "id": source.id,
                "name": source.name,
                "tier": source.tier,
                "category": source.category,
                "ok": True,
                "status": "ok",
                "fetched": len(result),
                "error": "",
            }
        )
        items.extend(result)

    upsert = storage.upsert_items(items)
    rescored = storage.rescore_items({source.id: source for source in all_sources}, keywords)
    result = CollectResult(
        fetched=len(items),
        saved=upsert.changed + rescored,
        inserted=upsert.inserted,
        updated=upsert.updated + rescored,
        unchanged=upsert.unchanged,
        sources=configured_source_count,
        succeeded_sources=succeeded_sources,
        failed_sources=len(errors),
        skipped_sources=len(skipped_results),
        errors=errors,
        source_results=source_results,
        started_at=started_at.isoformat(),
        finished_at=utc_now().isoformat(),
    )
    result_data = result.__dict__
    storage.set_metadata("last_collect_result", result_data)
    storage.record_collect_run(result_data)
    webhook_result = await notify_webhook(storage, result_data) if send_webhook else None
    if webhook_result:
        result_data["webhook"] = webhook_result
        result = CollectResult(**result_data)
        storage.set_metadata("last_collect_result", result_data)
    return result


def collect_sync(
    storage: Storage | None = None,
    *,
    include_types: set[str] | None = None,
    exclude_types: set[str] | None = None,
    source_ids: set[str] | None = None,
    skip_source_ids: set[str] | None = None,
    send_webhook: bool = True,
) -> CollectResult:
    return asyncio.run(
        collect_once(
            storage,
            include_types=include_types,
            exclude_types=exclude_types,
            source_ids=source_ids,
            skip_source_ids=skip_source_ids,
            send_webhook=send_webhook,
        )
    )


def filter_sources(
    sources: list[Source],
    *,
    include_types: set[str] | None = None,
    exclude_types: set[str] | None = None,
    source_ids: set[str] | None = None,
    skip_source_ids: set[str] | None = None,
) -> tuple[list[Source], list[dict[str, object]]]:
    selected: list[Source] = []
    skipped: list[dict[str, object]] = []
    for source in sources:
        reason = ""
        if source_ids and source.id not in source_ids:
            reason = "not selected by source id filter"
        elif skip_source_ids and source.id in skip_source_ids:
            reason = "skipped by source id filter"
        elif include_types and source.type not in include_types:
            reason = "not selected by source type filter"
        elif exclude_types and source.type in exclude_types:
            reason = "skipped by source type filter"

        if reason:
            skipped.append(skipped_source_result(source, reason))
            continue
        selected.append(source)
    return selected, skipped


def partition_sources(sources: list[Source]) -> tuple[list[Source], list[dict[str, object]]]:
    active: list[Source] = []
    skipped: list[dict[str, object]] = []
    for source in sources:
        if source.requires_token and not settings.github_token:
            skipped.append(skipped_source_result(source, "requires GITHUB_TOKEN"))
            continue
        if source.requires_x_token and should_skip_x_source_without_bearer():
            skipped.append(skipped_source_result(source, "requires X_BEARER_TOKEN or running X browser debug endpoint"))
            continue
        if source.type == "youtube_search" and not settings.youtube_api_key:
            skipped.append(skipped_source_result(source, "requires YOUTUBE_API_KEY"))
            continue
        if source.type in {"json_feed", "discord_feed", "forum_json"} and not resolve_source_url(source.url):
            env_name = source_url_env_name(source.url) or "feed URL"
            skipped.append(skipped_source_result(source, f"requires {env_name}"))
            continue
        active.append(source)
    return active, skipped


def skipped_source_result(source: Source, reason: str) -> dict[str, object]:
    return {
        "id": source.id,
        "name": source.name,
        "tier": source.tier,
        "category": source.category,
        "ok": True,
        "status": "skipped",
        "fetched": 0,
        "error": "",
        "reason": reason,
    }


def should_skip_x_source_without_bearer() -> bool:
    if settings.x_bearer_token:
        return False
    mode = settings.x_browser_search.strip().lower()
    if mode in {"on", "true", "1", "yes"}:
        return False
    if mode in {"off", "false", "0", "no"}:
        return True
    return not x_browser_debug_available()


def x_browser_debug_available() -> bool:
    try:
        response = httpx.get(settings.x_browser_debug_url, timeout=1.5)
        response.raise_for_status()
        return bool(response.json().get("webSocketDebuggerUrl"))
    except Exception:
        return False


async def notify_webhook(storage: Storage, collect_result: dict[str, object]) -> dict[str, object] | None:
    if not settings.webhook_url:
        return None
    try:
        dates = storage.available_digest_dates(limit=1)
        day = dates[0] if dates else None
        digest = storage.daily_digest(day=day, limit=50)
        markdown = render_markdown_digest(storage, day=digest["date"], limit=50)
        payload = webhook_payload(digest=digest, markdown=markdown, collect_result=collect_result)
        async with httpx.AsyncClient(
            timeout=settings.webhook_timeout,
            trust_env=not is_loopback_webhook(settings.webhook_url),
        ) as client:
            response = await client.post(settings.webhook_url, json=payload)
            response.raise_for_status()
        return {"ok": True, "url": settings.webhook_url, "status_code": response.status_code, "error": ""}
    except Exception as exc:
        return {"ok": False, "url": settings.webhook_url, "status_code": None, "error": str(exc)}


def is_loopback_webhook(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}
