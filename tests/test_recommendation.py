from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from app import vocab
from app.llm_triage import triage_priority
from app.models import NewsItem
from app.scoring import (
    engagement_velocity_points,
    follower_authority_points,
    score_breakdown,
    score_item,
)
from app.sources import author_followers_from_raw, x_author_followers
from app.storage import Storage, featured_channel_for, featured_quotas


def make_item(
    guid: str,
    title: str,
    *,
    score: int = 80,
    featured: bool = True,
    published_at: datetime | None = None,
    source_type: str = "rss",
    category: str = "official",
    tier: str = "T1",
    cluster_key: str | None = None,
    tags: list[str] | None = None,
    raw: dict[str, Any] | None = None,
) -> NewsItem:
    now = published_at or datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    return NewsItem(
        guid=guid,
        source_id="test-source",
        source_name="Test Source",
        source_type=source_type,
        category=category,
        title=title,
        summary="ComfyUI workflow update",
        url=f"https://example.com/{guid}",
        published_at=now,
        fetched_at=now,
        score=score,
        featured=featured,
        tags=tags or ["official", "workflow"],
        source_tier=tier,
        reason="primary source",
        score_breakdown={"source": score},
        cluster_key=cluster_key if cluster_key is not None else f"cluster-{guid}",
        cluster_title=title,
        author="tester",
        raw=raw,
    )


class VocabTests(unittest.TestCase):
    def test_family_terms_and_orgs(self) -> None:
        self.assertIn("flux", vocab.MODEL_FAMILY_TERMS)
        self.assertIn("wan", vocab.MODEL_FAMILY_TERMS)
        self.assertTrue(vocab.is_official_model_org("ByteDance"))
        self.assertTrue(vocab.is_official_model_org("black-forest-labs"))
        self.assertTrue(vocab.is_trusted_converter("Kijai"))
        self.assertFalse(vocab.is_official_model_org("random-user-123"))

    def test_family_regex_keeps_version_in_cluster_match(self) -> None:
        match = vocab.MODEL_FAMILY_RE.search("comfyui flux 2 release notes")
        self.assertIsNotNone(match)
        assert match is not None
        self.assertEqual(match.group(1).lower(), "flux 2")
        self.assertIsNone(vocab.MODEL_FAMILY_RE.search("i want to test nothing"))


class CommitClassificationTests(unittest.TestCase):
    def _score(self, title: str, *, source_id: str | None) -> int:
        return score_item(
            title=title,
            summary=title,
            source_weight=5,
            source_type="rss",
            source_tier="T1",
            tags=["official", "model", "performance"],
            source_id=source_id,
        )

    def test_subsystem_commit_prefix_is_capped_on_commit_feeds(self) -> None:
        title = "mm: dont reset cast buffers in cleanup_models_gc() (#14372)"
        self.assertLessEqual(self._score(title, source_id="comfyui-commits-atom"), 62)

    def test_unknown_prefix_not_capped_outside_commit_feeds(self) -> None:
        title = "ComfyUI: new official model release announcement for Flux video workflow support"
        self.assertGreater(self._score(title, source_id="comfyui-blog"), 62)

    def test_feat_commits_are_not_capped(self) -> None:
        title = "feat: Add model support for SCAIL-2 (#14373)"
        self.assertGreater(self._score(title, source_id="comfyui-commits-atom"), 62)

    def test_bracket_tag_before_feat_is_ignored(self) -> None:
        title = "[Partner Nodes] feat: add Krea 2 Medium Turbo model (#14280)"
        self.assertGreater(self._score(title, source_id="comfyui-commits-atom"), 62)


