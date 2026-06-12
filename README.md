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

可以给 X / Bilibili 设作者白名单，白名单作者会得到 authority 加权；互动指标会从 X API、Bilibili 搜索结果、YouTube API、Hugging Face / Civitai 统计中自动进入 popularity 加权：

```powershell
$env:X_AUTHOR_ALLOWLIST = "ComfyUI,comfyanonymous,kijai"
$env:BILIBILI_AUTHOR_ALLOWLIST = "作者A,作者B"
```

Bilibili 条目会默认补拉视频详情、完整简介、分 P/章节、可下载字幕文本，以及播放、点赞、投币、收藏、分享、评论和弹幕等互动指标。这些字段会写入 `raw.content_understanding` / `raw.engagement`，并在接口顶层返回 `engagement` 方便前端和 Agent 使用。若字幕拉取太慢，可以关闭：

```powershell
$env:BILIBILI_DETAIL_ENABLED = "1"
$env:BILIBILI_SUBTITLE_TEXT_ENABLED = "0"
```

ASR 默认关闭，建议只给预筛后的高潜力 Bilibili 候选使用。配置本地 ASR/术语校正脚本后，采集器会把视频 URL、BV 号、标题和从简介/章节/字幕提取的术语通过环境变量传给脚本，并把脚本标准输出纳入 `raw.content_understanding.asr`：

```powershell
$env:BILIBILI_ASR_ENABLED = "1"
$env:BILIBILI_ASR_COMMAND = ".\\scripts\\your-bilibili-asr.ps1"
$env:BILIBILI_ASR_MAX_ITEMS = "3"
$env:BILIBILI_ASR_MIN_WEIGHTED = "250"
```

YouTube 搜索需要 API key；未配置时会自动跳过，不算失败：

```powershell
$env:YOUTUBE_API_KEY = "<your-youtube-api-key>"
```

Civitai 公共接口默认可用；如果遇到限流，可以配置 token：

```powershell
$env:CIVITAI_TOKEN = "<your-civitai-token>"
```

Discord / forum sources use local JSON bridges. Keep bridge URLs and tokens in environment variables or ignored local files, not in Git:

```powershell
$env:DISCORD_COMFYUI_FEED_URL = "https://your-bridge.example/discord-comfyui.json"
$env:COMFYUI_FORUM_JSON_URL = "https://forum.example/latest.json"
$env:DISCORD_COMFYUI_FEED_TOKEN = "<your-bridge-token>"
$env:FORUM_COMFYUI_JSON_AUTHORIZATION = "Bearer <your-bridge-token>"
```

Secrets can also be stored in ignored local files such as `.secrets/bilibili_cookie.txt`, `.secrets/x_bearer_token.txt`, `.secrets/youtube_api_key.txt`, `.secrets/civitai_token.txt`, and `.secrets/openai_api_key.txt`. Environment variables still take priority.

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

注意：普通刷新结束时的隐式重打分只覆盖最近 `COMFYUI_NEWS_RESCORE_DAYS` 天（默认 14，设为 0 表示全量），避免数据库变大后每次刷新都全表重算。显式运行 `-Mode rescore` 仍然是全量重算，修改评分规则后想让历史条目也生效就用它。

只抓某类信源，例如只抓 Bilibili 或只抓 X：

```powershell
.\scripts\collect.ps1 -IncludeType bilibili_search
.\scripts\collect.ps1 -IncludeType x_search
```

只抓模型平台或 YouTube：

```powershell
.\scripts\collect.ps1 -IncludeType huggingface_models
.\scripts\collect.ps1 -IncludeType civitai_models
.\scripts\collect.ps1 -IncludeType youtube_search
.\scripts\collect.ps1 -IncludeType discord_feed
.\scripts\collect.ps1 -IncludeType forum_json
```

模型平台抓取包含两类入口：一类是 ComfyUI/Flux/Wan/Qwen/LTX 等已知生态关键词，另一类是 Hugging Face/Civitai 的广谱新模型发现，例如 `text-to-image`、`image-to-video`、`text-to-video`、`video generation`、`diffusers`、`checkpoint`、`lora` 等。后者用于发现尚未写进关键词表的新开源图像/视频模型；建议配合 `-LlmTriage` 使用，降低纯展示、教程和营销内容进入精选的概率。

可选 LLM 后处理默认不运行，只有配置了 API key 并显式运行脚本才会消耗 API。任何 OpenAI 兼容端点都可以用——除了环境变量，也支持放在忽略的本地文件里（环境变量优先）：

