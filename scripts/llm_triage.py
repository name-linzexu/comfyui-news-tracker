from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.llm_triage import triage_items
from app.storage import Storage


def main() -> None:
    parser = argparse.ArgumentParser(description="Use an LLM to review and rerank ComfyUI news candidates.")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--min-score", type=int, default=45)
    parser.add_argument("--include-reviewed", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = triage_items(
        Storage(),
        limit=args.limit,
        min_score=args.min_score,
        include_reviewed=args.include_reviewed,
        dry_run=args.dry_run,
    )
    data = summary.as_dict()
    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        print(
            "LLM triage reviewed {reviewed}; kept {kept}; downgraded {downgraded}; "
            "rejected {rejected}; failed {failed}; skipped {skipped}.".format(**data)
        )


if __name__ == "__main__":
    main()
