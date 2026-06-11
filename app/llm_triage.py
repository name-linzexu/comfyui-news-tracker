from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any

import httpx

from .scoring import apply_llm_triage
from .settings import settings
from .storage import Storage, utc_now


TRIAGE_PROMPT = """You are a strict editor for a ComfyUI ecosystem news tracker.
Decide whether a candidate item is useful news for experienced ComfyUI users.

High-value items include:
- official releases, breaking changes, security or migration notices
- new or meaningfully updated models, LoRAs, checkpoints, GGUF/FP8/NF4 builds
- ComfyUI custom nodes, manager/front-end/CLI updates, workflow integrations
- performance, VRAM, compatibility, runtime, or benchmark changes with concrete details

Low-value items include:
- beginner tutorials, installation walkthroughs, generic examples, course ads
- posts asking users to comment/reply/DM/join a group to get files
- showcases without a concrete new model, node, workflow, benchmark, or release
- broad AI tool lists, unrelated Stable Diffusion chatter, questions, memes, unsafe content

Return strict JSON with keys:
- decision: keep, downgrade, or reject
- content_type: one of official_release, model_release, node_update, workflow_update,
  performance_optimization, security_breaking, benchmark, tutorial, course_marketing,
  lead_generation, showcase, question, duplicate_noise, unrelated, unsafe, unknown
- importance: integer 0-100
- confidence: integer 0-100
- reason: short Chinese explanation
- zh_title: concise Chinese title, empty if not useful
- zh_summary: 1-2 Chinese sentences focused on what changed, empty if not useful
- cluster_key: stable key like model:flux-2 or node:comfyui-manager, empty if unknown
- signals: array of short concrete evidence strings
Do not invent facts beyond the provided item."""

LOW_VALUE_CONTENT_TYPES = {
    "tutorial",
    "course_marketing",
    "lead_generation",
    "showcase",
    "question",
    "duplicate_noise",
    "unrelated",
    "unsafe",
}

VALID_DECISIONS = {"keep", "downgrade", "reject"}


@dataclass(frozen=True)
class LlmTriageSummary:
    reviewed: int
    kept: int
    downgraded: int
    rejected: int
    skipped: int
    failed: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "reviewed": self.reviewed,
            "kept": self.kept,
            "downgraded": self.downgraded,
            "rejected": self.rejected,
            "skipped": self.skipped,
            "failed": self.failed,
        }


def triage_items(
    storage: Storage,
    *,
    limit: int = 40,
    min_score: int = 45,
    include_reviewed: bool = False,
    dry_run: bool = False,
) -> LlmTriageSummary:
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set; refusing to call LLM triage.")

    rows = storage.list_items(limit=max(limit * 6, 240), featured=None, sort="score", include_raw=True)
    eligible = [
        row
        for row in rows
        if should_triage_row(row, min_score=min_score, include_reviewed=include_reviewed)
    ]
    # Spend the LLM budget where rules are least certain: the gray score band,
    # noisy channels first.
    candidates = sorted(eligible, key=triage_priority)[:limit]

    kept = downgraded = rejected = failed = 0
    fetched: list[dict[str, Any] | None] = []
    if candidates:
        workers = max(1, min(settings.llm_triage_concurrency, len(candidates)))
        with httpx.Client(timeout=45) as client:
            if workers == 1:
                fetched = [fetch_triage_result(client, row) for row in candidates]
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    fetched = list(pool.map(lambda row: fetch_triage_result(client, row), candidates))
    for row, result in zip(candidates, fetched, strict=True):
        if result is None:
            failed += 1
            continue
        decision = result["decision"]
        if not dry_run:
            apply_triage_result(storage, row, result)
        if decision == "keep":
            kept += 1
        elif decision == "downgrade":
            downgraded += 1
        else:
            rejected += 1
    return LlmTriageSummary(
        reviewed=len(candidates),
        kept=kept,
        downgraded=downgraded,
        rejected=rejected,
        skipped=max(0, len(rows) - len(candidates)),
        failed=failed,
    )


def should_triage_row(row: dict[str, Any], *, min_score: int, include_reviewed: bool) -> bool:
    score = int(row.get("score") or 0)
    if score < min_score:
        return False
    raw = row.get("raw") or {}
    if not include_reviewed and isinstance(raw, dict) and raw.get("llm_triage"):
        return False
    source_type = str(row.get("source_type") or "")
    if source_type.startswith("github_search") and score < 70:
        return False
    return True


NOISY_SOURCE_TYPES = {
    "x_search",
    "bilibili_search",
    "youtube_search",
    "youtube_rss",
    "huggingface_models",
    "civitai_models",
    "discord_feed",
    "forum_json",
    "json_feed",
}


def triage_priority(row: dict[str, Any]) -> tuple[int, float]:
    """Sort key: lower sorts first. Gray-band rows from noisy channels lead."""
    score = int(row.get("score") or 0)
    low = settings.llm_triage_band_low
    high = settings.llm_triage_band_high
    in_band = low <= score <= high
    noisy = str(row.get("source_type") or "") in NOISY_SOURCE_TYPES
    candidate = row.get("featured_candidate")
    if candidate is None:
        candidate = row.get("featured")
    if in_band and noisy:
        bucket = 0
    elif in_band:
        bucket = 1
    elif noisy and bool(candidate):
        bucket = 2
    else:
        bucket = 3
    return (bucket, abs(score - (low + high) / 2))


