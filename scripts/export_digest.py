from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.digest import render_markdown_digest
from app.storage import Storage


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a ComfyUI daily digest markdown archive.")
    parser.add_argument("--day", help="UTC date in YYYY-MM-DD format. Defaults to today.")
    parser.add_argument("--out-dir", default=str(ROOT / "data" / "digests"), help="Output directory.")
    args = parser.parse_args()

    content = render_markdown_digest(Storage(), day=args.day)
    first_line = content.splitlines()[0]
    day = args.day or first_line.rsplit(" ", 1)[-1]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"{day}.md"
    output.write_text(content + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
