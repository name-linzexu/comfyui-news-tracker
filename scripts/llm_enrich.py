from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.settings import settings
from app.storage import Storage, utc_now


PROMPT = """You enrich ComfyUI news items for a Chinese reader.
Return strict JSON with keys:
- zh_title: concise Chinese title
- zh_summary: 1-2 sentence Chinese summary focused on what changed
- cluster_key: stable event key such as model:flux-2, node:wanvideo-wrapper, tool:comfyui-manager
- importance: integer 0-100
Do not include secrets, usernames, or unrelated speculation."""


def main() -> None:
    parser = argparse.ArgumentParser(description="Optionally enrich local news rows with LLM summaries.")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--min-score", type=int, default=58)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not settings.openai_api_key:
        raise SystemExit("OPENAI_API_KEY is not set; refusing to call an LLM.")

    storage = Storage()
    rows = storage.list_items(limit=args.limit, featured=None, sort="score", include_raw=True)
    candidates = [
        row for row in rows
        if int(row.get("score") or 0) >= args.min_score and not (row.get("raw") or {}).get("llm")
    ][: args.limit]

    enriched = 0
    for row in candidates:
        result = enrich_row(row)
        if args.dry_run:
            print(json.dumps({"guid": row["guid"], "llm": result}, ensure_ascii=False))
            continue
        write_llm_result(storage, row, result)
        enriched += 1
    print(f"LLM enriched {enriched} items.")


def enrich_row(row: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "model": settings.llm_model,
        "messages": [
            {"role": "system", "content": PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "title": row["title"],
                        "summary": row["summary"],
                        "url": row["url"],
                        "source": row["source_name"],
                        "tags": row.get("tags", []),
                        "score": row.get("score"),
                    },
                    ensure_ascii=False,
                ),
            },
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
    with httpx.Client(timeout=45) as client:
        response = client.post(
            f"{settings.openai_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.openai_api_key}"},
            json=payload,
        )
        response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    data = json.loads(content)
    return {
        "zh_title": str(data.get("zh_title") or "")[:160],
        "zh_summary": str(data.get("zh_summary") or "")[:500],
        "cluster_key": str(data.get("cluster_key") or "")[:120],
        "importance": int(data.get("importance") or 0),
        "model": settings.llm_model,
        "updated_at": utc_now().isoformat(),
    }


def write_llm_result(storage: Storage, row: dict[str, Any], result: dict[str, Any]) -> None:
    raw = row.get("raw") or {}
    raw["llm"] = result
    cluster_key = result.get("cluster_key") or row.get("cluster_key") or ""
    summary = result.get("zh_summary") or row["summary"]
    with storage.connection() as conn:
        conn.execute(
            """
            UPDATE items
            SET raw = ?, summary = ?, cluster_key = ?, cluster_title = ?
            WHERE guid = ?
            """,
            (
                json.dumps(raw, ensure_ascii=False),
                summary,
                cluster_key,
                result.get("zh_title") or row["cluster_title"],
                row["guid"],
            ),
        )


if __name__ == "__main__":
    main()
