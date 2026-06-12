from __future__ import annotations

import math
import re
from collections.abc import Iterable
from datetime import UTC, datetime

from . import vocab


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

# Compose model-family vocabulary from the central registry so a new family
# only needs one entry in app/vocab.py (or the sources.yml vocab: overlay).
TAG_RULES["model"] = tuple(dict.fromkeys((*TAG_RULES["model"], *vocab.MODEL_FAMILY_TERMS)))
HIGH_SIGNAL = tuple(dict.fromkeys((*HIGH_SIGNAL, *vocab.MODEL_FAMILY_TERMS)))
SOCIAL_NEWS_SUBJECTS = tuple(dict.fromkeys((*SOCIAL_NEWS_SUBJECTS, *vocab.MODEL_FAMILY_TERMS)))

COMMIT_PREFIX_RE = re.compile(
    r"^(?:commit:\s*)?(?:\[[^\]]+\]\s*)*([a-z][a-z0-9_./-]{0,24})(?:\([^)]*\))?\s*[:!]",
    re.IGNORECASE,
)
MAINTENANCE_COMMIT_TYPES = {
    "chore",
    "ci",
    "doc",
    "docs",
    "test",
    "tests",
    "refactor",
    "style",
    "build",
    "revert",
    "deps",
    "bump",
    "lint",
    "format",
}
FEATURE_COMMIT_TYPES = {"feat", "feature", "perf"}
FIX_COMMIT_TYPES = {"fix", "bugfix", "hotfix"}

MODEL_REUPLOAD_MARKERS = (
    "diffusers",
    "gguf",
    "fp8",
    "nf4",
    "bnb",
    "awq",
    "exl2",
    "int4",
    "int8",
    "4bit",
    "8bit",
    "merge",
    "merged",
    "lightning",
    "distill",
    "quant",
)

CIVITAI_PERSONAL_STYLE_TERMS = (
    "character",
    "anime",
    "cosplay",
    "waifu",
    "illustrious",
    "pony",
    "noobai",
    "face",
    "skin",
    "girl",
    "celebrity",
    "角色",
    "人脸",
    "真人",
    "美女",
)

PROMO_HOOK_PHRASES = (
    "stop using",
    "say goodbye",
    "game changer",
    "game-changer",
    "must have",
    "must-have",
    "you need this",
    "all you need",
    "insane",
    "mind blowing",
    "mind-blowing",
    "never go back",
    "blow your mind",
)

PROMO_BANG_RE = re.compile(r"!{2,}|！{2,}")


def is_promo_hype_title(title: str) -> bool:
    """Marketing-styled titles: emoji stacking, !!-chains, clickbait hooks."""
    value = normalize_text(title)
    lower = value.lower()
    emoji_count = sum(1 for ch in value if 0x1F300 <= ord(ch) <= 0x1FAFF)
    if emoji_count >= 2:
        return True
    if PROMO_BANG_RE.search(value) and emoji_count >= 1:
        return True
    return any(phrase in lower for phrase in PROMO_HOOK_PHRASES)


COMMUNITY_QUESTION_RE = re.compile(
    r"^(?:how|what|which|where|why|is there|are there|can i|can you|does|do i|anyone|any one|need help|help|best|recommend|looking for)\b"
    r"|\?\s*$",
    re.IGNORECASE,
)

SOCIAL_LOW_VALUE_PHRASES = (
    "parody account",
    "promoted",
    "sponsored",
    "giveaway",
    "help",
    "need help",
    "help me",
    "can i use",
    "how do i",
    "how can i",
    "my problem is",
    "problem is",
    "update problem",
    "not working",
    "does not work",
    "is there a model",
    "thanks -",
    "tried some",
    "for fun",
    "images for fun",
    "personal test",
    "just testing",
    "quick test",
    "middle finger",
    "prompt builder",
    "prompt generator",
    "prompt helper",
    "ai prompt generator",
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
    "pre made workflows",
    "pre-made workflows",
    "premade workflows",
    "my web ui using comfy ui",
    "just run.bat",
    "start cooking",
    "best coding llm",
    "current sota method",
    "temporal flickering issue",
    "have an error",
    "if anyone can help",
    "errors on re-installing",
    "this one step fixes",
    "hidden content system",
    "surprisingly usable",
    "is crazy for an open model",
    "comfyit.cn",
    "invite_code",
    "pan.quark.cn",
    "pan.baidu.com",
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
    "夸克",
    "百度",
    "百度网盘",
    "软件下载地址",
    "提示词大师",
    "管理大师",
    "搅拌站",
    "记录一下",
    "整个过程还挺有意思",
    "秒杀 comfyui",
    "竖中指",
)

