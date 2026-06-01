# ComfyUI News Tracker

一个 ComfyUI 专用动态获取器。它参考 AIHOT 类项目的形态，把信息源抓取、去重评分、分类、日报、RSS、API 和 Agent Skill 接入放在一个本地可运行服务里。当前重点是开源图片/视频模型发布、ComfyUI 节点适配、重大性能/显存优化，以及由此衍生的 LoRA/微调/量化/工作流更新。

## 功能

- 抓取 ComfyUI 官方仓库、前端、Manager、CLI、常用模型节点仓库、GitHub 模型关键词搜索、Bilibili 公开搜索、Reddit RSS 等信源。
- 默认精选会压低普通 GitHub 代码维护噪声，优先展示模型发布、视频/图片节点适配、LoRA/微调/量化、GGUF/FP8、性能/显存优化和高价值社区发布。
- SQLite 本地存储，按 URL 去重，支持全文搜索。
- 自动标签、分类和 0-100 信息价值评分。
- T1 / T1.5 / T2 信源分层，便于区分官方源、高信号生态源和广泛扫描源。
- 每条动态生成 `reason`，说明为什么被推荐或标记为精选。
- 每条动态生成 `score_breakdown`，按 source、relevance、authority、impact、freshness、popularity、penalty 拆分评分。
- 自动生成 `cluster_key`，把同仓库或同事件聚合为事件簇。
- Web UI：默认展示近 7 天精选重点，支持搜索、分类筛选、channel 筛选、精选筛选、分页、24h/7d/All 时间范围、按日分组、日期吸顶、按天收起/展开、日报视图、往期日报日期切换、最近日报归档、已读置灰、浅色/深色/跟随系统主题、信源墙、信源提报和反馈入口。
- API：内部接口 `/api/feed`、`/api/items`、`/api/clusters`、`/api/digest`、`/api/daily/latest`、`/api/daily/dates`、`/api/daily/archive`、`/api/stats`、`/api/sources`、`/api/source-wall`、`/api/source-submissions`、`/api/source-health`、`/api/feedback`、`/api/export/markdown`；Agent 友好的公开接口 `/api/public`、`/api/public/items`、`/api/public/daily`、`/api/public/daily/{date}`、`/api/public/dailies`、`/api/public/daily/archive`、`/api/public/briefing`、`/api/public/sources`、`/api/public/health`。
- RSS：`/feed`、`/rss`、`/feed.xml`、`/selected.xml`、`/all.xml`、`/daily.xml`、`/digests.xml`，也保留 `/rss/selected.xml`、`/rss/all.xml`、`/rss/daily.xml`、`/rss/digests.xml` 和兼容入口 `/rss.xml`。其中 `/daily.xml` 是当天日报内的条目流，`/digests.xml` 是按天一期的日报归档订阅，`/feeds.opml` 是可导入 RSS 阅读器的订阅清单。
- Webhook：可选设置 `COMFYUI_NEWS_WEBHOOK_URL`，刷新后自动 POST 当天日报 Markdown、结构化 digest 和刷新统计，便于接入飞书、企业微信、Slack、n8n 或自建 Agent。
- Skill：`skills/comfyui-news/SKILL.md`，同时通过 `/comfyui-skill/`、`/comfyui-skill/SKILL.md`、`/skill` 和 `/skill/comfyui-news/SKILL.md` 暴露，便于 Codex/Agent 读取本地接口。

## 快速启动

```powershell
git clone https://github.com/name-linzexu/comfyui-news-tracker.git
cd comfyui-news-tracker
.\run.ps1
```

启动后打开：

```text
http://127.0.0.1:8787
```

日报入口：

```text
http://127.0.0.1:8787/daily
http://127.0.0.1:8787/daily/2026-06-01
```

如果 GitHub API 触发限流，可以配置 token：

```powershell
$env:GITHUB_TOKEN = "<your-github-token>"
.\run.ps1
```

未配置 `GITHUB_TOKEN` 时，标记了 `requires_token: true` 的 GitHub REST 源会被跳过，并在 Overview 中显示为 skipped，不计入失败。项目已经配置了 GitHub Atom/RSS 备用源，官方 commit/release、前端 release、Manager release 仍会继续抓取；配置 token 后可以恢复更完整的 REST API 数据。

