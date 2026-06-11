from __future__ import annotations

import asyncio
import hashlib
import html
import os
import random
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import parse_qs, quote, urlencode, urljoin, urlsplit, urlunsplit
from typing import Any

import feedparser
import httpx
import yaml
from dateutil.parser import parse as parse_date

from . import vocab
from .models import NewsItem
from .scoring import (
    UNSAFE_OR_LOW_VALUE,
    extract_tags,
    has_social_news_signal,
    has_strong_social_news_signal,
    is_beginner_or_course_marketing_text,
    is_low_value_bilibili_text,
    is_low_value_social_text,
    is_low_value_x_text,
    normalize_text,
    score_breakdown,
    score_item,
)
from .settings import settings
from .storage import utc_now


class SourceFetchError(RuntimeError):
    pass


X_BROWSER_LOCK = asyncio.Lock()

RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
GITHUB_SEARCH_SPACING_SECONDS = 2.0
RATE_LIMIT_MAX_WAIT_SECONDS = 90.0

BILIBILI_WBI_MIXIN_KEY_TABLE = [
    46,
    47,
    18,
    2,
    53,
    8,
    23,
    32,
    15,
    50,
    10,
    31,
    58,
    3,
    45,
    35,
    27,
    43,
    5,
    49,
    33,
    9,
    42,
    19,
    29,
    28,
    14,
    39,
    12,
    38,
    41,
    13,
    37,
    48,
    7,
    16,
    24,
    55,
    40,
    61,
    26,
    17,
    0,
    1,
    60,
    51,
    30,
    4,
    22,
    25,
    54,
    21,
    56,
    59,
    6,
    63,
    57,
    62,
    11,
    36,
    20,
    34,
    44,
    52,
]

BILIBILI_TERMINOLOGY = (
    "ComfyUI",
    "Flux",
    "Wan",
    "Qwen",
    "QwenVL",
    "Qwen Image",
    "Hunyuan",
    "LTX",
    "LTX-Video",
    "Ideogram",
    "LoRA",
    "GGUF",
    "FP8",
    "NF4",
    "ControlNet",
    "IP-Adapter",
    "Diffusers",
    "Stable Diffusion",
    "SDXL",
    "SD3",
    "checkpoint",
    "safetensors",
    "custom node",
    "workflow",
)
BILIBILI_TERMINOLOGY = tuple(dict.fromkeys((*BILIBILI_TERMINOLOGY, *vocab.MODEL_FAMILY_TERMS)))

# The card endpoint risk-controls non-browser user agents (-352) while the
# search/view endpoints tolerate them, so follower lookups use a browser UA.
BILIBILI_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

X_EXTRACT_ARTICLES_JS = r"""
() => {
  const out = [];
  const articles = Array.from(document.querySelectorAll("article"));
  for (const article of articles) {
    const text = (article.innerText || "").trim();
    const time = article.querySelector("time");
    const links = Array.from(article.querySelectorAll("a[href]")).map(a => a.href);
    const statusLinks = links.filter(h => /x\.com\/.+\/status\//.test(h));
    const userLinks = links.filter(h => /^https:\/\/x\.com\/[A-Za-z0-9_]+$/.test(h));
    out.push({
      text,
      datetime: time ? time.getAttribute("datetime") : null,
      statusUrl: statusLinks[0] || null,
      links: statusLinks,
      userLinks
    });
  }
  return out;
}
"""


@dataclass(frozen=True)
class Source:
    id: str
    name: str
    type: str
    url: str
    category: str
    weight: int
    tier: str = "T2"
    requires_token: bool = False
    requires_x_token: bool = False