class ModelPlatformAuthorTests(unittest.TestCase):
    def _breakdown(self, title: str, *, author: str | None, interaction: int | None = None) -> dict[str, int]:
        return score_breakdown(
            title=title,
            summary="Pipeline: image-to-video. Tags: diffusers, safetensors.",
            source_weight=3,
            source_type="huggingface_models",
            source_tier="T2",
            tags=["models", "model", "video"],
            interaction_count=interaction,
            author=author,
        )

    def test_official_org_outranks_unknown_reupload(self) -> None:
        official = self._breakdown("Hugging Face model: ByteDance/Bernini-R-1.3B-Diffusers", author="ByteDance")
        reupload = self._breakdown("Hugging Face model: Lunael/Wan2.2-I2V-A14B-Diffusers", author="Lunael")

        self.assertGreater(official["authority"], reupload["authority"])
        self.assertGreater(sum(official.values()), sum(reupload.values()))
        self.assertLessEqual(sum(reupload.values()), 58)

    def test_adopted_reupload_can_score_higher(self) -> None:
        adopted = self._breakdown(
            "Hugging Face model: QuantStack2/Wan2.2-GGUF",
            author="QuantStack2",
            interaction=900,
        )
        self.assertLessEqual(sum(adopted.values()), 72)
        self.assertGreater(sum(adopted.values()), 58)

    def test_trusted_converter_is_not_capped(self) -> None:
        trusted = self._breakdown("Hugging Face model: Kijai/Wan2.2-GGUF", author="Kijai")
        self.assertGreaterEqual(trusted["authority"], 8)
        self.assertGreater(sum(trusted.values()), 72)


class NoiseCapTests(unittest.TestCase):
    def test_civitai_character_lora_is_capped(self) -> None:
        score = score_item(
            title="Civitai model: Illustrious Anime Character Generator | ComfyUI Workflow",
            summary="Generate anime characters with this workflow.",
            source_weight=3,
            source_type="civitai_models",
            source_tier="T2",
            tags=["models", "model", "workflow"],
            interaction_count=900,
        )
        self.assertLessEqual(score, 56)

    def test_civitai_real_model_not_capped(self) -> None:
        score = score_item(
            title="Civitai model: Wan 2.2 motion checkpoint",
            summary="New Wan 2.2 checkpoint for video generation in ComfyUI.",
            source_weight=3,
            source_type="civitai_models",
            source_tier="T2",
            tags=["models", "model", "video"],
            interaction_count=900,
        )
        self.assertGreater(score, 56)

    def test_community_question_is_capped(self) -> None:
        score = score_item(
            title="Best local text to audio for movies",
            summary="Looking for recommendations for audio models.",
            source_weight=2,
            source_type="rss",
            source_tier="T2",
            tags=["community"],
        )
        self.assertLessEqual(score, 56)

    def test_community_strong_news_keeps_higher_cap(self) -> None:
        score = score_item(
            title="Wan 2.2 GGUF released with ComfyUI workflow update",
            summary="New quantized weights released, supports low VRAM nodes.",
            source_weight=2,
            source_type="rss",
            source_tier="T2",
            tags=["community", "model", "quantization", "workflow"],
        )
        self.assertGreater(score, 56)
        self.assertLessEqual(score, 88)


class FeaturedSelectorTests(unittest.TestCase):
    def test_cluster_dedup_keeps_best_item_per_day(self) -> None:
        with TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            storage.upsert_items(
                [
                    make_item("a", "Flux 2 release official", score=95, cluster_key="model:flux-2"),
                    make_item("b", "Flux 2 release repost", score=80, cluster_key="model:flux-2", tier="T2"),
                    make_item("c", "Other news", score=70, cluster_key="model:other"),
                ]
            )

            result = storage.select_featured()
            rows = {row["guid"]: row for row in storage.list_items(limit=10, featured=None)}

            self.assertTrue(rows["a"]["featured"])
            self.assertFalse(rows["b"]["featured"])
            self.assertTrue(rows["c"]["featured"])
            self.assertTrue(rows["b"]["featured_candidate"])
            self.assertEqual(result["demoted_duplicates"], 1)

    def test_channel_quota_limits_noisy_channels(self) -> None:
        with TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            items = [
                make_item(
                    f"x{i}",
                    f"ComfyUI X post {i}",
                    score=60 + i,
                    source_type="x_search",
                    category="community",
                    tier="T2",
                    cluster_key=f"text:x-{i}",
                )
                for i in range(8)
            ]
            storage.upsert_items(items)

            result = storage.select_featured()
            rows = storage.list_items(limit=20, featured=True)

            self.assertEqual(len(rows), 6)
            self.assertEqual(result["demoted_quota"], 2)
            kept_scores = sorted(row["score"] for row in rows)
            self.assertEqual(kept_scores, [62, 63, 64, 65, 66, 67])

    def test_non_candidates_lose_featured_flag(self) -> None:
        with TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            storage.upsert_items([make_item("stale", "Old item", featured=False)])
            with storage.connection() as conn:
                conn.execute("UPDATE items SET featured = 1 WHERE guid = 'stale'")

            storage.select_featured()
            row = storage.list_items(limit=5, featured=None)[0]

            self.assertFalse(row["featured"])
            self.assertFalse(row["featured_candidate"])

    def test_channel_mapping_and_quota_parsing(self) -> None:
        self.assertEqual(featured_channel_for("x_search", "community"), "x")
        self.assertEqual(featured_channel_for("huggingface_models", "models"), "models")
        self.assertEqual(featured_channel_for("github_releases", "official"), "github")
        self.assertEqual(featured_channel_for("rss", "official"), "core")
        self.assertEqual(featured_channel_for("rss", "community"), "community")
        quotas = featured_quotas()
        self.assertEqual(quotas["x"], 6)
        self.assertNotIn("core", quotas)