X 搜索需要 API token：

```powershell
$env:X_BEARER_TOKEN = "<your-x-bearer-token>"
.\run.ps1
```

如果没有 X API Bearer，也可以走登录态浏览器抓取。先启动独立 Chrome profile，登录一次 X：

```powershell
.\scripts\start-x-debug.ps1
```

之后保持这个 Chrome 开着，刷新器会连接 `http://127.0.0.1:9222/json/version`，打开 X 搜索 live 页并抽取帖子。可用 `X_BROWSER_SEARCH=off` 关闭这个兜底方式。

Bilibili 公开搜索不强制登录。若遇到风控或返回不稳定，可以提供登录 cookie：

```powershell
$env:BILIBILI_COOKIE = "<your-bilibili-cookie>"
.\run.ps1
```

## 低 token 固定流程

本地脚本抓取、入库、重打分、导出日报本身不消耗 Codex/OpenAI 对话 token。消耗 token 的是让 Codex 在聊天里替你阅读网页、分析结果、总结日志或继续改代码。日常使用建议直接跑下面这些固定脚本，把重复动作留在本机完成。

完整刷新，保持当前功能不变：

```powershell
.\scripts\collect.ps1
```

刷新并导出日报 Markdown：

```powershell
.\scripts\collect.ps1 -Mode all
```

快速刷新，跳过最慢的 X 浏览器抓取和 GitHub 仓库广泛搜索：

```powershell
.\scripts\collect.ps1 -Fast
```

只重打分，不联网抓新数据，适合修改评分规则后立即看效果：

```powershell
.\scripts\collect.ps1 -Mode rescore
```

只抓某类信源，例如只抓 Bilibili 或只抓 X：

```powershell
.\scripts\collect.ps1 -IncludeType bilibili_search
.\scripts\collect.ps1 -IncludeType x_search
```

只启动 Web 服务：

```powershell
.\scripts\serve.ps1
```

脚本会把每次刷新日志写入 `data/logs/refresh-YYYYMMDD-HHMMSS.log`，依赖安装也会按 `requirements.txt` 的哈希缓存；依赖没变时不会反复 `pip install`。

## 常用命令

只刷新数据：

```powershell
.\scripts\collect.ps1
```

导出当天日报归档：

```powershell
.\scripts\collect.ps1 -Mode export
```

导出指定日期：

```powershell
.\scripts\collect.ps1 -Mode export -Day 2026-06-01
```

启动服务：

```powershell
.\scripts\serve.ps1 -Reload
```

运行回归测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

## Webhook 推送

默认不推送。需要自动分发日报时，先设置 webhook 地址：

```powershell
$env:COMFYUI_NEWS_WEBHOOK_URL = "https://example.com/webhook"
$env:COMFYUI_NEWS_WEBHOOK_TIMEOUT = "15"
.\.venv\Scripts\python.exe scripts\refresh.py
```

每次刷新完成后会发送 JSON：

```json
{
  "type": "comfyui_daily_digest",
  "date": "2026-06-01",
  "title": "ComfyUI Daily Digest - 2026-06-01",
  "total": 60,
  "categories": {"official": 6},
  "markdown": "# ComfyUI Daily Digest - 2026-06-01...",
  "digest": {},
  "refresh": {"inserted": 1, "updated": 0}
}
```

推送结果会写入 `last_collect_result.webhook`，可在 `/api/stats` 的 `last_collect_result` 中查看。

## API 示例