BEGINNER_OR_COURSE_MARKETING_PHRASES = (
    "for beginners",
    "beginner friendly",
    "beginner-friendly",
    "beginner tutorial",
    "beginner guide",
    "zero to hero",
    "from zero",
    "masterclass",
    "bootcamp",
    "paid course",
    "course discount",
    "join my course",
    "新手入门",
    "新手教程",
    "新手必看",
    "新手向",
    "小白入门",
    "小白教程",
    "零基础",
    "0基础",
    "零门槛",
    "从0开始",
    "从零开始",
    "从零到",
    "入门到精通",
    "基础入门",
    "基础教程",
    "基础系列",
    "基础课",
    "入门课",
    "公开课",
    "体验课",
    "实战课",
    "实战班",
    "商业变现",
    "保姆级",
    "手把手",
    "傻瓜式",
    "安装教程",
    "部署教程",
    "从安装到实战",
    "带你从安装",
    "环境配置",
    "工作流概览",
    "是否值得上手",
    "全套教程",
    "系统教程",
    "系统课",
    "训练营",
    "付费课程",
    "课程优惠",
    "课程咨询",
    "私教",
    "陪跑",
    "卖课",
    "付费社群",
    "知识星球",
    "加入星球",
    "加群领取",
    "进群领取",
    "扫码加群",
    "交流群领取",
    "更多资料评论区领取",
    "资料评论区领取",
    "评论区领取",
    "评论区留言",
    "评论区自取",
    "评论区获取",
    "评论区拿",
    "配套素材",
    "配套资料",
    "领取资料",
    "领取工作流",
    "领取素材",
    "领取安装包",
    "点击领取",
    "点击链接",
    "等不急的点击",
    "等不及的点击",
    "留言666",
    "回复666",
)

BEGINNER_STANDALONE_PHRASES = (
    "beginner",
    "beginners",
    "newbie",
    "newbies",
    "新手",
    "小白",
    "零基础",
    "0基础",
    "零门槛",
    "入门",
    "从0开始",
    "从零开始",
    "从零到",
    "保姆级",
    "手把手",
    "傻瓜式",
)

SOCIAL_LISTICLE_RE = re.compile(
    r"\b(?:top|best)\s+\d{0,2}\s*(?:open[- ]source\s+)?(?:ai\s+)?(?:tools|repos|repositories|options)\b"
    r"|\b\d{1,2}\s+github\s+repos\b",
    re.IGNORECASE,
)

BEGINNER_PATTERN_RE = re.compile(
    r"\b(?:beginner|beginners|newbie|newbies)\b|(?:新手|小白|零基础|0基础|入门|基础|保姆级|手把手).{0,12}(?:教程|教学|指南|课|课程|训练营|部署|安装)",
    re.IGNORECASE,
)

COURSE_MARKETING_RE = re.compile(
    r"(?:教程|课程|训练营|社群|星球).{0,16}(?:付费|优惠|报名|咨询|加群|扫码|领取|私教|陪跑|实战班)"
    r"|(?:付费|优惠|报名|咨询|扫码|领取|私教|陪跑).{0,16}(?:教程|课程|训练营|社群|星球)",
    re.IGNORECASE,
)

SOCIAL_LEAD_GEN_RE = re.compile(
    r"(?:更多资料|资料|素材|工作流|安装包|模型包|整合包).{0,20}(?:评论区|留言|回复|私信|点击|领取|获取|自取)"
    r"|(?:评论区|留言|回复|私信|点击).{0,20}(?:资料|素材|工作流|安装包|模型包|整合包|领取|获取|自取|666)"
    r"|(?:公众号|公/众/号|vx|v / x|微信|技术交流).{0,24}(?:扫码|扫视频|课程|教程|推送|关注|加群|领取|咨询)"
    r"|(?:扫码|扫视频|关注|加群|领取|咨询).{0,24}(?:公众号|公/众/号|vx|v / x|微信|课程|教程|好课|技术交流)"
    r"|(?:comment|reply|dm).{0,20}(?:666|files?|workflow|pack|download|get)"
    r"|https?://www\.bilibili\.com/opus/",
    re.IGNORECASE,
)

