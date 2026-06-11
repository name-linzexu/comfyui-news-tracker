from __future__ import annotations

import asyncio
import json
import time
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

import httpx

from app.llm_triage import apply_triage_result, fetch_triage_result, normalize_triage_result
from app.models import NewsItem
from app.scoring import apply_llm_triage
from app.settings import settings
from app.sources import Fetcher, Source, should_skip_bilibili_enrichment
from app.storage import Storage, utc_now


def make_item(
    guid: str,
    title: str,
    *,
    score: int = 50,
    featured: bool = False,
    published_at: datetime | None = None,
    source_type: str = "rss",
    category: str = "official",
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
        source_tier="T1",
        reason="primary source",
        score_breakdown={"source": score},
        cluster_key=f"cluster-{guid}",
        cluster_title=title,
        author="tester",
        raw=raw,
    )


async def instant_sleep(_delay: float) -> None:
    return None


def run_fetch(handler, url: str, **kwargs):
    async def run():
        fetcher = Fetcher(transport=httpx.MockTransport(handler))
        try:
            with mock.patch("app.sources.asyncio.sleep", instant_sleep):
                return await fetcher._get(url, **kwargs)
        finally:
            await fetcher.close()

    return asyncio.run(run())


class FetcherRetryTests(unittest.TestCase):
    def test_get_retries_transient_status_then_succeeds(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(408)
            return httpx.Response(200, json={"ok": True})

        response = run_fetch(handler, "https://example.com/feed")

        self.assertEqual(response.json(), {"ok": True})
        self.assertEqual(calls["n"], 2)

    def test_get_retries_transport_error_then_succeeds(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ConnectError("boom")
            return httpx.Response(200, json={"ok": True})

        response = run_fetch(handler, "https://example.com/feed")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls["n"], 2)

    def test_get_raises_after_exhausting_retries(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(503)

        with self.assertRaises(httpx.HTTPStatusError):
            run_fetch(handler, "https://example.com/feed")
        self.assertEqual(calls["n"], max(1, settings.http_retry_attempts))

    def test_github_rate_limit_is_retried(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(
                    403,
                    headers={
                        "x-ratelimit-remaining": "0",
                        "x-ratelimit-reset": str(int(time.time()) + 1),
                    },
                    text="API rate limit exceeded",
                )
            return httpx.Response(200, json={"items": []})

        response = run_fetch(handler, "https://api.github.com/search/repositories?q=comfyui")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(calls["n"], 2)

    def test_get_returns_ok_statuses_without_raising(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404)

        response = run_fetch(handler, "https://example.com/feed", ok_statuses={404})

        self.assertEqual(response.status_code, 404)
        self.assertEqual(calls["n"], 1)


class ApplyLlmTriageTests(unittest.TestCase):
    def test_reject_clamps_score_and_featured(self) -> None:
        score, featured, reason, cluster_key, cluster_title = apply_llm_triage(
            score=88,
            featured=True,
            reason="primary source",
            cluster_key="github:a/b",
            cluster_title="title",
            triage={"decision": "reject", "reason": "卖课宣传", "content_type": "course_marketing"},
        )
        self.assertLessEqual(score, 20)
        self.assertFalse(featured)
        self.assertIn("LLM triage rejected", reason)
        self.assertEqual(cluster_key, "github:a/b")

    def test_downgrade_clamps_score(self) -> None:
        score, featured, reason, _, _ = apply_llm_triage(
            score=75,
            featured=True,
            reason="",
            cluster_key="",
            cluster_title="",
            triage={"decision": "downgrade", "reason": "纯展示", "content_type": "showcase"},
        )
        self.assertLessEqual(score, 48)
        self.assertFalse(featured)
        self.assertIn("LLM triage downgraded", reason)

    def test_keep_boosts_and_is_idempotent(self) -> None:
        triage = {
            "decision": "keep",
            "importance": 85,
            "confidence": 80,
            "reason": "官方发布",
            "cluster_key": "model:flux-2",
            "zh_title": "Flux 2 发布",
        }
        score, featured, reason, cluster_key, cluster_title = apply_llm_triage(
            score=70,
            featured=False,
            reason="primary source",
            cluster_key="github:x/y",
            cluster_title="t",
            triage=triage,
        )
        self.assertEqual(score, 85)
        self.assertTrue(featured)
        self.assertIn("LLM triage kept", reason)
        self.assertEqual(cluster_key, "model:flux-2")
        self.assertEqual(cluster_title, "Flux 2 发布")

        score2, featured2, reason2, cluster_key2, cluster_title2 = apply_llm_triage(
            score=score,
            featured=featured,
            reason=reason,
            cluster_key=cluster_key,
            cluster_title=cluster_title,
            triage=triage,
        )
        self.assertEqual((score2, featured2, reason2), (score, featured, reason))
        self.assertEqual((cluster_key2, cluster_title2), (cluster_key, cluster_title))

    def test_unknown_decision_changes_nothing(self) -> None:
        result = apply_llm_triage(
            score=60,
            featured=True,
            reason="r",
            cluster_key="k",
            cluster_title="t",
            triage={"decision": "??"},
        )
        self.assertEqual(result, (60, True, "r", "k", "t"))


class TriagePersistenceTests(unittest.TestCase):
    def test_upsert_preserves_llm_triage_and_keeps_clamp(self) -> None:
        with TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            storage.upsert_items([make_item("triage-p", "ComfyUI Flux course", score=88, featured=True)])
            row = storage.list_items(limit=1, featured=None, include_raw=True)[0]
            result = normalize_triage_result(
                {
                    "decision": "reject",
                    "content_type": "course_marketing",
                    "importance": 10,
                    "confidence": 92,
                    "reason": "卖课宣传",
                }
            )
            apply_triage_result(storage, row, result)

            # Simulate the next refresh re-fetching the same item with a fresh high keyword score.
            upsert = storage.upsert_items([make_item("triage-p", "ComfyUI Flux course", score=88, featured=True)])
            updated = storage.list_items(limit=1, featured=None, include_raw=True)[0]

            self.assertLessEqual(updated["score"], 20)
            self.assertFalse(updated["featured"])
            self.assertIn("LLM triage rejected", updated["reason"])
            self.assertEqual(updated["raw"]["llm_triage"]["content_type"], "course_marketing")
            self.assertEqual(upsert.unchanged, 1)

    def test_rescore_keeps_triage_clamp(self) -> None:
        with TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            storage.upsert_items([make_item("triage-r", "ComfyUI Flux course", score=88, featured=True)])
            row = storage.list_items(limit=1, featured=None, include_raw=True)[0]
            apply_triage_result(
                storage,
                row,
                normalize_triage_result(
                    {
                        "decision": "reject",
                        "content_type": "course_marketing",
                        "importance": 10,
                        "confidence": 92,
                        "reason": "卖课宣传",
                    }
                ),
            )

            source = Source(
                id="test-source",
                name="Test Source",
                type="rss",
                url="https://example.com/feed",
                category="official",
                weight=5,
                tier="T1",
            )
            storage.rescore_items({"test-source": source}, {"include": ["comfyui"], "exclude": []})
            updated = storage.list_items(limit=1, featured=None, include_raw=True)[0]

            self.assertLessEqual(updated["score"], 20)
            self.assertFalse(updated["featured"])
            self.assertIn("llm_triage", updated["raw"])


class RescoreWindowTests(unittest.TestCase):
    def test_rescore_with_since_only_touches_recent_rows(self) -> None:
        with TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            now = utc_now()
            storage.upsert_items(
                [
                    make_item("old", "ComfyUI workflow update old", score=1, published_at=now - timedelta(days=300)),
                    make_item("recent", "ComfyUI workflow update recent", score=1, published_at=now - timedelta(days=1)),
                ]
            )
            source = Source(
                id="test-source",
                name="Test Source",
                type="rss",
                url="https://example.com/feed",
                category="official",
                weight=5,
                tier="T1",
            )
            keywords = {"include": ["comfyui"], "exclude": []}

            changed = storage.rescore_items({"test-source": source}, keywords, since=now - timedelta(days=14))
            rows = {row["guid"]: row for row in storage.list_items(limit=10, featured=None)}

            self.assertGreaterEqual(changed, 1)
            self.assertEqual(rows["old"]["score"], 1)
            self.assertNotEqual(rows["recent"]["score"], 1)


class BilibiliSkipTests(unittest.TestCase):
    def test_should_skip_bilibili_enrichment(self) -> None:
        known = {"https://www.bilibili.com/video/BV1known"}
        old_ts = int(time.time()) - 10 * 86400
        fresh_ts = int(time.time()) - 3600

        self.assertTrue(should_skip_bilibili_enrichment("https://www.bilibili.com/video/BV1known", old_ts, known))
        self.assertFalse(should_skip_bilibili_enrichment("https://www.bilibili.com/video/BV1known", fresh_ts, known))
        self.assertFalse(should_skip_bilibili_enrichment("https://www.bilibili.com/video/BV2new", old_ts, known))
        self.assertTrue(should_skip_bilibili_enrichment("https://www.bilibili.com/video/BV1known", None, known))

    def test_enriched_bilibili_urls_only_returns_enriched(self) -> None:
        with TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            storage.upsert_items(
                [
                    make_item(
                        "b-enriched",
                        "ComfyUI 视频模型",
                        source_type="bilibili_search",
                        raw={"content_understanding": {"summary": "总结"}},
                    ),
                    make_item("b-plain", "ComfyUI 节点", source_type="bilibili_search"),
                    make_item("not-bili", "ComfyUI release", source_type="rss", raw={"content_understanding": {}}),
                ]
            )

            urls = storage.enriched_bilibili_urls()

            self.assertEqual(urls, {"https://example.com/b-enriched"})


class FtsSkipTests(unittest.TestCase):
    def test_search_still_matches_after_unchanged_upsert(self) -> None:
        with TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            storage.upsert_items([make_item("f1", "ComfyUI Flux loader")])
            second = storage.upsert_items([make_item("f1", "ComfyUI Flux loader")])

            rows = storage.list_items(limit=10, featured=None, query="flux")

            self.assertEqual(second.unchanged, 1)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["guid"], "f1")


class FetchTriageResultTests(unittest.TestCase):
    def _client(self, handler) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=5)

    def test_returns_none_on_persistent_transport_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom")

        with self._client(handler) as client, mock.patch("app.llm_triage.time.sleep"):
            result = fetch_triage_result(client, {"title": "x"})

        self.assertIsNone(result)

    def test_retries_429_then_succeeds(self) -> None:
        calls = {"n": 0}
        content = json.dumps(
            {
                "decision": "keep",
                "content_type": "model_release",
                "importance": 90,
                "confidence": 90,
                "reason": "官方模型发布",
                "zh_title": "",
                "zh_summary": "",
                "cluster_key": "",
                "signals": [],
            }
        )
        body = {"choices": [{"message": {"content": content}}]}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(429, headers={"retry-after": "0"})
            return httpx.Response(200, json=body)

        with self._client(handler) as client, mock.patch("app.llm_triage.time.sleep"):
            result = fetch_triage_result(client, {"title": "x"})

        self.assertIsNotNone(result)
        self.assertEqual(result["decision"], "keep")
        self.assertEqual(calls["n"], 2)

    def test_returns_none_on_malformed_payload(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})

        with self._client(handler) as client:
            result = fetch_triage_result(client, {"title": "x"})

        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