class EngagementSignalTests(unittest.TestCase):
    def test_velocity_rewards_fresh_viral_items(self) -> None:
        now = datetime.now(UTC)
        fresh = engagement_velocity_points(5000, now - timedelta(days=1))
        stale = engagement_velocity_points(5000, now - timedelta(days=30))

        self.assertGreater(fresh, stale)
        self.assertEqual(engagement_velocity_points(0, now), 0)
        self.assertEqual(engagement_velocity_points(None, now), 0)
        self.assertLessEqual(engagement_velocity_points(10_000_000, now), 28)

    def test_follower_authority_log_curve(self) -> None:
        self.assertEqual(follower_authority_points(None), 0)
        self.assertEqual(follower_authority_points(50), 0)
        self.assertEqual(follower_authority_points(1000), 4)
        self.assertEqual(follower_authority_points(100_000), 12)
        self.assertEqual(follower_authority_points(50_000_000), 16)

    def test_follower_authority_enters_breakdown(self) -> None:
        kwargs = dict(
            title="ComfyUI Flux workflow update",
            summary="Flux LoRA workflow notes with GGUF nodes.",
            source_weight=2,
            source_type="x_search",
            source_tier="T2",
            tags=["community", "model", "workflow"],
        )
        base = score_breakdown(**kwargs)
        boosted = score_breakdown(**kwargs, author_followers=120_000)

        self.assertGreater(boosted["authority"], base["authority"])

    def test_x_author_followers_extraction(self) -> None:
        self.assertEqual(x_author_followers({"public_metrics": {"followers_count": 4321}}), 4321)
        self.assertEqual(x_author_followers({"public_metrics": {}}), 0)
        self.assertEqual(x_author_followers({}), 0)
        self.assertEqual(x_author_followers(None), 0)

    def test_followers_survive_rescore_via_raw(self) -> None:
        self.assertEqual(author_followers_from_raw({"engagement": {"author_followers": 9000}}), 9000)
        self.assertIsNone(author_followers_from_raw({"engagement": {}}))
        self.assertIsNone(author_followers_from_raw(None))


class TriagePriorityTests(unittest.TestCase):
    def test_gray_band_noisy_rows_lead(self) -> None:
        gray_noisy = {"score": 64, "source_type": "bilibili_search", "featured_candidate": False}
        gray_quiet = {"score": 64, "source_type": "rss", "featured_candidate": False}
        high_noisy_candidate = {"score": 92, "source_type": "x_search", "featured_candidate": True}
        plain = {"score": 95, "source_type": "rss", "featured_candidate": False}

        ordered = sorted([plain, high_noisy_candidate, gray_quiet, gray_noisy], key=triage_priority)

        self.assertIs(ordered[0], gray_noisy)
        self.assertIs(ordered[1], gray_quiet)
        self.assertIs(ordered[2], high_noisy_candidate)
        self.assertIs(ordered[3], plain)


if __name__ == "__main__":
    unittest.main()