def load_sources(path: Path = settings.sources_path) -> tuple[list[Source], dict[str, list[str]]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    sources = [
        Source(
            id=item["id"],
            name=item["name"],
            type=item["type"],
            url=item["url"],
            category=item.get("category", "general"),
            weight=int(item.get("weight", 1)),
            tier=str(item.get("tier", "T2")),
            requires_token=bool(item.get("requires_token", False)),
            requires_x_token=bool(item.get("requires_x_token", False)),
        )
        for item in data.get("sources", [])
    ]
    keywords = data.get("keywords", {"include": [], "exclude": []})
    return sources, keywords


def env_csv(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip().lower().lstrip("@") for part in re.split(r"[,;\s]+", value) if part.strip()}


def is_github_search_url(url: str) -> bool:
    parts = urlsplit(url)
    return (parts.hostname or "").lower() == "api.github.com" and parts.path.startswith("/search/")


def is_retryable_response(response: httpx.Response) -> bool:
    return response.status_code in RETRYABLE_STATUS_CODES or is_github_rate_limited(response)


def is_github_rate_limited(response: httpx.Response) -> bool:
    if response.status_code != 403:
        return False
    if (response.request.url.host or "").lower() != "api.github.com":
        return False
    if response.headers.get("x-ratelimit-remaining") == "0":
        return True
    try:
        return "rate limit" in response.text.lower()
    except Exception:
        return False


def retry_wait_seconds(response: httpx.Response, *, default: float) -> float:
    retry_after = response.headers.get("retry-after")
    if retry_after:
        try:
            return min(max(float(retry_after), 0.0), RATE_LIMIT_MAX_WAIT_SECONDS)
        except ValueError:
            pass
    reset = response.headers.get("x-ratelimit-reset")
    if reset:
        try:
            wait = float(reset) - time.time()
            if wait > 0:
                return min(wait + 1.0, RATE_LIMIT_MAX_WAIT_SECONDS)
        except ValueError:
            pass
    return min(default, RATE_LIMIT_MAX_WAIT_SECONDS)


class Fetcher:
    def __init__(
        self,
        *,
        bilibili_known_urls: set[str] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        headers = {"User-Agent": settings.user_agent}
        self.client = httpx.AsyncClient(
            headers=headers,
            timeout=settings.request_timeout,
            follow_redirects=True,
            transport=transport,
        )
        self.bilibili_known_urls = bilibili_known_urls or set()
        # GitHub search has strict secondary rate limits; serialize those requests per collect run.
        self._github_search_gate = asyncio.Semaphore(1)
        self._bilibili_follower_cache: dict[int, int] = {}

    async def close(self) -> None:
        await self.client.aclose()

    async def _get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        ok_statuses: set[int] | frozenset[int] = frozenset(),
    ) -> httpx.Response:
        attempts = max(1, settings.http_retry_attempts)
        delay = 1.5
        for attempt in range(attempts):
            try:
                if is_github_search_url(url):
                    async with self._github_search_gate:
                        response = await self.client.get(url, headers=headers, params=params)
                        await asyncio.sleep(GITHUB_SEARCH_SPACING_SECONDS)
                else:
                    response = await self.client.get(url, headers=headers, params=params)
            except httpx.TransportError:
                if attempt >= attempts - 1:
                    raise
                await asyncio.sleep(delay + random.uniform(0, 0.5))
                delay *= 2
                continue
            if response.status_code in ok_statuses:
                return response
            if attempt < attempts - 1 and is_retryable_response(response):
                await asyncio.sleep(retry_wait_seconds(response, default=delay) + random.uniform(0, 0.5))
                delay *= 2
                continue
            response.raise_for_status()
            return response
        raise SourceFetchError(f"request to {url} failed after {attempts} attempts")

    async def fetch_source(self, source: Source, keywords: dict[str, list[str]]) -> list[NewsItem]:
        try:
            if source.type == "rss":
                return await self._fetch_rss(source, keywords)
            if source.type == "github_releases":
                return await self._fetch_github_releases(source, keywords)
            if source.type == "github_commits":
                return await self._fetch_github_commits(source, keywords)
            if source.type == "github_search_repos":
                return await self._fetch_github_repos(source, keywords)
            if source.type == "github_issues":
                return await self._fetch_github_issues(source, keywords)
            if source.type == "bilibili_search":
                return await self._fetch_bilibili_search_v2(source, keywords)
            if source.type == "x_search":
                return await self._fetch_x_search(source, keywords)
            if source.type == "huggingface_models":
                return await self._fetch_huggingface_models(source, keywords)
            if source.type == "civitai_models":
                return await self._fetch_civitai_models(source, keywords)
            if source.type == "youtube_search":
                return await self._fetch_youtube_search(source, keywords)
            if source.type in {"json_feed", "discord_feed", "forum_json"}:
                return await self._fetch_json_feed(source, keywords)
        except Exception as exc:
            raise SourceFetchError(f"{source.id}: {exc}") from exc
        raise SourceFetchError(f"{source.id}: unsupported source type {source.type}")

    async def _get_json(self, url: str) -> Any:
        response = await self._get(url, headers=self._headers_for(url))
        return response.json()

    async def _get_text(self, url: str) -> str:
        response = await self._get(url, headers=self._headers_for(url))
        return response.text

    def _headers_for(self, url: str) -> dict[str, str]:
        host = (urlsplit(url).hostname or "").lower()
        if host == "api.github.com" and settings.github_token:
            return {"Authorization": f"Bearer {settings.github_token}"}
        if host.endswith("civitai.com") and settings.civitai_token:
            return {"Authorization": f"Bearer {settings.civitai_token}"}
        return {}

    async def _fetch_rss(self, source: Source, keywords: dict[str, list[str]]) -> list[NewsItem]:
        text = await self._get_text(source.url)
        feed = feedparser.parse(text)
        items = []
        for entry in feed.entries[:40]:
            title = clean_html(entry.get("title", "Untitled"))
            summary = clean_html(entry.get("summary", entry.get("description", "")))
            url = entry.get("link", "")
            published = parse_feed_datetime(entry)
            author = entry.get("author")
            items.append(
                build_item(
                    source=source,
                    title=title,
                    summary=summary,
                    url=url,
                    published_at=published,
                    keywords=keywords,
                    author=author,
                    raw=dict(entry),
                )
            )
        return [item for item in items if item]

    async def _fetch_github_releases(self, source: Source, keywords: dict[str, list[str]]) -> list[NewsItem]:
        data = await self._get_json(source.url)
        items = []
        for release in data[:30]:
            title = clean_html(release.get("name") or release.get("tag_name") or "Release")
            summary = clean_html(release.get("body") or "")
            url = release.get("html_url") or release.get("url") or source.url
            published_at = parse_datetime(release.get("published_at") or release.get("created_at"))
            items.append(
                build_item(
                    source=source,
                    title=f"{source.name}: {title}",
                    summary=summary,
                    url=url,
                    published_at=published_at,
                    keywords=keywords,
                    author=(release.get("author") or {}).get("login"),
                    raw=release,
                )
            )
        return [item for item in items if item]

    async def _fetch_github_commits(self, source: Source, keywords: dict[str, list[str]]) -> list[NewsItem]:
        data = await self._get_json(source.url)
        items = []
        for commit in data[:30]:
            info = commit.get("commit", {})
            message = normalize_text((info.get("message") or "").splitlines()[0])
            if not message:
                continue
            summary = clean_html(info.get("message") or "")
            url = commit.get("html_url") or source.url
            published_at = parse_datetime(((info.get("committer") or {}).get("date")))
            items.append(
                build_item(
                    source=source,
                    title=f"Commit: {message}",
                    summary=summary,
                    url=url,
                    published_at=published_at,
                    keywords=keywords,
                    author=(info.get("author") or {}).get("name"),
                    raw=commit,
                )
            )
        return [item for item in items if item]

    async def _fetch_github_repos(self, source: Source, keywords: dict[str, list[str]]) -> list[NewsItem]:
        data = await self._get_json(source.url)
        items = []
        for repo in data.get("items", [])[:30]:
            title = repo.get("full_name") or repo.get("name") or "GitHub repository"
            summary = clean_html(repo.get("description") or "")
            stars = repo.get("stargazers_count") or 0
            updated = parse_datetime(repo.get("pushed_at") or repo.get("updated_at"))
            item = build_item(
                source=source,
                title=title,
                summary=f"{summary} Stars: {stars}. Language: {repo.get('language') or 'unknown'}.",
                url=repo.get("html_url") or source.url,
                published_at=updated,
                keywords=keywords,
                author=(repo.get("owner") or {}).get("login"),
                raw=repo,
                github_stars=stars,
            )
            if item:
                items.append(item)
        return items

    async def _fetch_github_issues(self, source: Source, keywords: dict[str, list[str]]) -> list[NewsItem]:
        data = await self._get_json(source.url)
        items = []
        for issue in data[:30]:
            if "pull_request" in issue:
                continue
            title = issue.get("title") or "Issue"
            summary = clean_html(issue.get("body") or "")
            item = build_item(
                source=source,
                title=f"Issue: {title}",
                summary=summary,
                url=issue.get("html_url") or source.url,
                published_at=parse_datetime(issue.get("updated_at") or issue.get("created_at")),
                keywords=keywords,
                author=(issue.get("user") or {}).get("login"),
                raw=issue,
            )
            if item:
                items.append(item)
        return items

    async def _fetch_bilibili_search_v2(self, source: Source, keywords: dict[str, list[str]]) -> list[NewsItem]:
        query = parse_source_query(source.url) or "ComfyUI 新模型 OR ComfyUI 节点"
        headers = {
            "Referer": "https://search.bilibili.com/",
            "User-Agent": settings.user_agent,
        }
        if settings.bilibili_cookie:
            headers["Cookie"] = settings.bilibili_cookie
        await self._get("https://www.bilibili.com", headers=headers)
        items: list[NewsItem] = []
        seen: set[str] = set()
        wbi_key = await self._bilibili_wbi_key(headers)
        asr_budget = max(0, settings.bilibili_asr_max_items)
        enrich_gate = asyncio.Semaphore(max(1, settings.bilibili_enrich_concurrency))
        for term in bilibili_search_terms(query):
            term_headers = {**headers, "Referer": f"https://search.bilibili.com/all?keyword={quote(term)}"}
            payload = await self._bilibili_search_payload(term, term_headers, wbi_key)
            videos = (payload.get("data") or {}).get("result", [])[:30]
            if settings.bilibili_asr_enabled:
                # The ASR budget is consumed in order; keep the sequential path when ASR is on.
                for video in videos:
                    item, used_asr = await self._bilibili_video_to_item(
                        source,
                        video,
                        keywords,
                        seen,
                        headers=term_headers,
                        wbi_key=wbi_key,
                        asr_budget=asr_budget,
                    )
                    if used_asr:
                        asr_budget = max(0, asr_budget - 1)
                    if item:
                        items.append(item)
                continue

            async def convert(video: dict[str, Any]) -> NewsItem | None:
                async with enrich_gate:
                    item, _ = await self._bilibili_video_to_item(
                        source,
                        video,
                        keywords,
                        seen,
                        headers=term_headers,
                        wbi_key=wbi_key,
                        asr_budget=0,
                    )
                    return item

            converted = await asyncio.gather(*(convert(video) for video in videos))
            items.extend(item for item in converted if item)
        return sorted(items, key=lambda item: item.published_at, reverse=True)[:120]

    async def _bilibili_search_payload(
        self,
        term: str,
        term_headers: dict[str, str],
        wbi_key: str | None,
    ) -> dict[str, Any]:
        params = bilibili_signed_search_params(term, wbi_key)
        legacy_params = {key: value for key, value in params.items() if key not in {"w_rid", "wts"}}
        response = await self._get(
            "https://api.bilibili.com/x/web-interface/wbi/search/type",
            headers=term_headers,
            params=params,
            ok_statuses={404},
        )
        if response.status_code == 404:
            response = await self._get(
                "https://api.bilibili.com/x/web-interface/search/type",
                headers=term_headers,
                params=legacy_params,
            )
        payload = response.json()
        result_rows = (payload.get("data") or {}).get("result") or []
        if (payload.get("code") != 0 and wbi_key) or (payload.get("code") == 0 and not result_rows):
            try:
                response = await self._get(
                    "https://api.bilibili.com/x/web-interface/search/type",
                    headers=term_headers,
                    params=legacy_params,
                )
                fallback_payload = response.json()
                if fallback_payload.get("code") == 0 or payload.get("code") != 0:
                    payload = fallback_payload
            except httpx.HTTPError:
                if payload.get("code") != 0:
                    raise
        if payload.get("code") != 0:
            raise SourceFetchError(f"bilibili search returned code {payload.get('code')}: {payload.get('message')}")
        return payload

    async def _bilibili_wbi_key(self, headers: dict[str, str]) -> str | None:
        try:
            response = await self._get("https://api.bilibili.com/x/web-interface/nav", headers=headers)
            data = response.json().get("data") or {}
            wbi_img = data.get("wbi_img") or {}
            img_key = Path(urlsplit(wbi_img.get("img_url") or "").path).stem
            sub_key = Path(urlsplit(wbi_img.get("sub_url") or "").path).stem
            key = img_key + sub_key
            if not key:
                return None
            return "".join(key[index] for index in BILIBILI_WBI_MIXIN_KEY_TABLE if index < len(key))[:32]
        except Exception:
            return None

    async def _bilibili_video_detail(self, bvid: str, headers: dict[str, str]) -> dict[str, Any]:
        if not bvid:
            return {}
        try:
            response = await self._get(
                "https://api.bilibili.com/x/web-interface/view",
                headers=headers,
                params={"bvid": bvid},
            )
            payload = response.json()
            if payload.get("code") != 0:
                return {}
            data = payload.get("data")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    async def _bilibili_author_followers(self, mid: Any, headers: dict[str, str]) -> int:
        author_mid = int_or_none(mid)
        if not author_mid:
            return 0
        cached = self._bilibili_follower_cache.get(author_mid)
        if cached is not None:
            return cached
        followers = 0
        try:
            response = await self._get(
                "https://api.bilibili.com/x/web-interface/card",
                headers={**headers, "User-Agent": BILIBILI_BROWSER_UA},
                params={"mid": str(author_mid)},
            )
            payload = response.json()
            if payload.get("code") == 0:
                data = payload.get("data") or {}
                card = data.get("card") if isinstance(data.get("card"), dict) else {}
                followers = max(0, int(data.get("follower") or card.get("fans") or 0))
        except Exception:
            followers = 0
        self._bilibili_follower_cache[author_mid] = followers
        return followers

    async def _bilibili_player_context(
        self,
        bvid: str,
        cid: int | None,
        headers: dict[str, str],
        wbi_key: str | None,
    ) -> dict[str, Any]:
        if not bvid or not cid:
            return {}
        try:
            response = await self._get(
                "https://api.bilibili.com/x/player/wbi/v2",
                headers=headers,
                params=bilibili_signed_params({"bvid": bvid, "cid": str(cid)}, wbi_key),
            )
            payload = response.json()
            if payload.get("code") != 0:
                return {}
            data = payload.get("data")
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    async def _bilibili_subtitle_text(self, player: dict[str, Any], headers: dict[str, str]) -> str:
        if not settings.bilibili_subtitle_text_enabled:
            return ""
        subtitles = bilibili_subtitle_rows(player)
        if not subtitles:
            return ""
        url = str(subtitles[0].get("subtitle_url") or subtitles[0].get("url") or "")
        if not url:
            return ""
        if url.startswith("//"):
            url = f"https:{url}"
        try:
            response = await self._get(url, headers=headers)
            payload = response.json()
            body = payload.get("body") if isinstance(payload, dict) else None
            if not isinstance(body, list):
                return ""
            lines = []
            for row in body:
                if isinstance(row, dict):
                    content = clean_html(str(row.get("content") or ""))
                    if content:
                        lines.append(content)
                if sum(len(line) for line in lines) >= settings.bilibili_subtitle_max_chars:
                    break
            return summarize(" ".join(lines), limit=settings.bilibili_subtitle_max_chars)
        except Exception:
            return ""

    async def _bilibili_asr_context(
        self,
        *,
        url: str,
        bvid: str,
        title: str,
        understanding: dict[str, Any],
        engagement: dict[str, int],
        asr_budget: int,
    ) -> tuple[dict[str, Any] | None, bool]:
        if not settings.bilibili_asr_enabled or asr_budget <= 0:
            return None, False
        if not settings.bilibili_asr_command:
            return {"status": "skipped", "reason": "BILIBILI_ASR_COMMAND not set"}, False
        text = f"{title} {understanding.get('term_context') or ''} {understanding.get('summary') or ''}".lower()
        if not bilibili_should_use_asr(text, engagement):
            return None, False
        env = dict(os.environ)
        env.update(
            {
                "BILIBILI_VIDEO_URL": url,
                "BILIBILI_BVID": bvid,
                "BILIBILI_TITLE": title,
                "BILIBILI_TERMS": ", ".join(understanding.get("terms") or []),
            }
        )
        try:
            process = await asyncio.create_subprocess_shell(
                settings.bilibili_asr_command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=settings.bilibili_asr_timeout_seconds,
            )
        except Exception as exc:
            return {"status": "error", "reason": str(exc)}, True
        stdout_text = stdout.decode("utf-8", errors="replace").strip()
        stderr_text = stderr.decode("utf-8", errors="replace").strip()
        if process.returncode != 0:
            return {
                "status": "error",
                "returncode": process.returncode,
                "stderr": summarize(stderr_text, limit=600),
            }, True
        return {"status": "ok", "text": summarize(stdout_text, limit=2400)}, True

    async def _bilibili_video_to_item(
        self,
        source: Source,
        video: dict[str, Any],
        keywords: dict[str, list[str]],
        seen: set[str],
        *,
        headers: dict[str, str],
        wbi_key: str | None,
        asr_budget: int,
    ) -> tuple[NewsItem | None, bool]:
        title = clean_html(video.get("title") or "Bilibili video")
        bvid = video.get("bvid")
        arcurl = video.get("arcurl")
        url = arcurl or (f"https://www.bilibili.com/video/{bvid}" if bvid else source.url)
        if url in seen:
            return None, False
        seen.add(url)
        if should_skip_bilibili_enrichment(url, video.get("pubdate"), self.bilibili_known_urls):
            return None, False
        detail = await self._bilibili_video_detail(str(bvid), headers) if settings.bilibili_detail_enabled and bvid else {}
        cid = bilibili_primary_cid(video, detail)
        player = await self._bilibili_player_context(str(bvid), cid, headers, wbi_key) if cid else {}
        subtitle_text = await self._bilibili_subtitle_text(player, headers)
        engagement = bilibili_engagement(video, detail)
        understanding = bilibili_content_understanding(video, detail, player, subtitle_text)
        asr_context, used_asr = await self._bilibili_asr_context(
            url=url,
            bvid=str(bvid or ""),
            title=title,
            understanding=understanding,
            engagement=engagement,
            asr_budget=asr_budget,
        )
        if asr_context:
            understanding["asr"] = asr_context
            asr_text = clean_html(str(asr_context.get("text") or ""))
            if asr_text:
                understanding["summary"] = summarize(
                    f"{understanding.get('summary') or ''} ASR: {asr_text}",
                    limit=1800,
                )
                understanding["terms"] = bilibili_terms_from_text(
                    f"{understanding.get('term_context') or ''} {asr_text}"
                )
        summary = str(understanding.get("summary") or clean_html(video.get("description") or ""))
        author = video.get("author")
        owner = detail.get("owner") if isinstance(detail, dict) else None
        if isinstance(owner, dict):
            author = author or owner.get("name")
        author_mid = (owner or {}).get("mid") if isinstance(owner, dict) else None
        author_mid = author_mid or video.get("mid")
        followers = await self._bilibili_author_followers(author_mid, headers) if author_mid else 0
        engagement = {**engagement, "author_followers": followers}
        trusted_author = author_is_allowlisted(author, settings.bilibili_author_allowlist)
        raw = {
            **video,
            "bilibili_detail": detail,
            "bilibili_player": player,
            "content_understanding": understanding,
            "engagement": engagement,
            "trusted_author": trusted_author,
        }
        item = build_item(
            source=source,
            title=title,
            summary=summary,
            url=url,
            published_at=parse_unix_datetime(detail.get("pubdate") or video.get("pubdate")),
            keywords=keywords,
            author=author,
            raw=raw,
            interaction_count=engagement.get("weighted"),
            trusted_author=trusted_author,
            author_followers=followers,
        )
        return item, used_asr

    async def _fetch_x_search(self, source: Source, keywords: dict[str, list[str]]) -> list[NewsItem]:
        if settings.x_bearer_token:
            return await self._fetch_x_api_search(source, keywords)
        if settings.x_browser_search != "off":
            return await self._fetch_x_browser_search(source, keywords)
        raise SourceFetchError(f"{source.id}: requires X_BEARER_TOKEN or running X browser debug endpoint")

    async def _fetch_huggingface_models(self, source: Source, keywords: dict[str, list[str]]) -> list[NewsItem]:
        items: list[NewsItem] = []
        seen: set[str] = set()
        queries = model_discovery_queries(parse_source_query(source.url), default="ComfyUI")

        async def fetch_query(query: str) -> tuple[str, list[Any]]:
            params = {
                "search": query,
                "sort": "lastModified",
                "direction": "-1",
                "limit": "30",
                "full": "true",
            }
            response = await self._get("https://huggingface.co/api/models", params=params)
            return query, response.json()[:30]

        for query, models in await asyncio.gather(*(fetch_query(query) for query in queries)):
            for model in models:
                if not isinstance(model, dict):
                    continue
                model_id = model.get("modelId") or model.get("id") or ""
                if not model_id or model_id in seen:
                    continue
                seen.add(model_id)
                downloads = int(model.get("downloads") or 0)
                likes = int(model.get("likes") or 0)
                summary = huggingface_model_summary(model, downloads=downloads, likes=likes)
                item = build_item(
                    source=source,
                    title=f"Hugging Face model: {model_id}",
                    summary=summary,
                    url=f"https://huggingface.co/{model_id}",
                    published_at=parse_datetime(model.get("lastModified") or model.get("createdAt")),
                    keywords=keywords,
                    author=str(model_id).split("/")[0],
                    raw={
                        **model,
                        "discovery_query": query,
                        "engagement": {"downloads": downloads, "likes": likes, "weighted": downloads // 50 + likes * 2},
                    },
                    interaction_count=downloads // 50 + likes * 2,
                )
                if item:
                    items.append(item)
        return sorted(items, key=lambda item: item.published_at, reverse=True)[:90]

    async def _fetch_civitai_models(self, source: Source, keywords: dict[str, list[str]]) -> list[NewsItem]:
        items: list[NewsItem] = []
        seen: set[str] = set()
        queries = model_discovery_queries(parse_source_query(source.url), default="ComfyUI")

        async def fetch_query(query: str) -> tuple[str, list[Any]]:
            params = {
                "query": query,
                "sort": "Newest",
                "period": "Month",
                "limit": "50",
            }
            response = await self._get(
                "https://civitai.com/api/v1/models",
                params=params,
                headers=self._headers_for("https://civitai.com/api/v1/models"),
            )
            payload = response.json()
            return query, (payload.get("items") or [])[:40]

        for query, models in await asyncio.gather(*(fetch_query(query) for query in queries)):
            for model in models:
                if not isinstance(model, dict):
                    continue
                model_id = str(model.get("id") or "")
                if not model_id or model_id in seen:
                    continue
                seen.add(model_id)
                title = model.get("name") or "Civitai model"
                creator = (model.get("creator") or {}).get("username")
                stats = model.get("stats") or {}
                versions = model.get("modelVersions") or []
                latest_version = versions[0] if versions and isinstance(versions[0], dict) else {}
                interaction_count = (
                    int(stats.get("downloadCount") or 0) // 20
                    + int(stats.get("thumbsUpCount") or 0) * 2
                    + int(stats.get("commentCount") or 0) * 2
                )
                item = build_item(
                    source=source,
                    title=f"Civitai model: {title}",
                    summary=clean_html(
                        f"{model.get('description') or ''} Type: {model.get('type') or 'model'}. "
                        f"Latest version: {latest_version.get('name') or ''}."
                    ),
                    url=f"https://civitai.com/models/{model.get('id')}",
                    published_at=parse_datetime(
                        latest_version.get("publishedAt")
                        or latest_version.get("createdAt")
                        or model.get("publishedAt")
                        or model.get("createdAt")
                    ),
                    keywords=keywords,
                    author=creator,
                    raw={**model, "discovery_query": query, "engagement": {**stats, "weighted": interaction_count}},
                    interaction_count=interaction_count,
                )
                if item:
                    items.append(item)
        return sorted(items, key=lambda item: item.published_at, reverse=True)[:120]

    async def _fetch_youtube_search(self, source: Source, keywords: dict[str, list[str]]) -> list[NewsItem]:
        query = parse_source_query(source.url) or "ComfyUI model workflow"
        if not settings.youtube_api_key:
            raise SourceFetchError(f"{source.id}: requires YOUTUBE_API_KEY")
        search_params = {
            "key": settings.youtube_api_key,
            "part": "snippet",
            "q": query,
            "type": "video",
            "order": "date",
            "maxResults": "25",
        }
        response = await self._get("https://www.googleapis.com/youtube/v3/search", params=search_params)
        payload = response.json()
        video_ids = [
            str((item.get("id") or {}).get("videoId"))
            for item in payload.get("items", [])
            if (item.get("id") or {}).get("videoId")
        ]
        stats_by_id: dict[str, dict[str, Any]] = {}
        if video_ids:
            stats_response = await self._get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={
                    "key": settings.youtube_api_key,
                    "part": "statistics",
                    "id": ",".join(video_ids),
                },
            )
            stats_by_id = {
                item.get("id"): item.get("statistics") or {}
                for item in stats_response.json().get("items", [])
                if item.get("id")
            }
        channel_ids = list(
            dict.fromkeys(
                str((row.get("snippet") or {}).get("channelId"))
                for row in payload.get("items", [])
                if (row.get("snippet") or {}).get("channelId")
            )
        )
        subscribers_by_channel: dict[str, int] = {}
        if channel_ids:
            try:
                channels_response = await self._get(
                    "https://www.googleapis.com/youtube/v3/channels",
                    params={
                        "key": settings.youtube_api_key,
                        "part": "statistics",
                        "id": ",".join(channel_ids[:50]),
                    },
                )
                subscribers_by_channel = {
                    str(row.get("id")): int((row.get("statistics") or {}).get("subscriberCount") or 0)
                    for row in channels_response.json().get("items", [])
                    if row.get("id")
                }
            except Exception:
                subscribers_by_channel = {}
        items: list[NewsItem] = []
        for row in payload.get("items", []):
            snippet = row.get("snippet") or {}
            video_id = (row.get("id") or {}).get("videoId")
            if not video_id:
                continue
            stats = stats_by_id.get(video_id, {})
            interaction_count = (
                int(stats.get("viewCount") or 0) // 100
                + int(stats.get("likeCount") or 0) * 2
                + int(stats.get("commentCount") or 0) * 2
            )
            followers = subscribers_by_channel.get(str(snippet.get("channelId") or ""), 0)
            item = build_item(
                source=source,
                title=f"YouTube: {clean_html(snippet.get('title') or 'ComfyUI video')}",
                summary=clean_html(snippet.get("description") or ""),
                url=f"https://www.youtube.com/watch?v={video_id}",
                published_at=parse_datetime(snippet.get("publishedAt")),
                keywords=keywords,
                author=snippet.get("channelTitle"),
                raw={
                    **row,
                    "statistics": stats,
                    "engagement": {**stats, "weighted": interaction_count, "author_followers": followers},
                },
                interaction_count=interaction_count,
                author_followers=followers,
            )
            if item:
                items.append(item)
        return items

    async def _fetch_json_feed(self, source: Source, keywords: dict[str, list[str]]) -> list[NewsItem]:
        feed_url = resolve_source_url(source.url)
        if not feed_url:
            env_name = source_url_env_name(source.url) or "feed URL"
            raise SourceFetchError(f"{source.id}: requires {env_name}")
        response = await self._get(feed_url, headers={**self._headers_for(feed_url), **json_feed_headers(source)})
        payload = response.json()
        rows = json_feed_rows(payload)
        items: list[NewsItem] = []
        seen: set[str] = set()
        for row in rows[:80]:
            title = json_row_title(row, source)
            summary = json_row_summary(row)
            url = json_row_url(row, feed_url) or source.url
            if url in seen:
                continue
            seen.add(url)
            engagement = json_row_engagement(row)
            interaction_count = interaction_count_from_raw({"engagement": engagement})
            item = build_item(
                source=source,
                title=title,
                summary=summary,
                url=url,
                published_at=json_row_published(row),
                keywords=keywords,
                author=json_row_author(row),
                raw=json_row_raw(row, engagement),
                interaction_count=interaction_count,
            )
            if item:
                items.append(item)
        return items

    async def _fetch_x_api_search(self, source: Source, keywords: dict[str, list[str]]) -> list[NewsItem]:
        query = parse_source_query(source.url)
        if not query:
            query = "ComfyUI (model OR release OR node OR workflow OR Flux OR Wan OR Qwen)"
        headers = {"Authorization": f"Bearer {settings.x_bearer_token}"}
        params = {
            "query": query,
            "max_results": "25",
            "tweet.fields": "created_at,author_id,public_metrics",
            "expansions": "author_id",
            "user.fields": "username,name,public_metrics",
        }
        response = await self._get(
            "https://api.x.com/2/tweets/search/recent",
            params=params,
            headers=headers,
        )
        payload = response.json()
        users = {
            user["id"]: user
            for user in payload.get("includes", {}).get("users", [])
            if isinstance(user, dict) and user.get("id")
        }
        items = []
        for tweet in payload.get("data", [])[:25]:
            text = clean_html(tweet.get("text") or "")
            if not text:
                continue
            user = users.get(tweet.get("author_id") or "", {})
            username = user.get("username") or tweet.get("author_id") or "x"
            url = f"https://x.com/{username}/status/{tweet.get('id')}"
            metrics = tweet.get("public_metrics") or {}
            interaction_count = x_interaction_count(metrics)
            trusted_author = author_is_allowlisted(username, settings.x_author_allowlist)
            followers = x_author_followers(user)
            raw = {
                **tweet,
                "author": user,
                "engagement": {
                    "likes": int(metrics.get("like_count") or 0),
                    "reposts": int(metrics.get("retweet_count") or 0),
                    "replies": int(metrics.get("reply_count") or 0),
                    "quotes": int(metrics.get("quote_count") or 0),
                    "weighted": interaction_count,
                    "author_followers": followers,
                },
                "trusted_author": trusted_author,
            }
            item = build_item(
                source=source,
                title=first_sentence(text),
                summary=text,
                url=url,
                published_at=parse_datetime(tweet.get("created_at")),
                keywords=keywords,
                author=username,
                raw=raw,
                interaction_count=interaction_count,
                trusted_author=trusted_author,
                author_followers=followers,
            )
            if item:
                items.append(item)
        return items

    async def _fetch_x_browser_search(self, source: Source, keywords: dict[str, list[str]]) -> list[NewsItem]:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise SourceFetchError(f"{source.id}: install playwright or set X_BEARER_TOKEN") from exc
        query = parse_source_query(source.url)
        if not query:
            query = "ComfyUI model OR release OR node OR workflow OR Flux OR Wan OR Qwen"
        version_url = settings.x_browser_debug_url
        raw_rows: list[dict[str, Any]] = []
        async with X_BROWSER_LOCK:
            try:
                version_response = await self.client.get(version_url, timeout=5)
                version_response.raise_for_status()
            except httpx.HTTPError as exc:
                raise SourceFetchError(
                    f"{source.id}: start scripts/start-x-debug.ps1 and log in to X, or set X_BEARER_TOKEN"
                ) from exc
            ws_url = version_response.json().get("webSocketDebuggerUrl")
            if not ws_url:
                raise SourceFetchError(f"{source.id}: Chrome debug endpoint did not return webSocketDebuggerUrl")
            async with async_playwright() as playwright:
                browser = await playwright.chromium.connect_over_cdp(ws_url)
                try:
                    context = browser.contexts[0] if browser.contexts else await browser.new_context()
                    page = context.pages[0] if context.pages else await context.new_page()
                    terms = x_browser_terms(query)
                    term_scrolls = settings.x_browser_scrolls if len(terms) == 1 else min(settings.x_browser_scrolls, 4)
                    for term in terms:
                        search_query = x_browser_query(term)
                        try:
                            raw_rows.extend(
                                await scrape_x_browser_query(
                                    page=page,
                                    query=search_query,
                                    scrolls=term_scrolls,
                                    wait_ms=settings.x_browser_wait_ms,
                                )
                            )
                        except Exception:
                            continue
                finally:
                    await browser.close()
        if not raw_rows:
            raise SourceFetchError(f"{source.id}: X browser search returned no readable posts")
        return self._x_browser_rows_to_items(source, raw_rows, keywords)

    def _x_browser_rows_to_items(
        self,
        source: Source,
        rows: list[dict[str, Any]],
        keywords: dict[str, list[str]],
    ) -> list[NewsItem]:
        items: list[NewsItem] = []
        seen: set[str] = set()
        for row in sorted(rows, key=lambda item: item.get("datetime") or "", reverse=True):
            url = row.get("statusUrl")
            published = parse_datetime(row.get("datetime"))
            text = clean_x_text(row.get("text") or "")
            if not url or not published or not text or url in seen:
                continue
            seen.add(url)
            author_name, handle = parse_x_author(text)
            author = handle.lstrip("@") or author_name or "x"
            body = tweet_body(text)
            if is_low_value_x_text(body) and not has_social_news_signal(body):
                continue
            trusted_author = author_is_allowlisted(author, settings.x_author_allowlist)
            metrics = parse_x_browser_metrics(text)
            interaction_count = x_interaction_count(metrics)
            raw = {
                **row,
                "engagement": {**metrics, "weighted": interaction_count},
                "trusted_author": trusted_author,
            }
            item = build_item(
                source=source,
                title=first_sentence(body),
                summary=f"{author_name} {handle}: {body}" if author_name or handle else body,
                url=url,
                published_at=published,
                keywords=keywords,
                author=author,
                raw=raw,
                interaction_count=interaction_count,
                trusted_author=trusted_author,
            )
            if item:
                items.append(item)
        return items


def build_item(
    *,
    source: Source,
    title: str,
    summary: str,
    url: str,
    published_at: datetime | None,
    keywords: dict[str, list[str]],
    author: str | None = None,
    raw: dict[str, Any] | None = None,
    github_stars: int | None = None,
    interaction_count: int | None = None,
    trusted_author: bool = False,
    author_followers: int | None = None,
) -> NewsItem | None:
    title = normalize_text(title)
    summary = summarize(summary)
    if not title or not url:
        return None
    if not passes_keywords(title, summary, source, keywords):
        return None
    if is_low_value_t2_item(title, summary, source, raw):
        return None
    if author_followers is None:
        author_followers = author_followers_from_raw(raw)

    tags = extract_tags(title, summary, source.category)
    if source.category == "official":
        tags = sorted({*tags, "official"})
    breakdown = score_breakdown(
        title=title,
        summary=summary,
        source_weight=source.weight,
        source_type=source.type,
        source_tier=source.tier,
        tags=tags,
        github_stars=github_stars,
        interaction_count=interaction_count or interaction_count_from_raw(raw),
        trusted_author=trusted_author or bool((raw or {}).get("trusted_author")),
        author=author,
        source_id=source.id,
        published_at=published_at,
        author_followers=author_followers,
    )
    score = score_item(
        title=title,
        summary=summary,
        source_weight=source.weight,
        source_type=source.type,
        source_tier=source.tier,
        tags=tags,
        github_stars=github_stars,
        interaction_count=interaction_count or interaction_count_from_raw(raw),
        trusted_author=trusted_author or bool((raw or {}).get("trusted_author")),
        author=author,
        source_id=source.id,
        published_at=published_at,
        author_followers=author_followers,
    )
    cluster_key = cluster_key_for(title, summary, url)
    reason = explain_item(
        title=title,
        summary=summary,
        source=source,
        tags=tags,
        github_stars=github_stars,
        trusted_author=trusted_author or bool((raw or {}).get("trusted_author")),
        interaction_count=interaction_count or interaction_count_from_raw(raw),
        author_followers=author_followers,
    )
    featured = is_featured_item(
        score=score,
        source=source,
        title=title,
        summary=summary,
        tags=tags,
        github_stars=github_stars,
    )
    return NewsItem(
        guid=guid_for(url or title, source.id),
        source_id=source.id,
        source_name=source.name,
        source_type=source.type,
        category=source.category,
        title=title,
        summary=summary,
        url=url,
        published_at=published_at or utc_now(),
        fetched_at=utc_now(),
        score=score,
        featured=featured,
        tags=tags,
        source_tier=source.tier,
        reason=reason,
        score_breakdown=breakdown,
        cluster_key=cluster_key,
        cluster_title=cluster_title_for(title),
        author=author,
        raw=raw or {},
    )


def is_featured_item(
    *,
    score: int,
    source: Source,
    title: str,
    summary: str,
    tags: list[str],
    github_stars: int | None = None,
) -> bool:
    text = f"{title} {summary}".lower()
    tag_set = set(tags)
    model_news_tags = {"model", "video", "image-generation", "quantization"}
    node_or_workflow_tags = {"custom-nodes", "workflow"}
    release_words = (
        "release",
        "released",
        "launch",
        "launches",
        "v0.",
        "v1.",
        "v2.",
        "v3.",
        "version",
        "发布",
        "上线",
        "更新",
        "update",
        "updated",
    )
    model_words = (
        "checkpoint",
        "lora",
        "finetune",
        "fine-tune",
        "weights",
        "safetensors",
        "gguf",
        "fp8",
        "quant",
        "qwen",
        "image model",
        "video model",
        "model support",
        "模型",
        "微调",
        "权重",
        "节点",
        "视频模型",
        "图片模型",
        *vocab.MODEL_FAMILY_TERMS,
    )
    low_churn_words = (
        "chore",
        "typo",
        "readme",
        "docs",
        "lint",
        "ci",
        "test",
        "tests",
        "refactor",
        "cleanup",
        "format",
        "translation",
    )
    migration_words = ("migration", "deprecated", "deprecation", "removed", "portable updater")
    performance_words = (
        "performance",
        "speed",
        "faster",
        "optimize",
        "optimization",
        "vram",
        "memory",
        "threaded loader",
        "lowvram",
    )
    social_news_words = (
        "release",
        "released",
        "launch",
        "update",
        "updated",
        "guide",
        "tutorial",
        "workflow",
        "node",
        "nodes",
        "model",
        "lora",
        "gguf",
        "fp8",
        "vram",
        "flux",
        "wan",
        "qwen",
        "ltx",
        "comfyui",
        "发布",
        "更新",
        "教程",
        "工作流",
        "节点",
        "模型",
        "显存",
        "视频",
    )
    update_words = (
        "add",
        "added",
        "support",
        "enable",
        "new",
        "compatible",
        "integrate",
        "adapter",
        "wrapper",
        "loader",
        "node",
        "nodes",
        "optional",
    )
    maintenance_words = (
        "chore",
        "cleanup",
        "remove old",
        "unused",
        "rename",
        "move",
        "category",
        "categories",
        "docs",
        "readme",
        "test",
        "tests",
        "ci",
        "format",
        "translation",
        "updater",
    )
    has_release_signal = any(word in text for word in release_words) or source.type == "github_releases"
    has_model_signal = bool(model_news_tags & tag_set) or any(word in text for word in model_words)
    has_node_signal = bool(node_or_workflow_tags & tag_set)
    has_performance_signal = "performance" in tag_set or any(word in text for word in performance_words)
    has_migration_signal = "breaking" in tag_set and any(word in text for word in migration_words)
    has_user_visible_update = any(word in text for word in update_words)
    is_commit_feed = source.type in {"github_commits", "rss"} and "commit" in source.id
    is_bugfix_only_commit = is_commit_feed and "bugfix" in tag_set and not has_performance_signal
    is_maintenance_churn = any(word in text for word in maintenance_words)
    if is_bugfix_only_commit:
        return False
    if is_maintenance_churn and not has_performance_signal and not any(
        word in text for word in ("add new", "added", "support", "compatible", "release", "v0.", "v1.", "v2.", "v3.")
    ):
        return False
    is_low_churn = any(word in text for word in low_churn_words) and not (
        has_release_signal or has_performance_signal or (has_model_signal and has_user_visible_update)
    )
    if is_low_churn:
        return False
    if source.tier == "T1":
        if is_commit_feed:
            return (
                (score >= 92 and has_model_signal and has_user_visible_update)
                or (score >= 92 and "custom-nodes" in tag_set and has_release_signal)
                or (score >= 90 and has_performance_signal)
                or (score >= 94 and has_migration_signal and ("model" in text or "node" in text or "workflow" in text))
            )
        return score >= 70 and (has_release_signal or has_model_signal or has_node_signal or "official" in tag_set)
    if source.tier == "T1.5":
        if source.type == "github_issues":
            return False
        return score >= 68 and (
            source.type == "github_releases"
            or has_model_signal
            or (has_performance_signal and has_node_signal)
            or (has_node_signal and has_release_signal)
            or any(word in text for word in ("manager", "security"))
    )
    if source.type in {"bilibili_search", "x_search", "discord_feed", "forum_json", "json_feed"}:
        if source.type == "x_search" and is_low_value_x_text(text):
            return False
        if source.type == "bilibili_search" and is_low_value_bilibili_text(text):
            return False
        if source.type == "bilibili_search":
            return (
                score >= 58
                and (has_model_signal or has_release_signal or has_node_signal)
                and any(word in text for word in social_news_words)
                and has_strong_social_news_signal(text)
            )
        if source.type in {"discord_feed", "forum_json", "json_feed"}:
            return (
                score >= 60
                and (has_model_signal or has_release_signal or has_node_signal)
                and any(word in text for word in social_news_words)
                and has_social_news_signal(text)
            )
        return (
            score >= 58
            and (has_model_signal or has_release_signal or has_node_signal)
            and any(word in text for word in social_news_words)
            and has_strong_social_news_signal(text)
        )
    if source.type in {"huggingface_models", "civitai_models"}:
        return score >= 62 and (has_model_signal or has_release_signal)
    if source.type in {"youtube_search", "youtube_rss"}:
        return (
            score >= 62
            and (has_model_signal or has_release_signal or has_node_signal)
            and has_strong_social_news_signal(text)
        )
    if source.type.startswith("github_search"):
        return False
    if source.category == "community":
        return score >= 74 and (
            (has_model_signal or has_node_signal)
            and any(
                word in text
                for word in (
                    "release",
                    "launch",
                    "benchmark",
                    "comparison",
                    "guide",
                    "tutorial",
                    "update",
                    "updated",
                    "compatible",
                    "loader",
                    "发布",
                    "更新",
                    "测试",
                )
            )
        )
    return score >= 80


def passes_keywords(title: str, summary: str, source: Source, keywords: dict[str, list[str]]) -> bool:
    text = f"{title} {summary}".lower()
    includes = [word.lower() for word in keywords.get("include", [])]
    excludes = [word.lower() for word in keywords.get("exclude", [])]
    if source.category in {"official", "tooling", "model_nodes", "models"}:
        include_ok = True
    elif source.type in {"huggingface_models", "civitai_models"}:
        include_ok = any(word in text for word in includes) or any(
            word in text for word in ("flux", "wan", "qwen", "hunyuan", "ltx", "lora", "gguf", "safetensors")
        )
    else:
        include_ok = any(word in text for word in includes)
    return include_ok and not any(word in text for word in excludes)


def is_low_value_t2_item(
    title: str,
    summary: str,
    source: Source,
    raw: dict[str, Any] | None,
) -> bool:
    extra_text = bilibili_content_text(raw) if source.type == "bilibili_search" else ""
    text = f"{title} {summary} {extra_text}".lower()
    if any(word in text for word in UNSAFE_OR_LOW_VALUE):
        return True
    if source.id.endswith("open-model-discovery") and is_low_value_open_model_discovery_item(text, raw, source):
        return True
    if source.id.endswith("open-model-discovery") and not has_visual_open_model_signal(text):
        return True
    if source.type in {"huggingface_models", "civitai_models"} and is_low_value_model_platform_item(text, raw, source):
        return True
    if source.type in {"huggingface_models", "civitai_models"} and is_beginner_or_course_marketing_text(text):
        return True
    if source.category == "community" and source.type == "rss" and is_low_value_social_text(text):
        return True
    if source.type in {
        "bilibili_search",
        "x_search",
        "youtube_search",
        "youtube_rss",
        "discord_feed",
        "forum_json",
        "json_feed",
    } and is_beginner_or_course_marketing_text(text):
        return True
    if source.type == "x_search":
        return is_low_value_x_text(text) and not has_social_news_signal(text)
    if source.type == "bilibili_search":
        if is_low_value_bilibili_text(text):
            return True
        return is_low_value_bilibili_candidate(text, raw)
    if source.type == "youtube_search":
        return is_low_value_social_text(text) and not has_social_news_signal(text)
    if source.type in {"discord_feed", "forum_json", "json_feed"}:
        return is_low_value_social_text(text) and not has_social_news_signal(text)
    if source.tier != "T2" or not source.type.startswith("github_search"):
        return False
    repo = raw or {}
    repo_name = str(repo.get("full_name") or title).lower()
    topics = {str(topic).lower() for topic in repo.get("topics", [])}
    description = str(repo.get("description") or "").lower()
    stars = int(repo.get("stargazers_count") or 0)
    has_name_match = "comfyui" in repo_name or repo_name.startswith("comfy-")
    has_topic_match = "comfyui" in topics
    has_description_match = "comfyui" in description
    has_signal_tag = any(word in text for word in ("custom node", "workflow", "manager", "extension", "node pack"))
    if has_name_match or has_topic_match:
        return False
    if has_description_match and (has_signal_tag or stars >= 10):
        return False
    return True


def is_low_value_bilibili_candidate(text: str, raw: dict[str, Any] | None) -> bool:
    if has_social_news_signal(text) or has_visual_open_model_signal(text):
        return False
    specific_terms = (
        "flux",
        "wan",
        "qwen",
        "qwenvl",
        "ltx",
        "hunyuan",
        "ideogram",
        "lora",
        "gguf",
        "fp8",
        "nf4",
        "checkpoint",
        "safetensors",
        "custom node",
        "workflow",
        "diffusers",
        "controlnet",
        "ip-adapter",
    )
    has_specific_terms = any(term in text for term in specific_terms)
    if has_specific_terms and (raw or {}).get("content_understanding"):
        return False
    engagement = interaction_count_from_raw(raw) or 0
    semantic_terms = (
        "model",
        "node",
        "nodes",
        "workflow",
        "checkpoint",
        "release",
        "update",
        "adapter",
        "plugin",
        "custom node",
    )
    if "comfyui" in text and engagement >= 250 and any(term in text for term in semantic_terms):
        return False
    return True


def is_low_value_model_platform_item(text: str, raw: dict[str, Any] | None, source: Source) -> bool:
    low_value_terms = (
        "prompt builder",
        "prompt generator",
        "prompt helper",
        "prompt assistant",
        "ai prompt generator",
        "ai generation for sd",
        "wildcard pack",
        "wildcards",
        "negative prompt",
    )
    return any(term in text for term in low_value_terms)


def is_low_value_open_model_discovery_item(text: str, raw: dict[str, Any] | None, source: Source) -> bool:
    raw = raw or {}
    if source.id == "civitai-open-model-discovery":
        low_value_terms = (
            "patreon",
            "discord.gg",
            "early access",
            "character ",
            "character)",
            "cosplay",
            "alternate_costume",
            "recommended weight",
            "pony",
            "illustrious",
            "noobai",
            "anime",
            "arknights",
            "style ",
            "style lora",
        )
        high_value_terms = (
            "comfyui",
            "text-to-video",
            "image-to-video",
            "video model",
            "checkpoint",
            "flux",
            "wan",
            "qwen image",
            "hunyuan",
            "ltx",
            "hidream",
            "ideogram",
            "z-image",
        )
        if any(term in text for term in low_value_terms):
            return True
        return not any(term in text for term in high_value_terms)

    if source.id == "huggingface-open-model-discovery":
        model_id = str(raw.get("modelId") or raw.get("id") or "").lower()
        author = model_id.split("/", 1)[0]
        official_authors = vocab.OFFICIAL_MODEL_ORGS | vocab.TRUSTED_CONVERTERS
        downloads = int(raw.get("downloads") or 0)
        likes = int(raw.get("likes") or 0)
        if downloads <= 0 and likes <= 0 and author not in official_authors and "comfyui" not in text:
            return True
    return False


def has_visual_open_model_signal(text: str) -> bool:
    visual_terms = (
        "text-to-image",
        "text to image",
        "image-to-video",
        "image to video",
        "text-to-video",
        "text to video",
        "image generation",
        "video generation",
        "diffusion model",
        "diffusers",
        "stable diffusion",
        "comfyui",
        "controlnet",
        "ip-adapter",
        "checkpoint",
        "lora",
        "sdxl",
        "flux",
        "wan",
        "hunyuan",
        "ltx",
        "qwen image",
        "qwen-image",
    )
    low_value_terms = (
        "uncensored",
        "text-generation",
        "text generation",
        "large language model",
        "language model",
        "llm",
        "mamba",
        "falcon",
    )
    if any(term in text for term in low_value_terms) and not any(
        term in text for term in ("image", "video", "diffusion", "comfyui", "flux", "wan", "lora")
    ):
        return False
    return any(term in text for term in visual_terms)


def guid_for(url: str, source_id: str) -> str:
    digest = hashlib.sha1(canonical_url(url, source_id).encode("utf-8")).hexdigest()
    return digest


def canonical_url(url: str, source_id: str) -> str:
    parts = urlsplit(url.strip())
    path = parts.path.rstrip("/")
    if source_id in {"github-comfyui-search", "github-comfyui-topics"} and parts.netloc.lower() == "github.com":
        path = "/".join(path.split("/")[:3])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, "", ""))