```text
GET /api/public
GET /api/public/items?mode=selected&take=30&hours=168
GET /api/public/items?mode=all&take=30&since=2026-06-01T00:00:00Z
GET /api/public/items?mode=selected&channel=github&tier=T1.5
GET /api/public/items?mode=selected&category=model_nodes
GET /api/public/items?mode=all&channel=x&q=Wan
GET /api/public/items?mode=all&channel=bilibili&q=Wan
GET /api/public/items?q=ComfyUI-Manager:%20Flux%202&mode=all
GET /api/public/daily
GET /api/public/daily/2026-06-01
GET /api/public/dailies?take=30
GET /api/public/daily/archive?take=30
GET /api/public/briefing?hours=24&take=12
GET /api/public/briefing?hours=168&take=20&channel=github&q=ComfyUI-Manager
GET /api/public/sources
GET /api/public/health
GET /comfyui-skill/
GET /api/items?limit=20
GET /api/items?limit=30&offset=30
GET /api/items?limit=30&page=2
GET /api/items?channel=github
GET /api/items?q=workflow&featured=true
GET /api/items?q=ComfyUI-Manager:%20Flux%202
GET /api/items?tier=T1
GET /api/items?category=official&hours=168
GET /api/feed?mode=selected
GET /api/feed?mode=all
GET /api/feed?mode=daily
GET /api/clusters?featured=true
GET /api/digest
GET /api/daily/latest
GET /api/daily/dates
GET /api/daily/archive
GET /api/sources
GET /api/source-wall
GET /api/source-health
GET /api/export/markdown
GET /feed
GET /rss
GET /feed.xml
GET /selected.xml
GET /all.xml
GET /daily.xml
GET /digests.xml
GET /feeds.opml
GET /rss/selected.xml
GET /rss/all.xml
GET /rss/daily.xml
GET /rss/digests.xml
GET /rss/feeds.opml
POST /api/refresh?wait=true
POST /api/source-submissions
POST /api/feedback
```

## 增加信源

编辑 `config/sources.yml`。当前支持：

- `github_releases`
- `github_commits`
- `github_search_repos`
- `github_issues`
- `rss`
- `bilibili_search`
- `x_search`

默认精选不会把 `github_search_repos` 这类代码仓库发现结果当成新闻主线；它们主要留在全量搜索里供溯源。默认动态更偏向 X、Bilibili、官方发布、模型节点支持、工作流教程、LoRA/量化/性能变化。

每个信源可以设置 `category` 和 `weight`。`weight` 会参与精选评分，官方源和 release 源权重应更高。

每个信源也可以设置 `tier`：

- `T1`：官方或一手源，例如 ComfyUI commit、release、官方 blog。
- `T1.5`：高信号生态源，例如核心工具、常用扩展、issue。
- `T2`：广泛扫描源，例如 GitHub search、Reddit、社区讨论。

GitHub REST / X 源还可以设置：

- `requires_token: true`：未设置 `GITHUB_TOKEN` 时自动跳过，避免可预期的 rate limit 让刷新状态长期显示失败。
- `requires_x_token: true`：默认优先使用 `X_BEARER_TOKEN`；没有 Bearer 时会尝试登录态 Chrome 抓取。若设置 `X_BROWSER_SEARCH=off`，没有 Bearer 就跳过 X 源。

也可以先通过 Web UI 的 `Suggest source` 或 API 提交候选源：

```http
POST /api/source-submissions
Content-Type: application/json

{
  "url": "https://example.com/feed.xml",
  "name": "Example ComfyUI Feed",
  "reason": "持续发布 ComfyUI 节点、工作流或工具更新",
  "contact": "optional"
}
```

候选源会先进入本地 `source_submissions` 表，并出现在 `/api/source-wall` 的 pending 区域。确认质量后，再把它整理进 `config/sources.yml`。

## 定时刷新

安装 Windows 计划任务，每 2 小时刷新一次：

```powershell
cd comfyui-news-tracker
.\scripts\install_scheduled_task.ps1
```

Web UI 的 Overview 会显示最近一次刷新是否完整成功，以及成功/失败信源数量。
刷新状态会区分 `inserted`、`updated`、`unchanged`，便于判断这次刷新是真的新增内容，还是只是重复拉取。

## 后续可以增强

- 增强 Bilibili/X 的登录态抓取、作者白名单和互动指标。
- 接入 YouTube、Discord/论坛、Hugging Face 模型卡和 Civitai 模型更新。
- 用 LLM 做摘要、中文翻译和更精确的去重聚类。
- 增加定时任务、邮件/飞书/企业微信推送。
- 增加多项目订阅，把 ComfyUI、Stable Diffusion、模型发布分成不同频道。
