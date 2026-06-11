from __future__ import annotations

import unittest

from app.scoring import (
    bilibili_score_cap,
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

    def test_commit_bugfix_does_not_score_like_release_news(self) -> None:
        score = score_item(
            title="[Partner Nodes] fix: respect VideoSlice trim when resizing videos (#14213)",
            summary="Bugfix for VideoSlice trim when resizing videos.",
            source_weight=5,
            source_type="rss",
            source_tier="T1",
            tags=["official", "custom-nodes", "video", "bugfix"],
        )

        self.assertLessEqual(score, 72)

    def test_official_maintenance_commits_do_not_fill_digest(self) -> None:
        score = score_item(
            title="chore(openapi): sync shared API contract from cloud@5273c30 (#14266)",
            summary="chore(openapi): sync shared API contract from cloud@5273c30",
            source_weight=5,
            source_type="rss",
            source_tier="T1",
            tags=["official"],
        )

        self.assertLessEqual(score, 48)

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
                score=100,
                source=commit_source,
                title="[Partner Nodes] fix: respect VideoSlice trim when resizing videos (#14213)",
                summary="Bugfix for VideoSlice trim when resizing videos.",
                tags=["official", "custom-nodes", "video", "bugfix"],
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

    def test_beginner_and_course_marketing_social_posts_are_filtered(self) -> None:
        bilibili = Source(
            id="bilibili-comfyui-models",
            name="Bilibili ComfyUI model/node search",
            type="bilibili_search",
            url="local://bilibili-search?q=ComfyUI 新模型",
            category="community",
            weight=2,
            tier="T2",
        )
        x_source = Source(
            id="x-comfyui-models",
            name="X ComfyUI model/node search",
            type="x_search",
            url="local://x-search?q=ComfyUI Flux",
            category="community",
            weight=2,
            tier="T2",
        )
        youtube = Source(
            id="youtube-comfyui-models",
            name="YouTube ComfyUI model/workflow search",
            type="youtube_search",
            url="local://youtube-search?q=ComfyUI Flux",
            category="community",
            weight=2,
            tier="T2",
        )
        civitai = Source(
            id="civitai-comfyui-models",
            name="Civitai ComfyUI models",
            type="civitai_models",
            url="https://civitai.com/api/v1/models",
            category="models",
            weight=2,
            tier="T2",
        )

        self.assertTrue(is_low_value_bilibili_text("ComfyUI 零基础新手入门教程，保姆级部署安装"))
        self.assertTrue(is_low_value_bilibili_text("ComfyUI 新模型发布（小白福音）"))
        self.assertTrue(is_low_value_bilibili_text("40 岁小白学 LTX 2.3｜循序渐进弄懂视频生成原理"))
        self.assertTrue(is_low_value_bilibili_text("工作流基础系列25- LTX 2.3文生视频工作流"))
        self.assertTrue(is_low_value_bilibili_text("ComfyUI 商业变现实战课，扫码加群领取资料"))
        self.assertTrue(
            is_low_value_bilibili_text(
                "更多资料评论区领取，等不急的点击：https://www.bilibili.com/opus/1174368758826795045"
            )
        )
        self.assertTrue(is_low_value_bilibili_text("需要配套素材的同学评论区留言666记得领取"))
        self.assertTrue(is_low_value_bilibili_text("公众号 AOV视觉设计，扫视频开头的码了解更多免费课程和低价好课"))
        self.assertTrue(is_low_value_bilibili_text("【秋叶 ComfyUI 教程】抢先下载！超强 comfyui 工作流，AI模型，AI图片生成AI视频生成，创作神器"))
        self.assertTrue(is_low_value_bilibili_text("【 ComfyUI 教程】6月最新！ Wan 2.2图生视频 + 文生视频本地部署工作流详细教程！（附工作流）"))
        self.assertTrue(is_low_value_bilibili_text("【 ComfyUI 】LTX 导演台完整闭环全讲透！学到就是赚到！RunningHub 新人福利码"))
        self.assertTrue(is_low_value_bilibili_text("不会写提示词？GPT Image 2 + LTX 2.3 全自动六宫格导演台电影级短片直出，提供远程部署与技术支持"))
        self.assertTrue(is_low_value_bilibili_text("AI 自动化 工作流 构建 - Stable Diffusion / ComfyUI / 自动化流程。本期视频分享实践经验，你将看到流程搭建。"))
        self.assertTrue(is_low_value_bilibili_text("Pixaroma 教程 第20集，下载链接 工作流 https://example.com"))
        self.assertTrue(is_low_value_bilibili_text("每天半小时AI知识 | 11万星！ComfyUI 让AI绘画变成搭积木，节点式工作流"))
        self.assertTrue(is_low_value_bilibili_text("ComfyUI 教程 第20集：Pause Image 节点及其他 Pixaroma 更新，你将看到安装和配置工作流"))
        self.assertTrue(is_low_value_bilibili_text("ComfyUI 文心模型实测：值得入手吗？+ 新节点 使用指南（Ep14）"))
        self.assertTrue(is_low_value_bilibili_text("GPT-image2 节点更新，API注册地址和工作流资料免费分享，进交流群领取"))
        self.assertTrue(is_low_value_bilibili_text("10系老N卡优化版本 ComfyUI 分享文件，网盘链接和提取码自取"))
        self.assertFalse(is_low_value_bilibili_text("ComfyUI 教程：Qwen 节点更新与 Wan 图生视频工作流部署，新增模型节点适配"))
        self.assertFalse(is_low_value_bilibili_text("LongCat-Video-Avatar 1.5 全解析：模型版本更新，ComfyUI 工作流适配"))
        self.assertTrue(
            is_low_value_bilibili_text(
                "LTX 2.3 vs Wan 2.2，带你从安装到实战，环境配置，工作流概览，是否值得上手"
            )
        )
        self.assertTrue(is_low_value_social_text("ComfyUI workflow pack, comment 666 to get the files"))
        self.assertTrue(is_low_value_social_text("ComfyUI Flux masterclass: join my paid course"))
        self.assertTrue(
            is_low_value_social_text(
                "MY WEB UI USING COMFY UI BACK WITH PRE MADE WORKFLOWS just run.bat and start cooking"
            )
        )
        self.assertTrue(is_low_value_social_text("AI-Toolkit Ostris help with LoRA training"))
        self.assertTrue(is_low_value_social_text("Can I use a novel model on ComfyUI? Thanks - Dave"))
        self.assertTrue(is_low_value_social_text("Update problem after update ComfyUI portable"))
        self.assertTrue(is_low_value_social_text("how do I add motion to these static parts of the video"))
        self.assertTrue(is_low_value_social_text("Tried some 17MP ideogram 4 images for fun"))
        self.assertTrue(is_low_value_social_text("ComfyUI workflow where characters show middle finger"))
        self.assertTrue(is_low_value_bilibili_text("ComfyUI 实操159:让动物和其他角色竖中指 工作流"))
        self.assertTrue(is_low_value_social_text("记录 comfyui 搭配腾讯本地混元大模型，整个过程还挺有意思，记录一下"))
        self.assertTrue(is_low_value_social_text("ComfyUI 工作流训练营，扫码加群领取付费课程优惠"))
        self.assertTrue(
            is_low_value_t2_item(
                "Civitai model: Grimoire - Prompt Builder with AI Generation for SD / ComfyUI",
                "",
                civitai,
                {},
            )
        )
        self.assertTrue(
            is_low_value_t2_item(
                "ComfyUI 小白入门：Flux 工作流保姆级教程",
                "扫码加群领取系统课资料。",
                bilibili,
                {},
            )
        )
        self.assertTrue(
            is_low_value_t2_item(
                "ComfyUI Flux beginner tutorial",
                "Join my course to learn workflows from zero.",
                x_source,
                {},
            )
        )
        self.assertTrue(
            is_low_value_t2_item(
                "ComfyUI Wan 视频模型零基础系统课",
                "付费社群陪跑，入门到精通。",
                youtube,
                {},
            )
        )
        self.assertTrue(
            is_low_value_t2_item(
                "ComfyUI -Bernini导演台重磅升级：视频长度不设限",
                "ComfyUI-bernini插件地址，管理大师 invite_code，夸克 pan.quark.cn，百度 pan.baidu.com，软件下载地址",
                bilibili,
                {},
            )
        )
        self.assertTrue(
            is_low_value_t2_item(
                "ComfyUI daily casual demo",
                "Just recording a quick test.",
                bilibili,
                {"content_understanding": {"summary": "Just recording a quick test with ComfyUI."}},
            )
        )
        self.assertFalse(
            is_low_value_t2_item(
                "Interesting thing to do with Ideogram-4 using QwenVL and an image.",
                "ComfyUI workflow notes.",
                bilibili,
                {
                    "content_understanding": {
                        "subtitle_text": "Ideogram-4 image output is checked with QwenVL inside a ComfyUI workflow.",
                        "terms": ["Ideogram", "QwenVL", "ComfyUI", "workflow"],
                    }
                },
            )
        )

        item = build_item(
            source=bilibili,
            title="ComfyUI 教程：Qwen 节点更新与 Wan 图生视频工作流部署",
            summary="新增 Qwen 模型节点适配，支持 Wan 视频模型工作流和低显存量化配置。",
            url="https://www.bilibili.com/video/BV456",
            published_at=None,
            keywords={"include": ["comfyui"], "exclude": []},
        )
        self.assertIsNotNone(item)
        assert item is not None
        self.assertTrue(item.featured)

    def test_social_trusted_author_and_engagement_boost_score(self) -> None:
        tags = ["community", "model", "workflow"]
        plain = score_breakdown(
            title="ComfyUI Flux workflow",
            summary="Flux LoRA workflow notes with GGUF nodes.",
            source_weight=2,
            source_type="x_search",
            source_tier="T2",
            tags=tags,
        )
        boosted = score_breakdown(
            title="ComfyUI Flux workflow",
            summary="Flux LoRA workflow notes with GGUF nodes.",
            source_weight=2,
            source_type="x_search",
            source_tier="T2",
            tags=tags,
            interaction_count=800,
            trusted_author=True,
        )

        self.assertGreater(boosted["authority"], plain["authority"])
        self.assertGreater(boosted["popularity"], plain["popularity"])

    def test_bilibili_soft_content_is_capped_below_release_news(self) -> None:
        tags = ["community", "model", "workflow", "video"]
        direct_news = score_breakdown(
            title="TripoSplat 现已在 ComfyUI 原生支持：从单图生成 3D 高斯泼溅模型",
            summary="模型已在 ComfyUI 支持，新增节点和工作流。",
            source_weight=2,
            source_type="bilibili_search",
            source_tier="T2",
            tags=tags,
            interaction_count=400,
        )
        tutorial = score_breakdown(
            title="qwen-image-2512 出图教程，lora 与 turbo 加速",
            summary="基础模型地址：https://huggingface.co/Comfy-Org",
            source_weight=2,
            source_type="bilibili_search",
            source_tier="T2",
            tags=tags,
            interaction_count=20,
        )
        node_update_explainer = score_breakdown(
            title="ComfyUI 教程：Qwen 节点更新与 Wan 图生视频工作流部署",
            summary="新增 Qwen 模型节点适配，支持 Wan 视频模型工作流和低显存量化配置。",
            source_weight=2,
            source_type="bilibili_search",
            source_tier="T2",
            tags=tags,
            interaction_count=120,
        )
        workflow_showcase = score_breakdown(
            title="Flux 2 画质优化高清细节 LoRA 无损高清放大工作流",
            summary="ComfyUI 工作流展示，包含高清放大和 LoRA 效果。",
            source_weight=2,
            source_type="bilibili_search",
            source_tier="T2",
            tags=tags,
            interaction_count=400,
        )

        self.assertIsNone(bilibili_score_cap("TripoSplat 现已在 ComfyUI 原生支持：从单图生成 3D 高斯泼溅模型"))
        self.assertLessEqual(sum(tutorial.values()), 56)
        self.assertLessEqual(sum(node_update_explainer.values()), 76)
        # Adopted optimization workflows for current models now count as creator
        # deep-dives (capped at 86) instead of generic showcases.
        self.assertLessEqual(sum(workflow_showcase.values()), 86)
        self.assertGreater(sum(direct_news.values()), sum(tutorial.values()))


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

    def test_model_sources_build_featured_items(self) -> None:
        source = Source(
            id="huggingface-comfyui-models",
            name="Hugging Face ComfyUI model search",
            type="huggingface_models",
            url="local://huggingface-models?q=Flux",
            category="models",
            weight=3,
            tier="T2",
        )
        item = build_item(
            source=source,
            title="Hugging Face model: author/flux-comfyui-lora",
            summary="ComfyUI Flux LoRA safetensors model weights for image generation.",
            url="https://huggingface.co/author/flux-comfyui-lora",
            published_at=None,
            keywords={"include": ["comfyui", "flux", "lora"], "exclude": []},
            raw={"engagement": {"weighted": 500}},
        )

        self.assertIsNotNone(item)
        assert item is not None
        self.assertTrue(item.featured)
        self.assertEqual(item.cluster_key, "hf:author/flux-comfyui-lora")

    def test_broad_model_discovery_can_feature_unknown_model_names(self) -> None:
        source = Source(
            id="huggingface-open-model-discovery",
            name="Hugging Face open model discovery",
            type="huggingface_models",
            url="local://huggingface-models?q=text-to-image|image-to-video|diffusers",
            category="models",
            weight=2,
            tier="T2",
        )
        item = build_item(
            source=source,
            title="Hugging Face model: lab/aether-image-1.0",
            summary="Pipeline: text-to-image. Tags: diffusers, safetensors, diffusion model. License: apache-2.0.",
            url="https://huggingface.co/lab/aether-image-1.0",
            published_at=None,
            keywords={"include": ["comfyui", "diffusers", "text-to-image", "safetensors"], "exclude": []},
            raw={"modelId": "lab/aether-image-1.0", "downloads": 120, "likes": 8, "engagement": {"weighted": 400}},
        )

        self.assertIsNotNone(item)
        assert item is not None
        self.assertTrue(item.featured)
        self.assertIn("model", item.tags)

        unrelated = build_item(
            source=source,
            title="Hugging Face model: forkjoin-ai/falcon-mamba-7b-safetensors",
            summary="Pipeline: text-generation. Tags: safetensors, mamba, language model.",
            url="https://huggingface.co/forkjoin-ai/falcon-mamba-7b-safetensors",
            published_at=None,
            keywords={"include": ["comfyui", "diffusers", "text-to-image", "safetensors"], "exclude": []},
            raw={"engagement": {"weighted": 400}},
        )

        self.assertIsNone(unrelated)

        zero_engagement_mirror = build_item(
            source=source,
            title="Hugging Face model: mirror/Wan2.2-T2V-A14B-Diffusers",
            summary="Tags: diffusers, safetensors. Downloads: 0. Likes: 0.",
            url="https://huggingface.co/mirror/Wan2.2-T2V-A14B-Diffusers",
            published_at=None,
            keywords={"include": ["comfyui", "diffusers", "text-to-image", "safetensors"], "exclude": []},
            raw={"modelId": "mirror/Wan2.2-T2V-A14B-Diffusers", "downloads": 0, "likes": 0},
        )

        self.assertIsNone(zero_engagement_mirror)

    def test_civitai_open_discovery_filters_character_lora_noise(self) -> None:
        source = Source(
            id="civitai-open-model-discovery",
            name="Civitai open model discovery",
            type="civitai_models",
            url="local://civitai-models?q=checkpoint|video|sdxl",
            category="models",
            weight=2,
            tier="T2",
        )
        item = build_item(
            source=source,
            title="Civitai model: LoRA / Illustrious - NoobAI Dusk from Arknights Cosplay Character",
            summary="Join my Discord server. Compatible with Pony and other LoRA. Recommended Weight: 0.8.",
            url="https://civitai.com/models/123",
            published_at=None,
            keywords={"include": ["lora", "sdxl"], "exclude": []},
            raw={"engagement": {"weighted": 1000}},
        )

        self.assertIsNone(item)

        style_pack = build_item(
            source=source,
            title="Civitai model: Style DiT2 Shiny Metal SDXL",
            summary="A style pack for SDXL image generation.",
            url="https://civitai.com/models/456",
            published_at=None,
            keywords={"include": ["sdxl"], "exclude": []},
            raw={"engagement": {"weighted": 1000}},
        )
        character_pack = build_item(
            source=source,
            title="Civitai model: Character 03 SDXL",
            summary="A character resource for SDXL.",
            url="https://civitai.com/models/789",
            published_at=None,
            keywords={"include": ["sdxl"], "exclude": []},
            raw={"engagement": {"weighted": 1000}},
        )

        self.assertIsNone(style_pack)
        self.assertIsNone(character_pack)


if __name__ == "__main__":
    unittest.main()