```text
.secrets/openai_api_key.txt    # API key（OPENAI_API_KEY）
.secrets/openai_base_url.txt   # 端点，如 https://api.xiaomimimo.com/v1（OPENAI_BASE_URL）
.secrets/llm_model.txt         # 模型名，如 mimo-v2.5（COMFYUI_NEWS_LLM_MODEL）
```

审稿对模型输出做了容错解析（markdown 代码栏、前后缀文本都能解），单条约 600 token，40 条一轮在 MiMo v2.5 上约几分钱。它会给高分条目生成中文摘要、中文标题和更稳定的聚类键：

```powershell
$env:OPENAI_API_KEY = "<your-openai-api-key>"
$env:COMFYUI_NEWS_LLM_MODEL = "gpt-4.1-mini"
.\.venv\Scripts\python.exe scripts\llm_enrich.py --limit 20
```

如果需要先理解内容再筛选，可以开启 LLM 审稿。它会读取候选条目的标题、摘要、来源、标签和原始字段，判断是发布/模型/节点/性能更新，还是教程、卖课、评论区领取、纯展示、闲聊等低价值内容，然后写回 `score`、`featured`、`reason` 和 `raw.llm_triage`：

```powershell
$env:OPENAI_API_KEY = "<your-openai-api-key>"
$env:COMFYUI_NEWS_LLM_MODEL = "gpt-4.1-mini"
.\scripts\collect.ps1 -Mode all -LlmTriage -LlmTriageLimit 40 -LlmTriageMinScore 45
```

也可以只审已有数据库，不重新抓取：

```powershell
.\.venv\Scripts\python.exe scripts\llm_triage.py --limit 60 --min-score 45
```

只启动 Web 服务：

```powershell
.\scripts\serve.ps1
```

脚本会把每次刷新日志写入 `data/logs/refresh-YYYYMMDD-HHMMSS.log`，依赖安装也会按 `requirements.txt` 的哈希缓存；依赖没变时不会反复 `pip install`。

## 推荐架构

精选（featured）分两段决定：

1. **逐条候选**：抓取/重打分时按规则评出 `featured_candidate`（资格位），评分包含若干针对性封顶——commit 按前缀分类（`feat:`/`perf:` 不封顶，`fix:` 沿用 bugfix 上限，`chore:`/`docs:` 等封 48，`mm:`/`main:` 之类内部子系统前缀在 commit 源里封 62）；Hugging Face 按作者分级（官方组织加权 +14、可信转档作者 +8、未知作者的已知模型族转档按采用度封 58/72、未知作者且无下载量封 60）；Civitai 角色/人脸/动漫 LoRA 封 56；社区 RSS 提问帖封 56、无新闻信号封 76；关键词堆叠的 relevance 封 32。
2. **选片器**：每次采集/重打分后运行，在每个日报日内**按事件簇去重**（同一事件只保留分数/层级最高的一条），再按渠道配额裁剪（默认每天 x:6、bilibili:6、models:8、youtube:4、community:8、forum:6、discord:6；官方/T1 不限量），写回最终 `featured`。配额可用 `COMFYUI_NEWS_FEATURED_QUOTAS="x:8,models:10"` 覆盖。

模型族、官方组织、可信转档作者集中在 `app/vocab.py`，也可在 `config/sources.yml` 顶部加 `vocab:` 段扩充——新模型族只需改一处，打标、精选规则、聚类键、B 站术语全部自动生效。

互动与作者影响力信号：

- **互动速度**：popularity 分量按「加权互动 ÷ 发布天数」取对数计分（约 10/天 +7、100/天 +14、1000/天 +21，封顶 28），爆款有区分度，老内容不能靠存量互动刷分；条目在 rescore 窗口内会随时间自然衰减。
- **作者粉丝量**：X 通过 `user.fields=public_metrics` 拿粉丝数（同一请求免费字段）、B 站对新视频补全时拉一次 UP 主卡片（每作者每轮只查一次）、YouTube 批量查频道订阅数。写入 `raw.engagement.author_followers`，按对数进 authority 分量（1k +4、10k +8、100k +12，封顶 16），与白名单 `*_AUTHOR_ALLOWLIST` 叠加，白名单仍是更强信号。