BILIBILI_HARD_PROMO_RE = re.compile(
    r"(?:抢先下载|附工作流|含工作流|工作流资料|工作流和资料|配套资料|配套工作流|下载链接|工作流\s*https?|官注|关注\+|评论掉落|平论掉落|领取|快来领取|邀请码|新人福利码|runninghub|学到就是赚到|低价好课|免费课程|课程推送|扫码|扫视频|公众号|公/众/号|翻译整理|远程部署|技术支持|定制开发|工作流定制|不会写提示词|不想直接写提示词|不会写代码|提示词优化|prompt builder|prompt generator|模型分享|api注册地址|注册可获得|注册送|交流群|私有问必答|提取码|网盘|云盘|自取|分享文件|pan\.baidu|pan\.quark|pan\.xunlei)",
    re.IGNORECASE,
)

BILIBILI_TUTORIAL_NOISE_RE = re.compile(
    r"(?:教程|教学|使用指南|指南|值得入手吗|本地部署|部署工作流|一键|上手|系列\s*\d+|第\s*\d+\s*集|不会写提示词|提示词|创作神器|模型分享)",
    re.IGNORECASE,
)

BILIBILI_EXPLAINER_NOISE_RE = re.compile(
    r"(?:本期视频分享|实践经验|你将看到|学习路线|人工智能训练师|每天半小时|ai知识|带你玩转|全解析|上手感受)",
    re.IGNORECASE,
)

BILIBILI_SOFT_CONTENT_RE = re.compile(
    r"(?:教程|教学|使用指南|指南|值得入手吗|本地部署|部署工作流|出图教程|运行教程|实测|测试|对比|评测|全解析|精讲|详解|演示|体验|教你|带你|本期视频|你将看到|全流程|怎么使用|如何使用|揭秘|不评论，只放视频|分享了|分享文件|工作流|动作迁移|姿态迁移|换脸|face swap|高清放大|本地.{0,12}跑|跑通|做视频|生图|出图|生成视频|生成.{0,8}图)",
    re.IGNORECASE,
)

BILIBILI_RELEASE_SIGNAL_RE = re.compile(
    r"(?:发布|开源|上线|更新|新增|适配|支持|节点更新|模型发布|release|released|launch|update|updated|added|support|supports|adapter|v\d)",
    re.IGNORECASE,
)

BILIBILI_DIRECT_NEWS_RE = re.compile(
    r"(?:节点更新|模型更新|插件更新|模型发布|节点发布|插件发布|新增.{0,16}(?:模型|节点|插件|适配|支持)|(?:模型|节点|插件).{0,8}(?:适配|发布)|(?:现已|已经|已在|上线|接入).{0,24}(?:comfyui|节点|模型|插件|支持)|(?:发布|开源|上线).{0,20}(?:模型|节点|插件|lora|工作流)|day-0 support|support.{0,20}comfyui)",
    re.IGNORECASE,
)

BILIBILI_HIGH_DIRECT_NEWS_RE = re.compile(
    r"(?:day-0 support|现已.{0,16}(?:原生支持|上线|接入)|原生支持|节点更新|模型发布|节点发布|插件发布|partner nodes)",
    re.IGNORECASE,
)

