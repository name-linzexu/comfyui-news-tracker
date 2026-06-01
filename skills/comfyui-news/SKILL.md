# ComfyUI News

Use this skill when the user asks for the latest ComfyUI ecosystem updates, image/video model releases, model-node support, official ComfyUI changes, ComfyUI Manager releases, custom node news, workflow discoveries, LoRA/finetune/quantization updates, or a daily/weekly ComfyUI digest.

## Local Service

The local ComfyUI News Tracker service should be running at:

```text
http://127.0.0.1:8787
```

If it is not running, start it from the project directory:

```powershell
cd "D:\comfyui 开源社区动态获取器"
.\run.ps1
```

Optional webhook delivery can be enabled before refresh:

```powershell
$env:COMFYUI_NEWS_WEBHOOK_URL = "https://example.com/webhook"
$env:COMFYUI_NEWS_WEBHOOK_TIMEOUT = "15"
```

Optional authenticated sources:

```powershell
$env:GITHUB_TOKEN = "<your-github-token>"
$env:X_BEARER_TOKEN = "<your-x-bearer-token>"
$env:BILIBILI_COOKIE = "<your-bilibili-cookie>"
```

Without `X_BEARER_TOKEN`, the tracker can use the logged-in browser fallback. Start it once with `.\scripts\start-x-debug.ps1`, log in to X in that Chrome profile, and keep the window running while refreshing. Set `X_BROWSER_SEARCH=off` to disable this fallback.

## Workflow

Refresh data when the user asks for current/latest updates:

```http
POST http://127.0.0.1:8787/api/refresh?wait=true
```

Use the mode API for intent routing:

```http
GET http://127.0.0.1:8787/api/feed?mode=selected
GET http://127.0.0.1:8787/api/feed?mode=all
GET http://127.0.0.1:8787/api/feed?mode=daily
```

Prefer the public REST endpoints when another Agent or external client needs a stable, anonymous interface:

```http
GET http://127.0.0.1:8787/api/public
GET http://127.0.0.1:8787/api/public/items?mode=selected&take=30&hours=168
GET http://127.0.0.1:8787/api/public/items?mode=all&take=30
GET http://127.0.0.1:8787/api/public/items?mode=selected&channel=github&tier=T1.5
GET http://127.0.0.1:8787/api/public/items?mode=selected&category=model_nodes
GET http://127.0.0.1:8787/api/public/items?mode=all&channel=x&q=Wan
GET http://127.0.0.1:8787/api/public/items?mode=all&channel=bilibili&q=Wan
GET http://127.0.0.1:8787/api/public/items?mode=all&q=ComfyUI-Manager:%20Flux%202
GET http://127.0.0.1:8787/api/public/daily
GET http://127.0.0.1:8787/api/public/daily/YYYY-MM-DD
GET http://127.0.0.1:8787/api/public/dailies?take=30
GET http://127.0.0.1:8787/api/public/daily/archive?take=30
GET http://127.0.0.1:8787/api/public/briefing?hours=24&take=12
GET http://127.0.0.1:8787/api/public/briefing?hours=168&take=20&q=ComfyUI-Manager
GET http://127.0.0.1:8787/api/public/sources
GET http://127.0.0.1:8787/api/public/health
```

Use focused endpoints when needed:

```http
GET http://127.0.0.1:8787/api/items?q=workflow&featured=true
GET http://127.0.0.1:8787/api/items?q=ComfyUI-Manager:%20Flux%202
GET http://127.0.0.1:8787/api/items?category=official&hours=168
GET http://127.0.0.1:8787/api/items?channel=github
GET http://127.0.0.1:8787/api/items?tier=T1
GET http://127.0.0.1:8787/api/items?page=2&limit=30
GET http://127.0.0.1:8787/api/clusters?featured=true
GET http://127.0.0.1:8787/api/daily/latest
GET http://127.0.0.1:8787/api/daily/dates
GET http://127.0.0.1:8787/api/daily/archive
GET http://127.0.0.1:8787/api/source-wall
GET http://127.0.0.1:8787/api/source-health
GET http://127.0.0.1:8787/api/export/markdown
```

Daily web views:

```text
http://127.0.0.1:8787/daily
http://127.0.0.1:8787/daily/YYYY-MM-DD
```

RSS feeds:

```text
http://127.0.0.1:8787/feed
http://127.0.0.1:8787/rss
http://127.0.0.1:8787/feed.xml
http://127.0.0.1:8787/selected.xml
http://127.0.0.1:8787/all.xml
http://127.0.0.1:8787/daily.xml
http://127.0.0.1:8787/digests.xml
http://127.0.0.1:8787/feeds.opml
http://127.0.0.1:8787/rss/selected.xml
http://127.0.0.1:8787/rss/all.xml
http://127.0.0.1:8787/rss/daily.xml
http://127.0.0.1:8787/rss/digests.xml
http://127.0.0.1:8787/rss/feeds.opml
```

Install/read this skill from:

```text
http://127.0.0.1:8787/comfyui-skill/
http://127.0.0.1:8787/comfyui-skill/SKILL.md
http://127.0.0.1:8787/skill
http://127.0.0.1:8787/skill/comfyui-news/SKILL.md
```

## Response Guidance

- Prioritize readable source channels (`channel=x`, `channel=bilibili`, official/blog/release sources) before raw GitHub repository search results. Use raw GitHub repos only as supporting context or when the user asks for code/projects.
- Treat model releases, video/image generation nodes, LoRA/finetune updates, quantization/runtime support, and major performance/VRAM changes as higher value than routine GitHub commits.
- Use `mode=all` or `sort=latest` only when the user asks for the raw timeline.
- Prefer `source_tier=T1` for official facts, then `T1.5` for high-signal ecosystem updates, then `T2` for broad community discovery.
- Use `reason` to explain why an item matters.
- Use `score_breakdown` when the user asks why something was ranked highly.
- Use `/api/clusters` when the user asks for grouped events rather than raw feed items.
- Use `/api/public/briefing` when another Agent needs one compact response with summary counts, top items, event clusters, refresh health, links, and Markdown.
- Use `/api/public/daily/archive` when the user asks for historical daily coverage, a date index, or changes over multiple days.
- Use `/digests.xml` when the user wants an RSS reader subscription for one item per daily digest issue.
- Use `/feeds.opml` when the user wants to import all ComfyUI News Tracker feeds into an RSS reader.
- Use `/api/source-wall` when the user asks which sources are covered or wants to audit coverage.
- Use `/api/source-health` when the user asks whether the tracker is healthy or why coverage changed.
- Check `/api/stats` `last_collect_result.webhook` when the user asks whether webhook delivery succeeded.
- Treat `source_results[].status == "skipped"` with `reason == "requires GITHUB_TOKEN"` or `reason == "requires X_BEARER_TOKEN or running X browser debug endpoint"` as expected when credentials are not configured, not as failed sources.
- Separate official updates, model nodes, tooling updates, custom nodes/workflows, models, X, Bilibili, and broader community discussions.
- Include dates, source names, and links.
- If data is stale or empty, refresh the service first and say when no matching updates were found.
