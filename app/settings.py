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


def env_secret(name: str, default_file: Path | None = None) -> str | None:
    value = os.getenv(name)
    if value:
        return value
    file_value = os.getenv(f"{name}_FILE")
    path = Path(file_value) if file_value else default_file
    if not path:
        return None
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


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
    github_token: str | None = env_secret("GITHUB_TOKEN", BASE_DIR / ".secrets" / "github_token.txt")
    x_bearer_token: str | None = env_secret("X_BEARER_TOKEN", BASE_DIR / ".secrets" / "x_bearer_token.txt")
    x_browser_search: str = os.getenv("X_BROWSER_SEARCH", "auto").lower()
    x_browser_debug_url: str = os.getenv("X_BROWSER_DEBUG_URL", "http://127.0.0.1:9222/json/version")
    x_browser_scrolls: int = env_int("X_BROWSER_SCROLLS", 12)
    x_browser_wait_ms: int = env_int("X_BROWSER_WAIT_MS", 1800)
    x_author_allowlist: str = os.getenv("X_AUTHOR_ALLOWLIST", "")
    bilibili_author_allowlist: str = os.getenv("BILIBILI_AUTHOR_ALLOWLIST", "")
    bilibili_cookie: str | None = env_secret("BILIBILI_COOKIE", BASE_DIR / ".secrets" / "bilibili_cookie.txt")
    youtube_api_key: str | None = env_secret("YOUTUBE_API_KEY", BASE_DIR / ".secrets" / "youtube_api_key.txt")
    civitai_token: str | None = env_secret("CIVITAI_TOKEN", BASE_DIR / ".secrets" / "civitai_token.txt")
    openai_api_key: str | None = env_secret("OPENAI_API_KEY", BASE_DIR / ".secrets" / "openai_api_key.txt")
    openai_base_url: str = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
    llm_model: str = os.getenv("COMFYUI_NEWS_LLM_MODEL", "gpt-4.1-mini")
    webhook_url: str | None = os.getenv("COMFYUI_NEWS_WEBHOOK_URL")
    webhook_timeout: float = float(os.getenv("COMFYUI_NEWS_WEBHOOK_TIMEOUT", "15"))


settings = Settings()