BILIBILI_NEWS_ALLOW_RE = re.compile(
    r"(?:节点更新|模型更新|插件更新|模型发布|节点发布|插件发布|版本更新|新增.{0,16}(?:模型|节点|插件|适配|支持)|(?:模型|节点|插件).{0,8}(?:适配|发布)|(?:发布|开源|上线).{0,20}(?:模型|节点|插件|lora|工作流)|github\.com/|huggingface\.co/)",
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


def is_beginner_or_course_marketing_text(text: str) -> bool:
    value = normalize_text(text).lower()
    if any(word in value for word in BEGINNER_STANDALONE_PHRASES):
        return True
    if any(word in value for word in BEGINNER_OR_COURSE_MARKETING_PHRASES):
        return True
    if BEGINNER_PATTERN_RE.search(value):
        return True
    return bool(COURSE_MARKETING_RE.search(value))


def is_social_lead_gen_text(text: str) -> bool:
    return bool(SOCIAL_LEAD_GEN_RE.search(normalize_text(text).lower()))


DEEP_DIVE_SIGNALS = (
    "实测",
    "评测",
    "详解",
    "精讲",
    "深度解析",
    "全解析",
    "解读",
    "拆解",
    "讲解",
    "对比",
    "横评",
    "参数",
    "技巧",
    "提速",
    "加速",
    "优化",
    "显存",
    "低显存",
    "出图质量",
    "踩坑",
    "避坑",
    "原理",
    "deep dive",
    "deep-dive",
    "explained",
    "explainer",
    "hands-on",
    "hands on",
    "benchmark",
    "comparison",
    "compared",
    "tips",
    "optimization",
    "optimize",
    "optimized",
    "in-depth",
    "breakdown",
    "vram",
    "settings",
    "best practices",
)


def is_model_deep_dive(text: str) -> bool:
    """Quality second-hand interpretation of a current model family.

    The opposite of beginner tutorials / course marketing: requires a known
    model family plus concrete explanation, testing, or optimization signals.
    """
    value = normalize_text(text).lower()
    if not vocab.mentions_model_family(value):
        return False
    if is_beginner_or_course_marketing_text(value) or is_social_lead_gen_text(value):
        return False
    return any(signal in value for signal in DEEP_DIVE_SIGNALS)


def is_low_value_social_text(text: str) -> bool:
    value = normalize_text(text).lower()
    if is_beginner_or_course_marketing_text(value):
        return True
    if is_social_lead_gen_text(value):
        return True
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


BILIBILI_EXTRA_LOW_VALUE = (
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
    "低价好课",
    "免费课程",
    "课程推送",
    "扫码",
    "扫视频开头的码",
    "公众号",
    "公/众/号",
    "技术交流",
    "上车",
)


def is_hard_low_value_bilibili_text(text: str) -> bool:
    """Noise that is never acceptable: marketing, lead-gen, unsafe, promo spam.

    Tutorial/explainer phrasing is handled separately so quality deep-dives
    can be exempted while this hard tier always stays filtered.
    """
    value = normalize_text(text).lower()
    if any(word in value for word in UNSAFE_OR_LOW_VALUE):
        return True
    if is_beginner_or_course_marketing_text(value):
        return True
    if is_social_lead_gen_text(value):
        return True
    if BILIBILI_HARD_PROMO_RE.search(value):
        return True
    if any(word in value for word in SOCIAL_LOW_VALUE_PHRASES):
        return True
    return any(word in value for word in BILIBILI_EXTRA_LOW_VALUE)


def is_low_value_bilibili_text(text: str) -> bool:
    value = normalize_text(text).lower()
    if is_hard_low_value_bilibili_text(value):
        return True
    return is_bilibili_tutorial_or_promo_text(value)


def is_bilibili_tutorial_or_promo_text(text: str) -> bool:
    value = normalize_text(text).lower()
    if BILIBILI_HARD_PROMO_RE.search(value):
        return True
    if BILIBILI_EXPLAINER_NOISE_RE.search(value) and not BILIBILI_NEWS_ALLOW_RE.search(value):
        return True
    if BILIBILI_TUTORIAL_NOISE_RE.search(value) and not BILIBILI_NEWS_ALLOW_RE.search(value):
        return True
    return False


def bilibili_score_cap(
    text: str,
    interaction_count: int | None = None,
    author_followers: int | None = None,
) -> int | None:
    value = normalize_text(text).lower()
    if is_hard_low_value_bilibili_text(value):
        return 48
    # Quality creator interpretations of current models (hands-on tests,
    # optimization breakdowns) with real adoption rank close to news.
    deep_dive_backed = is_model_deep_dive(value) and (
        (interaction_count or 0) >= 150 or (author_followers or 0) >= 5000
    )
    if is_low_value_bilibili_text(value):
        # Only soft tutorial/explainer phrasing remains at this point.
        return 86 if deep_dive_backed else 48
    if deep_dive_backed:
        return 86
    is_soft = bool(
        BILIBILI_SOFT_CONTENT_RE.search(value)
        or BILIBILI_TUTORIAL_NOISE_RE.search(value)
        or BILIBILI_EXPLAINER_NOISE_RE.search(value)
    )
    if not is_soft:
        return None
    if BILIBILI_HIGH_DIRECT_NEWS_RE.search(value) and not (
        BILIBILI_TUTORIAL_NOISE_RE.search(value) or BILIBILI_EXPLAINER_NOISE_RE.search(value)
    ):
        return None
    if BILIBILI_DIRECT_NEWS_RE.search(value):
        return 84 if interaction_count and interaction_count >= 500 else 76
    if interaction_count and interaction_count >= 1000:
        return 68
    return 56


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


def engagement_velocity_points(
    interaction_count: int | None,
    published_at: datetime | None,
    *,
    now: datetime | None = None,
) -> int:
    """Log-scaled engagement-per-day so virality differentiates instead of saturating.

    Roughly: 10/day -> 7, 100/day -> 14, 1,000/day -> 21, 10,000/day -> 28 (cap).
    Unknown publish time is treated as one day old.
    """
    count = int(interaction_count or 0)
    if count <= 0:
        return 0
    now = now or datetime.now(UTC)
    age_days = 1.0
    if published_at is not None:
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=UTC)
        age_days = max((now - published_at).total_seconds() / 86400, 0.5)
    velocity = count / age_days
    return min(28, int(round(7 * math.log10(max(velocity, 1.0)))))