def source_url_env_name(url: str) -> str | None:
    if not url.startswith("env://"):
        return None
    name = url.removeprefix("env://").strip()
    return name or None


def resolve_source_url(url: str) -> str:
    env_name = source_url_env_name(url)
    if env_name:
        return os.getenv(env_name, "").strip()
    return url


def json_feed_headers(source: Source) -> dict[str, str]:
    prefix = re.sub(r"[^A-Z0-9]+", "_", source.id.upper()).strip("_")
    headers: dict[str, str] = {}
    authorization = os.getenv(f"{prefix}_AUTHORIZATION", "").strip()
    token = os.getenv(f"{prefix}_TOKEN", "").strip()
    if authorization:
        headers["Authorization"] = authorization
    elif token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def bilibili_primary_cid(video: dict[str, Any], detail: dict[str, Any]) -> int | None:
    for value in (video.get("cid"), detail.get("cid")):
        cid = int_or_none(value)
        if cid:
            return cid
    pages = detail.get("pages") if isinstance(detail, dict) else None
    if isinstance(pages, list):
        for page in pages:
            if isinstance(page, dict):
                cid = int_or_none(page.get("cid"))
                if cid:
                    return cid
    return None


def bilibili_subtitle_rows(player: dict[str, Any]) -> list[dict[str, Any]]:
    subtitle = player.get("subtitle") if isinstance(player, dict) else None
    subtitles = subtitle.get("subtitles") if isinstance(subtitle, dict) else None
    if not isinstance(subtitles, list):
        return []
    return [row for row in subtitles if isinstance(row, dict)]


