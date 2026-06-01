from __future__ import annotations

import unittest

from app.scoring import (
    has_social_news_signal,
    is_low_value_bilibili_text,
    is_low_value_social_text,
    score_breakdown,
    score_item,
)
from app.sources import Source, build_item, cluster_key_for, is_featured_item, is_low_value_t2_item


class ScoringTests(unittest.TestCase):
    def test_score_breakdown_sums_to_clamped_score(self) -> None:
        tags = ["official", "workflow", "custom-nodes"]
        breakdown = score_breakdown(
            title="ComfyUI frontend release workflow update",
            summary="Adds custom node workflow improvements",
            source_weight=5,
            source_type="github_releases",
            source_tier="T1",
            tags=tags,
            github_stars=9000,
        )
        score = score_item(
            title="ComfyUI frontend release workflow update",
            summary="Adds custom node workflow improvements",
            source_weight=5,
            source_type="github_releases",
            source_tier="T1",
            tags=tags,
            github_stars=9000,
        )
        self.assertEqual(score, sum(breakdown.values()))
        self.assertLessEqual(score, 100)
        self.assertGreaterEqual(score, 0)

    def test_unsafe_terms_are_penalized_and_filtered(self) -> None:
        source = Source(
            id="github-comfyui-topics",
            name="GitHub ComfyUI topics",
            type="github_search_repos",
            url="https://api.github.com/search/repositories",
            category="ecosystem",
            weight=2,
            tier="T2",
        )
        raw = {
            "full_name": "example/ComfyUI-Pro-cracked",
            "description": "ComfyUI Pro cracked activation guide",
            "topics": ["comfyui"],
            "stargazers_count": 5,
        }
        self.assertTrue(
            is_low_value_t2_item(
                "example/ComfyUI-Pro-cracked",
                "ComfyUI Pro cracked activation guide",
                source,
                raw,
            )
        )

    def test_t2_github_search_requires_direct_comfyui_relevance(self) -> None:
        source = Source(
            id="github-comfyui-search",
            name="GitHub ComfyUI repositories",
            type="github_search_repos",
            url="https://api.github.com/search/repositories",
            category="ecosystem",
            weight=2,
            tier="T2",
        )
        weak_raw = {
            "full_name": "someone/general-image-tool",
            "description": "Mentions stable diffusion in passing",
            "topics": [],
            "stargazers_count": 0,
        }
        strong_raw = {
            "full_name": "someone/ComfyUI-useful-node",
            "description": "Custom node pack",
            "topics": [],
            "stargazers_count": 0,
        }
        self.assertTrue(is_low_value_t2_item("someone/general-image-tool", "", source, weak_raw))
        self.assertFalse(is_low_value_t2_item("someone/ComfyUI-useful-node", "Custom node pack", source, strong_raw))

    def test_featured_requires_real_news_signal_for_commits_and_community(self) -> None:
        commit_source = Source(
            id="comfyui-commits-atom",
            name="ComfyUI commits atom",
            type="rss",
            url="https://github.com/comfyanonymous/ComfyUI/commits/master.atom",
            category="official",
            weight=5,
            tier="T1",
        )
        community_source = Source(
            id="reddit-comfyui",
            name="Reddit r/comfyui",
            type="rss",
            url="https://www.reddit.com/r/comfyui/.rss",
            category="community",
            weight=2,
            tier="T2",
        )

        self.assertFalse(
            is_featured_item(
                score=90,
                source=commit_source,
                title="chore: remove unused import",
                summary="small cleanup",
                tags=["official"],
            )
        )
        self.assertFalse(
            is_featured_item(
                score=100,
                source=commit_source,
                title="Remove old portable updater migration code.",
                summary="Delete old updater cleanup",
                tags=["official", "breaking"],
            )
        )
        self.assertTrue(
            is_featured_item(
                score=90,
                source=commit_source,
                title="fix(multigpu): performance improvement",
                summary="speed up model loading",
                tags=["official", "bugfix"],
            )
        )
        self.assertTrue(
            is_featured_item(
                score=100,
                source=commit_source,
                title="feat: add new nodes for Wan video model",
                summary="Adds partner nodes for a new video model.",
                tags=["official", "custom-nodes", "model", "video"],
            )
        )
        self.assertFalse(
            is_featured_item(
                score=72,
                source=community_source,
                title="I can't get Flux workflow to work",
                summary="help with error",
                tags=["community", "workflow"],
            )
        )
        self.assertFalse(
            is_featured_item(
                score=90,
                source=community_source,
                title="Windows fatal exception with Wan workflow",
                summary="Help me debug this error.",
                tags=["community", "model", "video", "workflow", "bugfix"],
            )
        )

    def test_model_video_node_release_is_featured(self) -> None:
        source = Source(
            id="wanvideo-wrapper-releases-atom",
            name="ComfyUI WanVideo Wrapper releases",
            type="rss",
            url="https://github.com/kijai/ComfyUI-WanVideoWrapper/releases.atom",
            category="model_nodes",
            weight=5,
            tier="T1.5",
        )
        item = build_item(
            source=source,
            title="WanVideoWrapper v2.1 release: Wan 2.2 I2V model support",
            summary="Adds new video generation nodes, FP8 weights, and lower VRAM optimizations for ComfyUI.",
            url="https://github.com/kijai/ComfyUI-WanVideoWrapper/releases/tag/v2.1",
            published_at=None,
            keywords={"include": ["comfyui", "wan", "video"], "exclude": []},
        )

        self.assertIsNotNone(item)
        assert item is not None
        self.assertTrue(item.featured)
        self.assertIn("model", item.tags)
        self.assertIn("video", item.tags)
        self.assertIn("performance", item.tags)

    def test_github_search_repos_do_not_become_featured_news(self) -> None:
        source = Source(
            id="github-comfyui-search",
            name="GitHub ComfyUI repositories",
            type="github_search_repos",
            url="https://api.github.com/search/repositories",
            category="ecosystem",
            weight=2,
            tier="T2",
        )
        self.assertFalse(
            is_featured_item(
                score=100,
                source=source,
                title="popular/comfyui-model-node",
                summary="ComfyUI model workflow node repository with release support",
                tags=["ecosystem", "model", "workflow"],
                github_stars=5000,
            )
        )

    def test_social_model_updates_are_featured_news(self) -> None:
        source = Source(
            id="x-comfyui-models",
            name="X ComfyUI model/node search",
            type="x_search",
            url="local://x-search?q=ComfyUI Flux",
            category="community",
            weight=2,
            tier="T2",
        )
        self.assertTrue(
            is_featured_item(
                score=60,
                source=source,
                title="ComfyUI Flux workflow update",
                summary="New Flux model workflow tutorial with GGUF low VRAM node support.",
                tags=["community", "model", "workflow", "quantization"],
            )
        )

    def test_social_listicles_and_parody_posts_are_not_featured_news(self) -> None:
        source = Source(
            id="x-comfyui-models",
            name="X ComfyUI model/node search",
            type="x_search",
            url="local://x-search?q=ComfyUI Flux",
            category="community",
            weight=2,
            tier="T2",
        )

        listicle = "If you want to generate AI images locally for free, these 9 GitHub repos are among the best options available right now."
        parody = "Parody account #comfyui LTX 2.3 video üretimi 31"

        self.assertTrue(is_low_value_social_text(listicle))
        self.assertTrue(is_low_value_social_text(parody))
        self.assertFalse(has_social_news_signal(listicle))
        self.assertFalse(
            is_featured_item(
                score=100,
                source=source,
                title=listicle,
                summary=listicle,
                tags=["community", "model", "workflow"],
            )
        )
        self.assertFalse(
            is_featured_item(
                score=100,
                source=source,
                title=parody,
                summary=parody,
                tags=["community", "model", "video"],
            )
        )

    def test_bilibili_chinese_model_node_update_is_featured_news(self) -> None:
        source = Source(
            id="bilibili-comfyui-models",
            name="Bilibili ComfyUI model/node search",
            type="bilibili_search",
            url="local://bilibili-search?q=ComfyUI 新模型",
            category="community",
            weight=2,
            tier="T2",
        )

        title = "ComfyUI 教程：Qwen 节点更新与 Wan 图生视频工作流部署"
        summary = "新增 Qwen 模型节点适配，支持 Wan 视频模型工作流和低显存量化配置。"

        item = build_item(
            source=source,
            title=title,
            summary=summary,
            url="https://www.bilibili.com/video/BV123",
            published_at=None,
            keywords={"include": ["comfyui"], "exclude": []},
        )

        self.assertIsNotNone(item)
        assert item is not None
        self.assertTrue(item.featured)
        self.assertIn("custom-nodes", item.tags)
        self.assertIn("workflow", item.tags)
        self.assertIn("model", item.tags)
        self.assertFalse(is_low_value_bilibili_text("ComfyUI 四大加载器精讲｜Image / Flux / Qwen / Wan 一次搞懂"))
        self.assertTrue(is_low_value_bilibili_text("太变态 NSFW 美女视频 ComfyUI 工作流"))
        self.assertTrue(is_low_value_bilibili_text("每日分享三个超变态的Ai 工作流 模板"))
        self.assertTrue(is_low_value_bilibili_text("2026新版 ComfyUI 整合包 Win+Mac一键安装 全套工作流"))