def follower_authority_points(author_followers: int | None) -> int:
    """Log-scaled author reach: 1k -> 4, 10k -> 8, 100k -> 12, 1M -> 16 (cap)."""
    followers = int(author_followers or 0)
    if followers < 100:
        return 0
    return max(0, min(16, int(round(4 * (math.log10(followers) - 2)))))


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
    author: str | None = None,
    source_id: str | None = None,
    published_at: datetime | None = None,
    author_followers: int | None = None,
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
            author=author,
            source_id=source_id,
            published_at=published_at,
            author_followers=author_followers,
        ).values()
    )
    return max(0, min(total, 100))


def is_commit_source(source_type: str, source_id: str | None) -> bool:
    return source_type == "github_commits" or "commit" in (source_id or "")


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
    author: str | None = None,
    source_id: str | None = None,
    published_at: datetime | None = None,
    author_followers: int | None = None,
) -> dict[str, int]:
    text = f"{title} {summary}".lower()
    title_lower = title.lower()
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
    # Keyword stuffing should not buy unbounded relevance.
    relevance = min(relevance, 32)
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
    if source_type in {"huggingface_models", "civitai_models"}:
        if vocab.is_official_model_org(author):
            authority += 14
        elif vocab.is_trusted_converter(author):
            authority += 8
    authority += follower_authority_points(author_followers)

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
    impact = min(impact, 32)

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
    popularity += engagement_velocity_points(interaction_count, published_at)
    if source_type == "github_search_repos":
        penalty -= 35
    if source_type == "github_issues":
        penalty -= 18

    total = source_score + relevance + authority + impact + freshness + popularity + penalty
    if source_type == "github_search_repos" and total > 48:
        penalty -= total - 48
        total = 48
    if source_type in {"github_commits", "rss"} and source_tier == "T1" and "bugfix" in tag_set:
        commit_bugfix_cap = 84 if "performance" in tag_set else 72
        if total > commit_bugfix_cap:
            penalty -= total - commit_bugfix_cap
            total = commit_bugfix_cap
    if source_type in {"github_commits", "rss"} and source_tier == "T1":
        commit_match = COMMIT_PREFIX_RE.match(text)
        commit_type = commit_match.group(1).lower() if commit_match else ""
        if commit_type in MAINTENANCE_COMMIT_TYPES:
            maintenance_cap = 48
            if total > maintenance_cap:
                penalty -= total - maintenance_cap
                total = maintenance_cap
        elif (
            commit_type
            and commit_type not in FEATURE_COMMIT_TYPES
            and commit_type not in FIX_COMMIT_TYPES
            and is_commit_source(source_type, source_id)
        ):
            # Unknown subsystem prefixes ("mm:", "main:", ...) are internal
            # plumbing commits, not release news.
            subsystem_cap = 62
            if total > subsystem_cap:
                penalty -= total - subsystem_cap
                total = subsystem_cap
    if (
        source_type == "huggingface_models"
        and not vocab.is_official_model_org(author)
        and not vocab.is_trusted_converter(author)
    ):
        unknown_author_cap: int | None = None
        if vocab.mentions_model_family(title_lower) and any(
            marker in title_lower for marker in MODEL_REUPLOAD_MARKERS
        ):
            # Re-uploads/conversions of known model families by unknown
            # authors: useful only once the community actually adopts them.
            unknown_author_cap = 72 if (interaction_count or 0) >= 200 else 58
        elif (interaction_count or 0) < 10:
            # Unknown publisher with no community adoption signal yet.
            unknown_author_cap = 60
        if unknown_author_cap is not None and total > unknown_author_cap:
            penalty -= total - unknown_author_cap
            total = unknown_author_cap
    if source_type == "civitai_models" and any(term in title_lower for term in CIVITAI_PERSONAL_STYLE_TERMS):
        personal_cap = 56
        if total > personal_cap:
            penalty -= total - personal_cap
            total = personal_cap
    if (
        source_type in {"civitai_models", "huggingface_models"}
        or (source_type == "rss" and source_tier == "T2")
        or is_social_source_type(source_type)
    ) and is_promo_hype_title(title):
        # Marketing-styled titles stay listed but never reach the featured bar;
        # LLM triage can still promote genuinely important ones.
        hype_cap = 58
        if total > hype_cap:
            penalty -= total - hype_cap
            total = hype_cap
    if source_type == "rss" and source_tier == "T2":
        if COMMUNITY_QUESTION_RE.search(title_lower) or is_low_value_social_text(text):
            community_cap = 56
        elif has_strong_social_news_signal(text):
            community_cap = 88
        else:
            community_cap = 76
        if total > community_cap:
            penalty -= total - community_cap
            total = community_cap
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
        elif source_type == "bilibili_search":
            cap = bilibili_score_cap(text, interaction_count, author_followers)
            if cap is not None and total > cap:
                penalty -= total - cap
                total = cap
            elif total > 94:
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