def bilibili_engagement(video: dict[str, Any], detail: dict[str, Any] | None = None) -> dict[str, int]:
    stat = detail.get("stat") if isinstance(detail, dict) else None
    stat = stat if isinstance(stat, dict) else {}
    views = first_int(stat.get("view"), video.get("play"))
    likes = first_int(stat.get("like"), video.get("like"))
    coins = first_int(stat.get("coin"), video.get("coins"))
    favorites = first_int(stat.get("favorite"), video.get("favorites"))
    shares = first_int(stat.get("share"), video.get("share"))
    comments = first_int(stat.get("reply"), video.get("review"))
    danmaku = first_int(stat.get("danmaku"), video.get("danmaku"))
    weighted = views // 100 + likes * 2 + coins * 3 + favorites * 3 + shares * 4 + comments * 2 + danmaku // 20
    return {
        "views": views,
        "likes": likes,
        "coins": coins,
        "favorites": favorites,
        "shares": shares,
        "comments": comments,
        "danmaku": danmaku,
        "weighted": weighted,
    }


def bilibili_content_understanding(
    video: dict[str, Any],
    detail: dict[str, Any] | None = None,
    player: dict[str, Any] | None = None,
    subtitle_text: str = "",
) -> dict[str, Any]:
    detail = detail if isinstance(detail, dict) else {}
    player = player if isinstance(player, dict) else {}
    description = clean_html(str(detail.get("desc") or video.get("description") or ""))
    dynamic = clean_html(str(detail.get("dynamic") or ""))
    pages = bilibili_page_titles(detail)
    chapters = bilibili_chapter_titles(player)
    subtitles = bilibili_subtitle_rows(player)
    subtitle_labels = [
        clean_html(str(row.get("lan_doc") or row.get("lan") or "subtitle"))
        for row in subtitles
        if row.get("lan_doc") or row.get("lan")
    ]
    term_context = " ".join(
        part
        for part in [
            clean_html(str(video.get("title") or "")),
            description,
            dynamic,
            " ".join(pages),
            " ".join(chapters),
            subtitle_text,
        ]
        if part
    )
    terms = bilibili_terms_from_text(term_context)
    parts = []
    if terms:
        parts.append(f"Terms: {', '.join(terms)}.")
    if description:
        parts.append(f"Description: {description}")
    if dynamic:
        parts.append(f"Dynamic: {dynamic}")
    if pages:
        parts.append(f"Pages: {' / '.join(pages[:8])}")
    if chapters:
        parts.append(f"Chapters: {' / '.join(chapters[:12])}")
    if subtitle_text:
        parts.append(f"Subtitle: {subtitle_text}")
    elif subtitle_labels:
        parts.append(f"Subtitle available: {', '.join(subtitle_labels[:3])}")
    summary = summarize(" ".join(parts) or description or clean_html(str(video.get("description") or "")), limit=1800)
    return {
        "summary": summary,
        "description": description,
        "dynamic": dynamic,
        "pages": pages,
        "chapters": chapters,
        "subtitle_languages": subtitle_labels,
        "subtitle_text": subtitle_text,
        "subtitle_available": bool(subtitles),
        "terms": terms,
        "term_context": summarize(term_context, limit=2600),
    }


