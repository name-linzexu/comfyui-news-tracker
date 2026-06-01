from __future__ import annotations

import re
from collections.abc import Iterable


TAG_RULES: dict[str, tuple[str, ...]] = {
    "custom-nodes": (
        "custom node",
        "custom nodes",
        "node pack",
        "nodes",
        "node",
        "wrapper",
        "sampler",
        "scheduler",
        "attention",
        "节点",
        "节点包",
        "自定义节点",
    ),
    "workflow": (
        "workflow",
        "workflows",
        "json",
        "pipeline",
        "preset",
        "template",
        "工作流",
        "流程",
        "模板",
    ),
    "model": (
        "checkpoint",
        "lora",
        "lycoris",
        "finetune",
        "fine-tune",
        "fine tune",
        "model",
        "weights",
        "safetensors",
        "diffusion transformer",
        "dit",
        "flux",
        "kontext",
        "wan",
        "wan2",
        "qwen image",
        "qwen-image",
        "hunyuan",
        "ltx",
        "ltx-video",
        "sdxl",
        "sd3",
        "hidream",
        "z-image",
        "z image",
        "zimage",
        "模型",
        "视频模型",
        "图片模型",
        "微调",
        "权重",
        "底模",
        "检查点",
    ),
    "video": (
        "video",
        "image to video",
        "i2v",
        "text to video",
        "t2v",
        "wanvideo",
        "ltxvideo",
        "hunyuanvideo",
        "frame interpolation",
        "interpolation",
        "motion",
        "vae decode",
        "视频",
        "图生视频",
        "文生视频",
        "动作迁移",
        "对口型",
        "口型同步",
    ),
    "image-generation": (
        "image generation",
        "text to image",
        "txt2img",
        "img2img",
        "inpainting",
        "outpainting",
        "upscale",
        "super-resolution",
        "文生图",
        "图生图",
        "补图",
        "扩图",
        "放大",
    ),
    "quantization": (
        "gguf",
        "quant",
        "quantized",
        "quantization",
        "fp8",
        "nf4",
        "int8",
        "bitsandbytes",
        "量化",
    ),
    "plugin": ("manager", "extension", "plugin", "cli"),
    "performance": (
        "performance",
        "speed",
        "faster",
        "optimize",
        "optimization",
        "memory",
        "vram",
        "compile",
        "显存",
        "低显存",
        "加速",
        "提速",
        "优化",
    ),
    "bugfix": ("fix", "bug", "issue", "crash", "error", "修复", "报错"),
    "breaking": ("breaking", "migration", "deprecated", "removed", "迁移", "弃用", "破坏性"),
    "tutorial": ("guide", "tutorial", "how to", "example", "教程", "部署", "实测", "详解"),
}

HIGH_SIGNAL = (
    "release",
    "released",
    "launch",
    "launches",
    "breaking",
    "security",
    "manager",
    "frontend",
    "workflow",
    "custom node",
    "model",
    "checkpoint",
    "lora",
    "finetune",
    "fine-tune",
    "weights",
    "video",
    "wan",
    "flux",
    "qwen",
    "hunyuan",
    "ltx",
    "gguf",
    "fp8",
    "quant",
    "performance",
    "optimization",
    "support",
    "adapter",
    "comfyui",
    "发布",
    "更新",
    "上线",
    "模型",
    "节点",
    "工作流",
    "微调",
    "权重",
    "量化",
    "显存",
)

LOW_SIGNAL = (
    "question",
    "help",
    "how do i",
    "stuck",
    "docs",
    "readme",
    "typo",
    "format",
    "lint",
    "ci",
    "test",
    "tests",
    "chore",
    "cleanup",
    "refactor",
    "dependency bump",
    "bump",
)
UNSAFE_OR_LOW_VALUE = (
    "crack",
    "cracked",
    "activation",
    "license bypass",
    "keygen",
    "pirated",
    "torrent",
)

SOCIAL_NEWS_SUBJECTS = (
    "comfyui",
    "flux",
    "wan",
    "qwen",
    "hunyuan",
    "ltx",
    "z-image",
    "z image",
    "zimage",
    "hidream",
    "lora",
    "gguf",
    "fp8",
    "nf4",
    "checkpoint",
    "safetensors",
    "workflow",
    "workflows",
    "node",
    "nodes",
    "model",
    "vram",
    "模型",
    "视频模型",
    "图片模型",
    "工作流",
    "节点",
    "微调",
    "权重",
    "量化",
    "显存",
)

