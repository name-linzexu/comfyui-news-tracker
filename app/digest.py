from __future__ import annotations

from typing import Any

from .storage import Storage


def render_markdown_digest(
    storage: Storage,
    *,
    day: str | None = None,
    limit: int = 50,
    channel: str | None = None,
) -> str:
    data = storage.daily_digest(day=day, limit=limit, channel=channel)
    channel_label = f" ({channel})" if channel else ""
    lines = [f"# ComfyUI Daily Digest - {data['date']}{channel_label}", ""]
    lines.append(f"Total: {data['total']}")
    lines.append("")
    section_map = [
        ("Official / Primary", data["sections"].get("official", [])),
        ("Releases", data["sections"].get("releases", [])),
        ("Creator Deep-dives", data["sections"].get("creator_deep_dives", [])),
        ("Custom Nodes / Workflows", data["sections"].get("custom_nodes_workflows", [])),
        ("Models", data["sections"].get("models", [])),
        ("Community", data["sections"].get("community", [])),
    ]
    seen: set[str] = set()
    for section, section_items_raw in section_map:
        section_items = [item for item in section_items_raw if item["guid"] not in seen]
        if not section_items:
            continue
        lines.append(f"## {section}")
        lines.append("")
        for item in section_items:
            seen.add(item["guid"])
            tags = ", ".join(item["tags"])
            lines.append(f"- [{item['title']}]({item['url']})")
            lines.append(
                f"  - source: {item['source_name']} | tier: {item['source_tier']} | "
                f"score: {item['score']} | tags: {tags}"
            )
            if item["reason"]:
                lines.append(f"  - why: {item['reason']}")
            if item["summary"]:
                lines.append(f"  - {item['summary']}")
        lines.append("")
    return "\n".join(lines)


def webhook_payload(
    *,
    digest: dict[str, Any],
    markdown: str,
    collect_result: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": "comfyui_daily_digest",
        "date": digest["date"],
        "title": f"ComfyUI Daily Digest - {digest['date']}",
        "total": digest["total"],
        "categories": digest["categories"],
        "markdown": markdown,
        "digest": digest,
        "refresh": {
            "fetched": collect_result.get("fetched", 0),
            "inserted": collect_result.get("inserted", 0),
            "updated": collect_result.get("updated", 0),
            "unchanged": collect_result.get("unchanged", 0),
            "succeeded_sources": collect_result.get("succeeded_sources", 0),
            "failed_sources": collect_result.get("failed_sources", 0),
            "finished_at": collect_result.get("finished_at"),
        },
    }