def bilibili_page_titles(detail: dict[str, Any]) -> list[str]:
    pages = detail.get("pages") if isinstance(detail, dict) else None
    if not isinstance(pages, list):
        return []
    titles: list[str] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        title = clean_html(str(page.get("part") or page.get("title") or ""))
        if title:
            titles.append(title)
    return titles


def bilibili_chapter_titles(player: dict[str, Any]) -> list[str]:
    chapters = []
    for key in ("view_points", "viewpoints", "chapters"):
        rows = player.get(key) if isinstance(player, dict) else None
        if isinstance(rows, list):
            chapters.extend(rows)
    titles: list[str] = []
    for row in chapters:
        if not isinstance(row, dict):
            continue
        title = clean_html(str(row.get("content") or row.get("title") or row.get("name") or ""))
        if title:
            titles.append(title)
    return titles


def bilibili_terms_from_text(text: str) -> list[str]:
    value = normalize_text(text).lower()
    terms = []
    for term in BILIBILI_TERMINOLOGY:
        if term.lower() in value:
            terms.append(term)
    return list(dict.fromkeys(terms))


def bilibili_content_text(raw: dict[str, Any] | None) -> str:
    if not isinstance(raw, dict):
        return ""
    understanding = raw.get("content_understanding")
    if not isinstance(understanding, dict):
        return ""
    parts: list[str] = []
    for key in ("summary", "description", "dynamic", "subtitle_text", "term_context"):
        value = understanding.get(key)
        if value:
            parts.append(str(value))
    for key in ("pages", "chapters", "terms"):
        values = understanding.get(key)
        if isinstance(values, list):
            parts.extend(str(value) for value in values if value)
    asr = understanding.get("asr")
    if isinstance(asr, dict) and asr.get("text"):
        parts.append(str(asr["text"]))
    return normalize_text(" ".join(parts))