class SourceBuildTests(unittest.TestCase):
    def test_build_item_populates_reason_breakdown_and_cluster(self) -> None:
        source = Source(
            id="comfyui-repo-commits",
            name="ComfyUI commits",
            type="github_commits",
            url="https://api.github.com/repos/comfyanonymous/ComfyUI/commits",
            category="official",
            weight=4,
            tier="T1",
        )
        item = build_item(
            source=source,
            title="Commit: feat: add Wan video model partner nodes (#14202)",
            summary="Adds new ComfyUI partner nodes for a Wan video model.",
            url="https://github.com/Comfy-Org/ComfyUI/commit/abc123",
            published_at=None,
            keywords={"include": ["comfyui"], "exclude": []},
            author="comfyanonymous",
        )
        self.assertIsNotNone(item)
        assert item is not None
        self.assertTrue(item.featured)
        self.assertIn("official", item.tags)
        self.assertIn("primary source", item.reason)
        self.assertEqual(item.cluster_key, "github-event:comfy-org/comfyui/commit/abc123")
        self.assertIn("source", item.score_breakdown or {})

    def test_cluster_key_uses_repo_for_broad_github_repos(self) -> None:
        key = cluster_key_for(
            "Deno2026/comfyui-deno-custom-nodes",
            "ComfyUI custom nodes",
            "https://github.com/Deno2026/comfyui-deno-custom-nodes",
        )
        self.assertEqual(key, "github:deno2026/comfyui-deno-custom-nodes")


if __name__ == "__main__":
    unittest.main()