TRIAGE_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


def fetch_triage_result(client: httpx.Client, row: dict[str, Any]) -> dict[str, Any] | None:
    """Call the LLM for one row; returns None on failure so one bad item never aborts the run."""
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": TRIAGE_PROMPT},
            {"role": "user", "content": json.dumps(item_payload(row), ensure_ascii=False)},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
    }
    delay = 2.0
    for attempt in range(3):
        try:
            response = client.post(
                f"{settings.openai_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json=payload,
            )
        except httpx.TransportError:
            if attempt >= 2:
                return None
            time.sleep(delay)
            delay *= 2
            continue
        if response.status_code in TRIAGE_RETRYABLE_STATUS_CODES:
            if attempt >= 2:
                return None
            time.sleep(min(triage_retry_after(response, default=delay), 60.0))
            delay *= 2
            continue
        if response.status_code >= 400:
            return None
        try:
            content = response.json()["choices"][0]["message"]["content"]
            return normalize_triage_result(parse_triage_json(content))
        except (KeyError, IndexError, TypeError, ValueError):
            return None
    return None


def parse_triage_json(content: str) -> dict[str, Any]:
    """Parse model output as JSON, tolerating markdown fences and prose padding."""
    text = (content or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("triage response is not a JSON object")
    return data


def triage_retry_after(response: httpx.Response, *, default: float) -> float:
    value = response.headers.get("retry-after")
    if value:
        try:
            return max(float(value), 0.0)
        except ValueError:
            pass
    return default


def triage_row(row: dict[str, Any]) -> dict[str, Any]:
    with httpx.Client(timeout=45) as client:
        result = fetch_triage_result(client, row)
    if result is None:
        raise RuntimeError("LLM triage request failed after retries")
    return result


def item_payload(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    return {
        "title": row.get("title"),
        "summary": row.get("summary"),
        "url": row.get("url"),
        "source": {
            "id": row.get("source_id"),
            "name": row.get("source_name"),
            "type": row.get("source_type"),
            "tier": row.get("source_tier"),
            "category": row.get("category"),
            "author": row.get("author"),
        },
        "score": row.get("score"),
        "featured": row.get("featured"),
        "tags": row.get("tags", []),
        "raw_excerpt": raw_excerpt(raw),
    }


def raw_excerpt(raw: dict[str, Any]) -> dict[str, Any]:
    excerpt: dict[str, Any] = {}
    for key in (
        "description",
        "body",
        "content",
        "text",
        "html_url",
        "full_name",
        "topics",
        "stargazers_count",
        "engagement",
        "trusted_author",
    ):
        value = raw.get(key)
        if value in (None, "", [], {}):
            continue
        excerpt[key] = value[:1500] if isinstance(value, str) else value
    return excerpt


def normalize_triage_result(data: dict[str, Any]) -> dict[str, Any]:
    content_type = str(data.get("content_type") or "unknown").strip().lower()[:80] or "unknown"
    decision = str(data.get("decision") or "").strip().lower()
    importance = clamp_int(data.get("importance"), default=0)
    confidence = clamp_int(data.get("confidence"), default=0)
    if decision not in VALID_DECISIONS:
        decision = inferred_decision(content_type, importance, confidence)
    if content_type in LOW_VALUE_CONTENT_TYPES and confidence >= 55:
        decision = "reject" if content_type in {"course_marketing", "lead_generation", "unrelated", "unsafe"} else "downgrade"
    return {
        "decision": decision,
        "content_type": content_type,
        "importance": importance,
        "confidence": confidence,
        "reason": str(data.get("reason") or "")[:300],
        "zh_title": str(data.get("zh_title") or "")[:160],
        "zh_summary": str(data.get("zh_summary") or "")[:500],
        "cluster_key": str(data.get("cluster_key") or "")[:120],
        "signals": normalize_signals(data.get("signals")),
        "model": settings.llm_model,
        "reviewed_at": utc_now().isoformat(),
    }


def inferred_decision(content_type: str, importance: int, confidence: int) -> str:
    if content_type in LOW_VALUE_CONTENT_TYPES and confidence >= 55:
        return "reject"
    if importance >= 70:
        return "keep"
    if importance >= 45:
        return "downgrade"
    return "reject"


def clamp_int(value: Any, *, default: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    return max(0, min(number, 100))


def normalize_signals(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item)[:160] for item in value if str(item).strip()][:8]


def apply_triage_result(storage: Storage, row: dict[str, Any], result: dict[str, Any]) -> None:
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    raw = dict(raw)
    raw["llm_triage"] = result

    score, featured, reason, cluster_key, cluster_title = apply_llm_triage(
        score=int(row.get("score") or 0),
        featured=bool(row.get("featured")),
        reason=str(row.get("reason") or ""),
        cluster_key=str(row.get("cluster_key") or ""),
        cluster_title=str(row.get("cluster_title") or ""),
        triage=result,
    )

    with storage.connection() as conn:
        conn.execute(
            """
            UPDATE items
            SET raw = ?, score = ?, featured = ?, featured_candidate = ?, reason = ?, cluster_key = ?, cluster_title = ?
            WHERE guid = ?
            """,
            (
                json.dumps(raw, ensure_ascii=False),
                score,
                1 if featured else 0,
                1 if featured else 0,
                reason[:1000],
                cluster_key,
                cluster_title,
                row["guid"],
            ),
        )