def should_skip_bilibili_enrichment(url: str, pubdate: Any, known_urls: set[str]) -> bool:
    """Skip re-enriching videos that are already stored with content understanding.

    Videos newer than BILIBILI_REENRICH_HOURS are still refreshed because their
    engagement numbers are still climbing.
    """
    if url not in known_urls:
        return False
    published = parse_unix_datetime(pubdate)
    if published is None:
        return True
    age_hours = (utc_now() - published).total_seconds() / 3600
    return age_hours >= settings.bilibili_reenrich_hours


def bilibili_should_use_asr(text: str, engagement: dict[str, int]) -> bool:
    weighted = int(engagement.get("weighted") or 0)
    if weighted >= settings.bilibili_asr_min_weighted:
        return True
    return has_social_news_signal(text) or has_visual_open_model_signal(text)


def first_int(*values: Any) -> int:
    for value in values:
        parsed = int_or_none(value)
        if parsed is not None:
            return parsed
    return 0


def int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def interaction_count_from_raw(raw: dict[str, Any] | None) -> int | None:
    if not isinstance(raw, dict):
        return None
    engagement = raw.get("engagement")
    if isinstance(engagement, dict):
        value = engagement.get("weighted")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def author_followers_from_raw(raw: dict[str, Any] | None) -> int | None:
    if not isinstance(raw, dict):
        return None
    engagement = raw.get("engagement")
    if isinstance(engagement, dict):
        value = engagement.get("author_followers")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None


