from __future__ import annotations

"""Central vocabulary for model families and trusted publishers.

Every scoring/filter rule that needs to know "which model families matter" or
"which authors are first-party" should import from here instead of keeping its
own copy. New families can be added without code changes through an optional
`vocab:` section in config/sources.yml:

    vocab:
      model_families:
        newmodel: ["newmodel", "new-model"]
      official_model_orgs: ["newlab-ai"]
      trusted_converters: ["someconverter"]
"""

import re

import yaml

from .settings import settings


DEFAULT_MODEL_FAMILIES: dict[str, tuple[str, ...]] = {
    "flux": ("flux", "kontext"),
    "wan": ("wan", "wan2", "wanvideo"),
    "qwen-image": ("qwen image", "qwen-image", "qwenimage"),
    "hunyuan": ("hunyuan", "hunyuanvideo"),
    "ltx": ("ltx", "ltx-video", "ltxvideo"),
    "sdxl": ("sdxl",),
    "sd3": ("sd3",),
    "hidream": ("hidream",),
    "z-image": ("z-image", "z image", "zimage"),
    "ideogram": ("ideogram",),
    "krea": ("krea",),
    "bernini": ("bernini",),
    "scail": ("scail",),
    "longcat": ("longcat",),
}

DEFAULT_OFFICIAL_MODEL_ORGS: tuple[str, ...] = (
    "alibaba",
    "alibaba-pai",
    "black-forest-labs",
    "bytedance",
    "comfy-org",
    "comfyanonymous",
    "deepseek-ai",
    "diffusers",
    "genmo",
    "google",
    "hidream-ai",
    "ideogram-ai",
    "kwai-kolors",
    "lightricks",
    "ltx-video",
    "minimaxai",
    "qwen",
    "qwenlm",
    "skywork",
    "stabilityai",
    "tencent",
    "thudm",
    "wan-ai",
    "zhipuai",
)

DEFAULT_TRUSTED_CONVERTERS: tuple[str, ...] = (
    "calcuis",
    "city96",
    "kijai",
    "quantstack",
)


def _load_overlay() -> dict:
    try:
        data = yaml.safe_load(settings.sources_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    vocab = data.get("vocab")
    return vocab if isinstance(vocab, dict) else {}


def _merge_vocab() -> tuple[dict[str, tuple[str, ...]], frozenset[str], frozenset[str]]:
    overlay = _load_overlay()
    families = {name: tuple(aliases) for name, aliases in DEFAULT_MODEL_FAMILIES.items()}
    extra_families = overlay.get("model_families")
    if isinstance(extra_families, dict):
        for name, aliases in extra_families.items():
            if not isinstance(aliases, list):
                continue
            merged = [*families.get(str(name), ()), *(str(alias).lower() for alias in aliases)]
            families[str(name).lower()] = tuple(dict.fromkeys(merged))
    orgs = {org.lower() for org in DEFAULT_OFFICIAL_MODEL_ORGS}
    extra_orgs = overlay.get("official_model_orgs")
    if isinstance(extra_orgs, list):
        orgs.update(str(org).lower() for org in extra_orgs)
    converters = {name.lower() for name in DEFAULT_TRUSTED_CONVERTERS}
    extra_converters = overlay.get("trusted_converters")
    if isinstance(extra_converters, list):
        converters.update(str(name).lower() for name in extra_converters)
    return families, frozenset(orgs), frozenset(converters)


MODEL_FAMILIES, OFFICIAL_MODEL_ORGS, TRUSTED_CONVERTERS = _merge_vocab()


def model_family_terms() -> tuple[str, ...]:
    terms: list[str] = []
    for aliases in MODEL_FAMILIES.values():
        terms.extend(aliases)
    return tuple(dict.fromkeys(term.lower() for term in terms))


MODEL_FAMILY_TERMS = model_family_terms()


def _family_alternation() -> str:
    aliases = sorted(MODEL_FAMILY_TERMS, key=len, reverse=True)
    escaped = [re.escape(alias).replace("\\-", "[\\s\\-]?").replace(" ", "[\\s\\-]?") for alias in aliases]
    return "|".join(escaped)


MODEL_FAMILY_RE = re.compile(
    r"\b((?:" + _family_alternation() + r")(?:[\s\-]?\d+(?:\.\d+)*)?)\b",
    re.IGNORECASE,
)


def normalize_author(author: str | None) -> str:
    return (author or "").strip().lower().lstrip("@")


def is_official_model_org(author: str | None) -> bool:
    return normalize_author(author) in OFFICIAL_MODEL_ORGS


def is_trusted_converter(author: str | None) -> bool:
    return normalize_author(author) in TRUSTED_CONVERTERS


def mentions_model_family(text: str) -> bool:
    value = (text or "").lower()
    return any(term in value for term in MODEL_FAMILY_TERMS)
