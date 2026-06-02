from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.collector import collect_sync
from app.digest import render_markdown_digest
from app.sources import load_sources
from app.storage import Storage, utc_now


SLOW_SOURCE_TYPES = {"x_search", "github_search_repos"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh or maintain the local ComfyUI news database.")
    parser.add_argument(
        "--mode",
        choices=("refresh", "rescore", "export", "all"),
        default="refresh",
        help="refresh fetches sources; rescore updates existing rows only; export writes a digest; all does refresh + export.",
    )
    parser.add_argument("--include-type", action="append", default=[], help="Only fetch this source type. Repeatable.")
    parser.add_argument("--exclude-type", action="append", default=[], help="Skip this source type. Repeatable.")
    parser.add_argument("--source-id", action="append", default=[], help="Only fetch this source id. Repeatable.")
    parser.add_argument("--skip-source-id", action="append", default=[], help="Skip this source id. Repeatable.")
    parser.add_argument("--fast", action="store_true", help="Skip slow broad-discovery sources: X browser search and GitHub repo search.")
    parser.add_argument("--skip-x", action="store_true", help="Skip X sources for this run.")
    parser.add_argument("--skip-github-search", action="store_true", help="Skip GitHub repository search sources for this run.")
    parser.add_argument("--no-webhook", action="store_true", help="Do not send COMFYUI_NEWS_WEBHOOK_URL for this run.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    parser.add_argument("--quiet", action="store_true", help="Only print the final summary line unless --json is used.")
    parser.add_argument("--day", help="Digest date in YYYY-MM-DD format. Defaults to latest configured local digest day.")
    parser.add_argument("--out-dir", default=str(ROOT / "data" / "digests"), help="Digest output directory.")
    args = parser.parse_args()

    storage = Storage()
    summary: dict[str, Any] = {"mode": args.mode}

    if args.mode in {"refresh", "all"}:
        include_types = set(args.include_type) or None
        exclude_types = set(args.exclude_type)
        if args.fast:
            exclude_types.update(SLOW_SOURCE_TYPES)
            os.environ.setdefault("X_BROWSER_SEARCH", "off")
        if args.skip_x:
            exclude_types.add("x_search")
            os.environ.setdefault("X_BROWSER_SEARCH", "off")
        if args.skip_github_search:
            exclude_types.add("github_search_repos")
        result = collect_sync(
            storage,
            include_types=include_types,
            exclude_types=exclude_types or None,
            source_ids=set(args.source_id) or None,
            skip_source_ids=set(args.skip_source_id) or None,
            send_webhook=not args.no_webhook,
        )
        summary["refresh"] = collect_result_summary(result.__dict__)
        if not args.quiet and not args.json:
            print_human_result(result.__dict__)

    if args.mode == "rescore":
        sources, keywords = load_sources()
        changed = storage.rescore_items({source.id: source for source in sources}, keywords)
        result = {
            "started_at": utc_now().isoformat(),
            "finished_at": utc_now().isoformat(),
            "fetched": 0,
            "saved": changed,
            "inserted": 0,
            "updated": changed,
            "unchanged": 0,
            "sources": 0,
            "succeeded_sources": 0,
            "failed_sources": 0,
            "skipped_sources": 0,
            "errors": [],
            "source_results": [],
            "operation": "rescore",
        }
        storage.set_metadata("last_collect_result", result)
        storage.record_collect_run(result)
        summary["rescore"] = {"updated": changed}
        if not args.quiet and not args.json:
            print(f"Rescored existing items; updated {changed}.")

    if args.mode in {"export", "all"}:
        output = export_digest(storage, day=args.day, out_dir=Path(args.out_dir))
        summary["export"] = {"path": str(output)}
        if not args.quiet and not args.json:
            print(f"Exported digest: {output}")

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    elif args.quiet:
        print(compact_summary(summary))


def print_human_result(result: dict[str, Any]) -> None:
    print(
        f"Fetched {result['fetched']} items from {result['sources']} sources; "
        f"inserted {result['inserted']}; updated {result['updated']}; "
        f"unchanged {result['unchanged']}; errors {len(result['errors'])}."
    )
    for error in result["errors"]:
        print(f"  - {error}")


def collect_result_summary(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "fetched": result["fetched"],
        "inserted": result["inserted"],
        "updated": result["updated"],
        "unchanged": result["unchanged"],
        "sources": result["sources"],
        "succeeded_sources": result["succeeded_sources"],
        "failed_sources": result["failed_sources"],
        "skipped_sources": result["skipped_sources"],
        "errors": result["errors"],
        "started_at": result["started_at"],
        "finished_at": result["finished_at"],
    }


def export_digest(storage: Storage, *, day: str | None, out_dir: Path) -> Path:
    content = render_markdown_digest(storage, day=day)
    lines = content.splitlines()
    first_line = lines[0] if lines else ""
    digest_day = day or first_line.rsplit(" ", 1)[-1] or utc_now().date().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"{digest_day}.md"
    output.write_text(content + "\n", encoding="utf-8")
    return output


def compact_summary(summary: dict[str, Any]) -> str:
    parts = [f"mode={summary['mode']}"]
    refresh = summary.get("refresh")
    if refresh:
        parts.append(
            "refresh="
            f"fetched:{refresh['fetched']} "
            f"inserted:{refresh['inserted']} "
            f"updated:{refresh['updated']} "
            f"errors:{len(refresh['errors'])}"
        )
    rescore = summary.get("rescore")
    if rescore:
        parts.append(f"rescore=updated:{rescore['updated']}")
    export = summary.get("export")
    if export:
        parts.append(f"export={export['path']}")
    return " | ".join(parts)


if __name__ == "__main__":
    main()