开启 `-LlmTriage` 时，审稿候选优先取 50–78 分灰区（`COMFYUI_NEWS_LLM_BAND_LOW/HIGH` 可调）和嘈杂渠道里的候选条目，把 LLM 预算花在规则最拿不准的地方。

**创作者解读（deep-dive）**：命中当前模型族 + 解读信号（实测/详解/对比/参数/显存优化/benchmark 等）且不含新手营销/引流的内容会打上 `deep-dive` 标签，日报中有独立的「Creator Deep-dives」板块。有互动量（≥150）或作者粉丝量（≥5000）背书的解读会豁免 B 站教程类措辞的降分封顶（最高 86，仍低于一手官方新闻）；卖课、引流、网盘类硬噪声不受豁免。LLM 审稿对应的内容类型为 `model_deep_dive`（保留）与 `tutorial`（新手教学，降级）。把你信任的博主加进 `X_AUTHOR_ALLOWLIST` / `BILIBILI_AUTHOR_ALLOWLIST` 可以再叠加白名单权威加成。

## 抓取可靠性与增量抓取

- 所有信源请求自带重试：瞬时网络错误和 408/429/5xx 会按指数退避重试，次数由 `COMFYUI_NEWS_HTTP_RETRIES` 控制（默认 3）。
- GitHub 搜索请求会在单次刷新内串行执行并加间隔，命中 rate limit 时按 `Retry-After` / `x-ratelimit-reset` 等待后重试（封顶 90 秒），避免之前并发突发导致的 403。
- B 站增量抓取：已入库且补全过详情/字幕的视频默认不再重复拉取（发布不足 `BILIBILI_REENRICH_HOURS` 小时的视频仍会刷新互动数据，默认 48）。因此 B 站源的 `fetched` 计数只反映本次真正抓取的新视频。新视频的详情/字幕补全按 `BILIBILI_ENRICH_CONCURRENCY` 并发执行（默认 4；开启 ASR 时自动回退为串行以保持预算语义）。
- LLM 审稿结果写入 `raw.llm_triage` 后会在后续刷新和重打分中保留并继续生效，不会被关键词分覆盖，也不会对同一条目重复花 API 费用。审稿请求按 `COMFYUI_NEWS_LLM_CONCURRENCY` 并发（默认 4），单条失败不会中断整轮，摘要中会以 `failed` 计数显示。
- 每个信源的抓取耗时（`duration_ms`）会记录在刷新结果里，可通过 `/api/source-health` 和 `/api/stats` 查看，便于定位慢源。

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
- `huggingface_models`
- `civitai_models`
- `youtube_search`
- `discord_feed`
- `forum_json`
- `json_feed`

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

推荐安装为每 2 小时一次的 Windows 计划任务（增量抓取下每轮仅约 20-90 秒）。一天只跑一次会有两个完整性问题：X/B 站这类快速滚动的信源会在两次抓取之间丢内容；而且当天日报在早晨导出后就不再更新。

```powershell
cd comfyui-news-tracker
.\scripts\install_scheduled_task.ps1 -Schedule Hourly -EveryHours 2 -LlmTriage
```

也可以装成每天固定时间一次：

```powershell
.\scripts\install_scheduled_task.ps1 -Schedule Daily -At 09:00 -Mode all -LlmTriage
```

每次运行的导出是**滚动窗口回补**：重新导出最近 `--export-days`（默认 3）天的日报文件，所以昨天/前天的归档会随着补抓的数据自动补全，不会冻结在某个时间点的状态。`-LlmTriage` 的审稿结果有持久化，频繁运行不会对同一条目重复扣费。

日报按 `COMFYUI_NEWS_TIMEZONE` 分日，默认 `Asia/Shanghai`。外部推送仍需要先配置 `COMFYUI_NEWS_WEBHOOK_URL` 或 `.secrets\webhook_url.txt`。

Web UI 的 Overview 会显示最近一次刷新是否完整成功，以及成功/失败信源数量。
刷新状态会区分 `inserted`、`updated`、`unchanged`，便于判断这次刷新是真的新增内容，还是只是重复拉取。

## 后续可以增强

- 增强 Bilibili/X 的登录态抓取、作者白名单和互动指标。
- 接入 YouTube、Discord/论坛、Hugging Face 模型卡和 Civitai 模型更新。
- 用 LLM 做摘要、中文翻译和更精确的去重聚类。
- 增加定时任务、邮件/飞书/企业微信推送。
- 增加多项目订阅，把 ComfyUI、Stable Diffusion、模型发布分成不同频道。