SOCIAL_NEWS_ACTIONS = (
    "release",
    "released",
    "launch",
    "launches",
    "update",
    "updated",
    "new",
    "added",
    "support",
    "supports",
    "adapter",
    "workflow",
    "tutorial",
    "guide",
    "example",
    "benchmark",
    "comparison",
    "faster",
    "speed",
    "optimize",
    "optimization",
    "low vram",
    "vram",
    "distill",
    "distilled",
    "lora",
    "gguf",
    "fp8",
    "nf4",
    "checkpoint",
    "safetensors",
    "发布",
    "上线",
    "更新",
    "新增",
    "支持",
    "适配",
    "教程",
    "部署",
    "工作流",
    "节点",
    "微调",
    "量化",
    "低显存",
    "显存",
    "加速",
    "优化",
    "实测",
    "对比",
)

SOCIAL_STRONG_NEWS_ACTIONS = (
    "release",
    "released",
    "launch",
    "launches",
    "update",
    "updated",
    "added",
    "support",
    "supports",
    "adapter",
    "tutorial",
    "guide",
    "benchmark",
    "comparison",
    "faster",
    "speed",
    "optimize",
    "optimization",
    "distill",
    "distilled",
    "lora",
    "gguf",
    "fp8",
    "nf4",
    "checkpoint",
    "safetensors",
    "发布",
    "上线",
    "更新",
    "新增",
    "支持",
    "适配",
    "教程",
    "部署",
    "详解",
    "精讲",
    "微调",
    "量化",
    "低显存",
    "加速",
    "优化",
    "实测",
    "对比",
)

SOCIAL_LOW_VALUE_PHRASES = (
    "parody account",
    "promoted",
    "sponsored",
    "giveaway",
    "link in bio",
    "onlyfans",
    "uncensored",
    "nsfw",
    "adult content",
    "content warning",
    "best options available",
    "best github repos",
    "top github repos",
    "github repos are among",
    "herramientas de ia para artistas",
    "tools for artists",
    "tool list",
    "ai tools list",
    "created in #",
    "#fantasyart",
    "#mysteryart",
    "#aiart",
    "role-based personas",
    "creative director, marketing strategist",
    "mayo en",
    "herramientas de ia",
    "美女视频",
    "太变态",
    "超变态",
    "大熊",
    "真人演员",
    "去水印",
    "无限画布",
    "每日分享三个",
    "整合包",
    "一键安装",
    "网盘",
    "秒杀 comfyui",
)

SOCIAL_LISTICLE_RE = re.compile(
    r"\b(?:top|best)\s+\d{0,2}\s*(?:open[- ]source\s+)?(?:ai\s+)?(?:tools|repos|repositories|options)\b"
    r"|\b\d{1,2}\s+github\s+repos\b",
    re.IGNORECASE,
)


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def has_social_news_signal(text: str) -> bool:
    value = normalize_text(text).lower()
    return any(word in value for word in SOCIAL_NEWS_SUBJECTS) and any(
        word in value for word in SOCIAL_NEWS_ACTIONS
    )


def has_strong_social_news_signal(text: str) -> bool:
    value = normalize_text(text).lower()
    return any(word in value for word in SOCIAL_NEWS_SUBJECTS) and any(
        word in value for word in SOCIAL_STRONG_NEWS_ACTIONS
    )


def is_low_value_social_text(text: str) -> bool:
    value = normalize_text(text).lower()
    if any(word in value for word in SOCIAL_LOW_VALUE_PHRASES):
        return True
    if SOCIAL_LISTICLE_RE.search(value):
        return True
    if re.match(r"^(?:and\s+)?@\w+\b", value) and not has_social_news_signal(value):
        return True
    if "stable diffusion + comfyui" in value and not any(
        word in value for word in ("release", "update", "workflow", "node", "lora", "gguf", "flux", "wan", "qwen")
    ):
        return True
    return False


def is_low_value_x_text(text: str) -> bool:
    return is_low_value_social_text(text)


def is_low_value_bilibili_text(text: str) -> bool:
    value = normalize_text(text).lower()
    if any(word in value for word in UNSAFE_OR_LOW_VALUE):
        return True
    bilibili_low_value = (
        "nsfw",
        "太变态",
        "超变态",
        "大熊",
        "美女视频",
        "真人演员",
        "去水印",
        "擦边",
        "无限画布",
        "每日分享三个",
        "整合包",
        "一键安装",
        "网盘",
        "秒杀 comfyui",
    )
    return any(word in value for word in bilibili_low_value)


def social_quality_adjustment(text: str) -> int:
    value = normalize_text(text).lower()
    if is_low_value_social_text(value):
        return -48
    if has_social_news_signal(value):
        return 8
    return -16


def is_social_source_type(source_type: str) -> bool:
    return source_type in {"bilibili_search", "x_search", "discord_feed", "forum_json", "json_feed"}


def extract_tags(title: str, summary: str, source_category: str) -> list[str]:
    text = f"{title} {summary}".lower()
    tags = {source_category}
    for tag, needles in TAG_RULES.items():
        if any(needle in text for needle in needles):
            tags.add(tag)
    return sorted(tags)