def x_author_followers(user: dict[str, Any] | None) -> int:
    if not isinstance(user, dict):
        return 0
    metrics = user.get("public_metrics")
    if not isinstance(metrics, dict):
        return 0
    try:
        return max(0, int(metrics.get("followers_count") or 0))
    except (TypeError, ValueError):
        return 0


def json_feed_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "messages", "posts", "entries"):
        rows = payload.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    topics = (payload.get("topic_list") or {}).get("topics")
    if isinstance(topics, list):
        return [{**row, "_feed_kind": "discourse_topic"} for row in topics if isinstance(row, dict)]
    return []


def json_row_title(row: dict[str, Any], source: Source) -> str:
    for key in ("title", "name", "subject"):
        value = row.get(key)
        if value:
            return clean_html(str(value))
    summary = json_row_summary(row)
    prefix = "Discord" if source.type == "discord_feed" else "Forum"
    return f"{prefix}: {first_sentence(summary) or source.name}"


def json_row_summary(row: dict[str, Any]) -> str:
    for key in ("summary", "description", "content_text", "content_html", "content", "text", "body", "excerpt"):
        value = row.get(key)
        if value:
            return clean_html(str(value))
    return clean_html(str(row.get("title") or row.get("name") or ""))


def json_row_url(row: dict[str, Any], feed_url: str) -> str:
    for key in ("url", "external_url", "html_url", "permalink", "jump_url", "link"):
        value = row.get(key)
        if value:
            return urljoin(feed_url, str(value))
    if row.get("_feed_kind") == "discourse_topic" and row.get("id"):
        base = discourse_base_url(feed_url)
        slug = row.get("slug") or re.sub(r"[^a-z0-9]+", "-", str(row.get("title") or "topic").lower()).strip("-")
        return f"{base}/t/{slug}/{row['id']}"
    return ""


def huggingface_model_summary(model: dict[str, Any], *, downloads: int, likes: int) -> str:
    tags = [str(tag) for tag in model.get("tags") or []]
    card_data = model.get("cardData") if isinstance(model.get("cardData"), dict) else {}
    pipeline = model.get("pipeline_tag") or card_data.get("pipeline_tag")
    license_name = model.get("license") or card_data.get("license")
    base_model = card_data.get("base_model") or card_data.get("base_models")
    parts = []
    if pipeline:
        parts.append(f"Pipeline: {pipeline}")
    if base_model:
        parts.append(f"Base model: {base_model}")
    if license_name:
        parts.append(f"License: {license_name}")
    if tags:
        parts.append("Tags: " + ", ".join(tags[:10]))
    parts.append(f"Downloads: {downloads}")
    parts.append(f"Likes: {likes}")
    return ". ".join(parts) + "."


def discourse_base_url(feed_url: str) -> str:
    parts = urlsplit(feed_url)
    path = re.sub(r"/(?:latest|top|new)\.json$", "", parts.path.rstrip("/"))
    return urlunsplit((parts.scheme, parts.netloc, path, "", "")).rstrip("/")


def json_row_published(row: dict[str, Any]) -> datetime | None:
    for key in ("published_at", "date_published", "updated_at", "date_modified", "last_posted_at", "created_at", "timestamp"):
        published = parse_datetime(str(row.get(key))) if row.get(key) else None
        if published:
            return published
    return None


def json_row_author(row: dict[str, Any]) -> str | None:
    for key in ("author", "user", "creator"):
        value = row.get(key)
        if isinstance(value, dict):
            name = value.get("username") or value.get("name") or value.get("display_name") or value.get("global_name")
            if name:
                return str(name)
        elif value:
            return str(value)
    posters = row.get("posters")
    if isinstance(posters, list) and posters:
        first = posters[0]
        if isinstance(first, dict) and first.get("user_id"):
            return str(first["user_id"])
    return None


def json_row_engagement(row: dict[str, Any]) -> dict[str, int]:
    likes = first_count(row, ("likes", "like_count", "likeCount", "thumbsUpCount", "reaction_count"))
    replies = first_count(row, ("replies", "reply_count", "comments", "comment_count", "posts_count"))
    views = first_count(row, ("views", "view_count", "viewCount"))
    shares = first_count(row, ("shares", "share_count", "reposts", "retweets"))
    reactions = reactions_count(row.get("reactions"))
    weighted = views // 100 + (likes + reactions) * 2 + replies * 2 + shares * 3
    return {
        "views": views,
        "likes": likes,
        "reactions": reactions,
        "replies": replies,
        "shares": shares,
        "weighted": weighted,
    }


def json_row_raw(row: dict[str, Any], engagement: dict[str, int]) -> dict[str, Any]:
    return {
        "id": row.get("id") or row.get("guid") or row.get("message_id"),
        "channel": row.get("channel") or row.get("channel_name"),
        "feed_kind": row.get("_feed_kind") or "json_feed",
        "engagement": engagement,
    }