def apply_llm_triage(
    *,
    score: int,
    featured: bool,
    reason: str,
    cluster_key: str,
    cluster_title: str,
    triage: dict,
    title: str | None = None,
) -> tuple[int, bool, str, str, str]:
    """Re-apply a stored LLM triage decision on top of rule-based scoring.

    Keeps LLM verdicts stable across refresh and rescore runs. Idempotent:
    keep-reasons are appended at most once.
    """
    decision = str(triage.get("decision") or "").strip().lower()
    note = str(triage.get("reason") or "") or str(triage.get("content_type") or "")
    importance = _triage_int(triage.get("importance"))
    confidence = _triage_int(triage.get("confidence"))
    if decision == "reject":
        return min(score, 20), False, f"LLM triage rejected: {note}"[:1000], cluster_key, cluster_title
    if decision == "downgrade":
        return min(score, 48), False, f"LLM triage downgraded: {note}"[:1000], cluster_key, cluster_title
    if decision == "keep":
        if importance >= 80:
            # A keep verdict can rescue rule-capped items, but marketing-styled
            # titles never ride the boost to the top of the feed.
            boosted = importance
            if title and is_promo_hype_title(title):
                boosted = min(boosted, 75)
            score = max(score, boosted)
        featured = featured or (score >= 72 and importance >= 78 and confidence >= 65)
        triage_reason = str(triage.get("reason") or "")
        if triage_reason and "LLM triage" not in reason:
            reason = f"{reason}; LLM triage kept: {triage_reason}" if reason else f"LLM triage kept: {triage_reason}"
        cluster_key = str(triage.get("cluster_key") or "") or cluster_key
        cluster_title = str(triage.get("zh_title") or "") or cluster_title
        return score, featured, reason[:1000], cluster_key, cluster_title
    return score, featured, reason, cluster_key, cluster_title


def _triage_int(value: object) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