def score_item(
    *,
    title: str,
    summary: str,
    source_weight: int,
    source_type: str,
    source_tier: str,
    tags: Iterable[str],
    github_stars: int | None = None,
    interaction_count: int | None = None,
    trusted_author: bool = False,
) -> int:
    total = sum(
        score_breakdown(
            title=title,
            summary=summary,
            source_weight=source_weight,
            source_type=source_type,
            source_tier=source_tier,
            tags=tags,
            github_stars=github_stars,
            interaction_count=interaction_count,
            trusted_author=trusted_author,
        ).values()
    )
    return max(0, min(total, 100))


def score_breakdown(
    *,
    title: str,
    summary: str,
    source_weight: int,
    source_type: str,
    source_tier: str,
    tags: Iterable[str],
    github_stars: int | None = None,
    interaction_count: int | None = None,
    trusted_author: bool = False,
) -> dict[str, int]:
    text = f"{title} {summary}".lower()
    source_score = source_weight * 10
    if source_tier == "T1":
        source_score += 16
    elif source_tier == "T1.5":
        source_score += 8
    if source_type == "github_search_repos":
        source_score = min(source_score, 12)
    elif source_type == "github_issues":
        source_score = min(source_score, 10)
    elif source_type in {"bilibili_search", "x_search", "discord_feed", "forum_json", "json_feed"}:
        source_score += 12
    elif source_type in {"huggingface_models", "civitai_models"}:
        source_score += 10
    elif source_type in {"youtube_search", "youtube_rss"}:
        source_score += 6

    relevance = 0
    for needle in HIGH_SIGNAL:
        if needle in text:
            relevance += 8
    if source_type == "github_search_repos":
        relevance = min(relevance, 24)
    elif source_type in {"bilibili_search", "x_search", "discord_feed", "forum_json", "json_feed"}:
        relevance += 8
    elif source_type in {"huggingface_models", "civitai_models"}:
        relevance += 12
    elif source_type in {"youtube_search", "youtube_rss"}:
        relevance += 6

    penalty = 0
    for needle in LOW_SIGNAL:
        if needle in text:
            penalty -= 4
    for needle in UNSAFE_OR_LOW_VALUE:
        if needle in text:
            penalty -= 30
    if is_social_source_type(source_type):
        if source_type == "x_search":
            penalty += social_quality_adjustment(text)
        elif is_low_value_bilibili_text(text):
            penalty -= 36

    tag_set = set(tags)
    authority = 0
    if "official" in tag_set:
        authority += 16
    if trusted_author:
        authority += 14

    impact = 0
    if "breaking" in tag_set:
        impact += 14
    if "custom-nodes" in tag_set or "workflow" in tag_set:
        impact += 8
    if "model" in tag_set:
        impact += 16
    if "video" in tag_set:
        impact += 14
    if "image-generation" in tag_set:
        impact += 10
    if "quantization" in tag_set:
        impact += 10
    if "performance" in tag_set:
        impact += 8

    freshness = 0
    if source_type == "github_releases":
        freshness += 18
    if source_type == "github_commits":
        freshness += 4
    if source_type == "github_search_repos":
        freshness -= 10
    if is_social_source_type(source_type):
        freshness += 10
    if source_type in {"huggingface_models", "civitai_models"}:
        freshness += 12
    if source_type in {"youtube_search", "youtube_rss"}:
        freshness += 6
    if source_type == "rss" and source_tier == "T1":
        freshness += 8

    popularity = 0
    if github_stars and source_type != "github_search_repos":
        popularity += min(20, github_stars // 500)
    if interaction_count:
        popularity += min(20, int(interaction_count) // 100)
    if source_type == "github_search_repos":
        penalty -= 35
    if source_type == "github_issues":
        penalty -= 18

    total = source_score + relevance + authority + impact + freshness + popularity + penalty
    if source_type == "github_search_repos" and total > 48:
        penalty -= total - 48
        total = 48
    if is_social_source_type(source_type):
        if (
            (source_type == "x_search" and is_low_value_x_text(text))
            or (source_type == "bilibili_search" and is_low_value_bilibili_text(text))
        ) and total > 48:
            penalty -= total - 48
            total = 48
        elif source_type == "x_search" and is_low_value_social_text(text) and total > 52:
            penalty -= total - 52
            total = 52
        elif not has_strong_social_news_signal(text) and total > 76:
            penalty -= total - 76
            total = 76
        elif source_type == "x_search" and total > 92:
            penalty -= total - 92
            total = 92
        elif source_type == "bilibili_search" and total > 94:
            penalty -= total - 94
            total = 94
    if total < 0:
        penalty -= total
    elif total > 100:
        penalty -= total - 100

    return {
        "source": source_score,
        "relevance": relevance,
        "authority": authority,
        "impact": impact,
        "freshness": freshness,
        "popularity": popularity,
        "penalty": penalty,
    }
