from __future__ import annotations

from datetime import datetime
from email.utils import format_datetime
from html import escape
from typing import Any


def render_rss(
    items: list[dict[str, Any]],
    *,
    site_url: str,
    title: str = "ComfyUI News Tracker",
    description: str = "Curated ComfyUI official, tooling and community updates.",
) -> str:
    rows = []
    for item in items:
        published = parse_iso(item["published_at"])
        rows.append(
            f"""
            <item>
              <title>{escape(item["title"])}</title>
              <link>{escape(item["url"])}</link>
              <guid isPermaLink="false">{escape(item["guid"])}</guid>
              <pubDate>{format_datetime(published)}</pubDate>
              <category>{escape(item["category"])}</category>
              <description>{escape(item["summary"])}</description>
            </item>
            """.strip()
        )
    body = "\n".join(rows)
    return f"""<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
  <channel>
    <title>{escape(title)}</title>
    <link>{escape(site_url)}</link>
    <description>{escape(description)}</description>
    <language>zh-cn</language>
    {body}
  </channel>
</rss>
"""


def render_digest_rss(
    days: list[dict[str, Any]],
    *,
    site_url: str,
    title: str = "ComfyUI Daily Digest Archive",
    description: str = "Daily ComfyUI digest issues with top signal summaries.",
    channel: str | None = None,
) -> str:
    base_url = site_url.rstrip("/")
    query = f"?channel={channel}" if channel else ""
    rows = []
    for day in days:
        date = day["date"]
        top_item = day.get("top_item") or {}
        latest = parse_iso(day["latest_published_at"]) if day.get("latest_published_at") else datetime.now().astimezone()
        categories = ", ".join(f"{key}: {value}" for key, value in (day.get("categories") or {}).items())
        summary_parts = [
            f"Total signals: {day.get('total', 0)}",
            f"Featured: {day.get('featured', 0)}",
            f"Top score: {day.get('top_score', 0)}",
        ]
        if categories:
            summary_parts.append(f"Categories: {categories}")
        if top_item:
            summary_parts.append(f"Top item: {top_item.get('title', '')}")
        rows.append(
            f"""
            <item>
              <title>ComfyUI Daily Digest {escape(date)}</title>
              <link>{escape(f"{base_url}/daily/{date}{query}")}</link>
              <guid isPermaLink="false">comfyui-daily-{escape(date)}{escape(f'-{channel}' if channel else '')}</guid>
              <pubDate>{format_datetime(latest)}</pubDate>
              <category>daily</category>
              <description>{escape(" / ".join(summary_parts))}</description>
            </item>
            """.strip()
        )
    body = "\n".join(rows)
    return f"""<?xml version="1.0" encoding="UTF-8" ?>
<rss version="2.0">
  <channel>
    <title>{escape(title)}</title>
    <link>{escape(site_url)}</link>
    <description>{escape(description)}</description>
    <language>zh-cn</language>
    {body}
  </channel>
</rss>
"""


def render_opml(feeds: list[dict[str, str]], *, title: str = "ComfyUI News Tracker Feeds") -> str:
    outlines = []
    for feed in feeds:
        outlines.append(
            (
                f'<outline text="{escape(feed["title"])}" title="{escape(feed["title"])}" '
                f'type="rss" xmlUrl="{escape(feed["xml_url"])}" htmlUrl="{escape(feed["html_url"])}" />'
            )
        )
    body = "\n      ".join(outlines)
    return f"""<?xml version="1.0" encoding="UTF-8" ?>
<opml version="2.0">
  <head>
    <title>{escape(title)}</title>
  </head>
  <body>
    <outline text="ComfyUI News Tracker" title="ComfyUI News Tracker">
      {body}
    </outline>
  </body>
</opml>
"""


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
