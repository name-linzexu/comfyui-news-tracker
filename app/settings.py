from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    app_name: str = "ComfyUI News Tracker"
    database_path: Path = BASE_DIR / "data" / "news.sqlite3"
    sources_path: Path = BASE_DIR / "config" / "sources.yml"
    static_dir: Path = BASE_DIR / "static"
    request_timeout: float = 20.0
    user_agent: str = (
        "ComfyUI-News-Tracker/0.1 "
        "(https://github.com/local/comfyui-news-tracker; contact: local)"
    )
    github_token: str | None = os.getenv("GITHUB_TOKEN")
    x_bearer_token: str | None = os.getenv("X_BEARER_TOKEN")
    x_browser_search: str = os.getenv("X_BROWSER_SEARCH", "auto").lower()
    x_browser_debug_url: str = os.getenv("X_BROWSER_DEBUG_URL", "http://127.0.0.1:9222/json/version")
    x_browser_scrolls: int = env_int("X_BROWSER_SCROLLS", 12)
    x_browser_wait_ms: int = env_int("X_BROWSER_WAIT_MS", 1800)
    bilibili_cookie: str | None = os.getenv("BILIBILI_COOKIE")
    webhook_url: str | None = os.getenv("COMFYUI_NEWS_WEBHOOK_URL")
    webhook_timeout: float = float(os.getenv("COMFYUI_NEWS_WEBHOOK_TIMEOUT", "15"))


settings = Settings()