def first_count(row: dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        if key in row:
            return coerce_count(row.get(key))
    return 0


def reactions_count(value: Any) -> int:
    if isinstance(value, list):
        total = 0
        for item in value:
            if isinstance(item, dict):
                total += coerce_count(item.get("count", 1))
            else:
                total += 1
        return total
    return coerce_count(value)


def coerce_count(value: Any) -> int:
    if value is None:
        return 0
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return parse_compact_count(str(value))


def author_is_allowlisted(author: str | None, allowlist: str | None) -> bool:
    if not author:
        return False
    normalized = author.lower().lstrip("@")
    return normalized in env_csv(allowlist)


def x_interaction_count(metrics: dict[str, Any]) -> int:
    return (
        int(metrics.get("like_count") or metrics.get("likes") or 0)
        + int(metrics.get("retweet_count") or metrics.get("reposts") or 0) * 3
        + int(metrics.get("reply_count") or metrics.get("replies") or 0) * 2
        + int(metrics.get("quote_count") or metrics.get("quotes") or 0) * 2
    )


def parse_x_browser_metrics(text: str) -> dict[str, int]:
    # X browser markup is localized and unstable; this parser only uses visible labels when present.
    metrics = {"likes": 0, "reposts": 0, "replies": 0, "quotes": 0}
    patterns = {
        "likes": r"([\d,.万千kKmM]+)\s+(?:Likes?|喜欢)",
        "reposts": r"([\d,.万千kKmM]+)\s+(?:Reposts?|Retweets?|转发)",
        "replies": r"([\d,.万千kKmM]+)\s+(?:Replies?|回复)",
        "quotes": r"([\d,.万千kKmM]+)\s+(?:Quotes?|引用)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            metrics[key] = parse_compact_count(match.group(1))
    return metrics


def parse_compact_count(value: str) -> int:
    value = value.strip().replace(",", "")
    multipliers = {"k": 1_000, "m": 1_000_000, "万": 10_000, "千": 1_000}
    suffix = value[-1:].lower()
    try:
        if suffix in multipliers:
            return int(float(value[:-1]) * multipliers[suffix])
        return int(float(value))
    except ValueError:
        return 0


def cluster_key_for(title: str, summary: str, url: str) -> str:
    parts = urlsplit(url)
    if parts.netloc.lower() == "github.com":
        segments = [segment for segment in parts.path.split("/") if segment]
        if len(segments) >= 2:
            if len(segments) >= 4 and segments[2].lower() in {"commit", "issues", "pull", "releases"}:
                return "github-event:" + "/".join(segment.lower() for segment in segments[:5])
            return f"github:{segments[0].lower()}/{segments[1].lower()}"
    if parts.netloc.lower() == "huggingface.co":
        segments = [segment for segment in parts.path.split("/") if segment]
        if len(segments) >= 2:
            return f"hf:{segments[0].lower()}/{segments[1].lower()}"
    if "civitai.com" in parts.netloc.lower():
        match = re.search(r"/models/(\d+)", parts.path)
        if match:
            return f"civitai:{match.group(1)}"
    normalized = f"{title} {summary}".lower()
    model_match = vocab.MODEL_FAMILY_RE.search(normalized)
    if model_match:
        return "model:" + normalize_text(model_match.group(1)).replace(" ", "-")
    text = f"{title} {summary}".lower()
    tokens = re.findall(r"[a-z0-9][a-z0-9._-]{2,}", text)
    stop = {
        "comfyui",
        "comfy",
        "workflow",
        "workflows",
        "custom",
        "nodes",
        "node",
        "with",
        "using",
        "release",
        "stars",
        "language",
    }
    signal = [token for token in tokens if token not in stop][:8]
    return "text:" + "-".join(signal[:5])


def cluster_title_for(title: str) -> str:
    text = re.sub(r"^(commit|issue):\s*", "", title, flags=re.IGNORECASE)
    text = re.sub(r"\s*\(#\d+\)\s*$", "", text)
    return normalize_text(text)


def explain_item(
    *,
    title: str,
    summary: str,
    source: Source,
    tags: list[str],
    github_stars: int | None = None,
    trusted_author: bool = False,
    interaction_count: int | None = None,
    author_followers: int | None = None,
) -> str:
    text = f"{title} {summary}".lower()
    reasons: list[str] = []
    if source.tier == "T1":
        reasons.append("primary source")
    elif source.tier == "T1.5":
        reasons.append("high-signal ecosystem source")
    if "official" in tags:
        reasons.append("official update")
    if "breaking" in tags:
        reasons.append("breaking or migration signal")
    if "custom-nodes" in tags:
        reasons.append("custom node signal")
    if "workflow" in tags:
        reasons.append("workflow signal")
    if "model" in tags:
        reasons.append("model or weights signal")
    if "video" in tags:
        reasons.append("video generation signal")
    if "quantization" in tags:
        reasons.append("quantization/runtime signal")
    if "performance" in tags:
        reasons.append("performance signal")
    if "bugfix" in tags:
        reasons.append("fix or issue signal")
    if "release" in text:
        reasons.append("release mention")
    if github_stars and github_stars >= 100:
        reasons.append(f"{github_stars} GitHub stars")
    if trusted_author:
        reasons.append("trusted author")
    if author_followers and author_followers >= 10000:
        reasons.append("influential author")
    if interaction_count and interaction_count >= 100:
        reasons.append("high engagement")
    return ", ".join(reasons[:4]) or "matched ComfyUI keywords"


def clean_html(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = html.unescape(text)
    return normalize_text(text)


def summarize(value: str, limit: int = 360) -> str:
    text = clean_html(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."


def parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parse_date(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (ValueError, TypeError):
        return None


def parse_feed_datetime(entry: Any) -> datetime | None:
    for key in ("published", "updated", "created"):
        value = entry.get(key)
        if not value:
            continue
        try:
            return parsedate_to_datetime(value).astimezone(UTC)
        except (TypeError, ValueError):
            parsed = parse_datetime(value)
            if parsed:
                return parsed
    return None


def parse_unix_datetime(value: Any) -> datetime | None:
    try:
        seconds = int(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    return datetime.fromtimestamp(seconds, tz=UTC)


def parse_source_query(url: str) -> str:
    parsed = urlsplit(url)
    query_pairs = parse_qs(parsed.query)
    raw = (query_pairs.get("q") or query_pairs.get("keyword") or [""])[0]
    return html.unescape(raw).strip()


def model_discovery_queries(value: str, *, default: str) -> list[str]:
    raw = value.strip() or default
    queries = [part.strip() for part in re.split(r"[|;]", raw) if part.strip()]
    if not queries:
        return [default]
    seen: set[str] = set()
    result: list[str] = []
    for query in queries:
        key = query.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(query)
    return result


async def scrape_x_browser_query(page: Any, query: str, scrolls: int, wait_ms: int) -> list[dict[str, Any]]:
    url = "https://x.com/search?q=" + quote(query) + "&src=typed_query&f=live"
    last_exc: Exception | None = None
    for attempt in range(2):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            last_exc = None
            break
        except Exception as exc:
            last_exc = exc
            if attempt < 1:
                await page.wait_for_timeout(2000 * (attempt + 1))
    if last_exc:
        raise last_exc
    await page.wait_for_timeout(5000)

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    last_count = 0
    stale_rounds = 0
    for _ in range(scrolls):
        for row in await page.evaluate(X_EXTRACT_ARTICLES_JS):
            row["query"] = query
            row["text"] = clean_x_text(row.get("text", ""))
            key = row.get("statusUrl") or (str(row.get("datetime")) + "|" + row["text"][:160])
            if not key or key in seen or not row["text"]:
                continue
            seen.add(key)
            rows.append(row)
        await page.mouse.wheel(0, 1800)
        await page.wait_for_timeout(wait_ms)
        if len(rows) == last_count:
            stale_rounds += 1
        else:
            stale_rounds = 0
            last_count = len(rows)
        if stale_rounds >= 4:
            break
    return rows


def x_browser_terms(query: str) -> list[str]:
    query = query.strip()
    base_terms = []
    if query and " or " not in query.lower() and "(" not in query and ")" not in query:
        base_terms.append(query)
    terms = [
        *base_terms,
        "ComfyUI Flux",
        "ComfyUI Wan",
        "ComfyUI Qwen",
        "ComfyUI LTX",
        "ComfyUI LoRA",
        "ComfyUI GGUF",
        "ComfyUI workflow",
    ]
    return list(dict.fromkeys(term for term in terms if term))[:6]


def bilibili_search_terms(query: str) -> list[str]:
    raw_parts = re.split(r"\s+OR\s+|[|,，;；]", query, flags=re.IGNORECASE)
    terms = [re.sub(r"[()]+", " ", part).strip() for part in raw_parts if part.strip()]
    if not terms:
        terms = [query]
    terms.insert(0, "ComfyUI")
    extras = [
        "ComfyUI 新模型",
        "ComfyUI 视频模型",
        "ComfyUI 模型发布",
        "ComfyUI 模型更新",
        "ComfyUI 节点",
        "ComfyUI 节点更新",
        "ComfyUI 节点适配",
        "ComfyUI 工作流",
        "ComfyUI 工作流 教程",
        "ComfyUI Flux",
        "ComfyUI Wan",
        "ComfyUI Qwen",
        "ComfyUI LTX",
        "ComfyUI LoRA",
        "ComfyUI GGUF",
        "ComfyUI FP8",
        "ComfyUI 低显存",
        "ComfyUI 量化",
    ]
    return list(dict.fromkeys([*terms, *extras]))[:20]


def bilibili_signed_search_params(term: str, mixin_key: str | None) -> dict[str, str]:
    params = {
        "search_type": "video",
        "keyword": term,
        "order": "pubdate",
        "page": "1",
        "page_size": "20",
    }
    return bilibili_signed_params(params, mixin_key)


def bilibili_signed_params(params: dict[str, Any], mixin_key: str | None) -> dict[str, str]:
    normalized = {key: str(value) for key, value in params.items() if value is not None}
    if not mixin_key:
        return normalized
    signed = {**normalized, "wts": str(int(time.time()))}
    encoded = urlencode(sorted(signed.items()), quote_via=quote)
    signed["w_rid"] = hashlib.md5(f"{encoded}{mixin_key}".encode("utf-8")).hexdigest()
    return signed


def x_browser_query(term: str) -> str:
    window_until = datetime.now(UTC) + timedelta(days=1)
    window_since = window_until - timedelta(days=10)
    term = term.strip()
    if " since:" in term.lower() or " until:" in term.lower():
        return term
    return f"{term} since:{window_since:%Y-%m-%d} until:{window_until:%Y-%m-%d}"


def clean_x_text(text: str) -> str:
    skip = {
        "Ad",
        "Promoted",
        "Show more",
        "Translate post",
        "Relevant people",
        "Show original",
    }
    lines = []
    for line in (text or "").splitlines():
        line = line.strip()
        if line and line not in skip:
            lines.append(line)
    return "\n".join(lines)


def parse_x_author(text: str) -> tuple[str, str]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 2 and lines[1].startswith("@"):
        return lines[0], lines[1]
    match = re.search(r"@([A-Za-z0-9_]+)", text)
    return (lines[0] if lines else ""), ("@" + match.group(1) if match else "")


def tweet_body(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) >= 2 and lines[1].startswith("@"):
        lines = lines[2:]
    cleaned = " ".join(lines)
    cleaned = re.sub(r"^·\s*\S+\s+", "", cleaned)
    cleaned = re.sub(r"^Replying to @\w+(?: @\w+)*(?: and \d+ others)?\s+", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip() or text


def first_sentence(value: str, limit: int = 120) -> str:
    text = normalize_text(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
