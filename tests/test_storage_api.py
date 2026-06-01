from __future__ import annotations

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

from fastapi.testclient import TestClient

from app.collector import (
    filter_sources,
    is_loopback_webhook,
    notify_webhook,
    partition_sources,
    should_skip_x_source_without_bearer,
)
import app.main as main_module
from app.digest import render_markdown_digest, webhook_payload
from app.main import app
from app.models import NewsItem
from app.settings import settings
from app.sources import bilibili_search_terms, clean_x_text, load_sources, parse_x_author, tweet_body, x_browser_query
from app.storage import Storage, normalize_fts_query


def make_item(
    guid: str,
    title: str,
    *,
    score: int = 50,
    featured: bool = False,
    published_at: datetime | None = None,
    source_type: str = "rss",
    category: str = "official",
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
        tags=["official", "workflow"],
        source_tier="T1",
        reason="primary source",
        score_breakdown={"source": score},
        cluster_key=f"cluster-{guid}",
        cluster_title=title,
        author="tester",
    )


class StorageTests(unittest.TestCase):
    def test_upsert_counts_insert_update_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            first = storage.upsert_items([make_item("a", "First")])
            self.assertEqual((first.inserted, first.updated, first.unchanged), (1, 0, 0))

            same = storage.upsert_items([make_item("a", "First")])
            self.assertEqual((same.inserted, same.updated, same.unchanged), (0, 0, 1))

            changed = storage.upsert_items([make_item("a", "First changed")])
            self.assertEqual((changed.inserted, changed.updated, changed.unchanged), (0, 1, 0))

    def test_sources_can_require_token_and_partition_skips_without_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "sources.yml"
            config.write_text(
                """
keywords:
  include: [comfyui]
  exclude: []
sources:
  - id: atom
    name: Atom
    type: rss
    url: https://example.com/feed.xml
    category: official
    tier: T1
    weight: 5
  - id: github-rest
    name: GitHub REST
    type: github_releases
    url: https://api.github.com/repos/example/repo/releases
    category: official
    tier: T1
    weight: 5
    requires_token: true
  - id: x-api
    name: X API
    type: x_search
    url: local://x-search?q=ComfyUI
    category: community
    tier: T2
    weight: 2
    requires_x_token: true
""",
                encoding="utf-8",
            )
            sources, _ = load_sources(config)
            previous_token = settings.github_token
            previous_x_token = settings.x_bearer_token
            previous_x_browser_search = settings.x_browser_search
            try:
                object.__setattr__(settings, "github_token", None)
                object.__setattr__(settings, "x_bearer_token", None)
                object.__setattr__(settings, "x_browser_search", "off")
                active, skipped = partition_sources(sources)
            finally:
                object.__setattr__(settings, "github_token", previous_token)
                object.__setattr__(settings, "x_bearer_token", previous_x_token)
                object.__setattr__(settings, "x_browser_search", previous_x_browser_search)

            self.assertEqual([source.id for source in active], ["atom"])
            self.assertEqual(skipped[0]["id"], "github-rest")
            self.assertEqual(skipped[0]["status"], "skipped")
            self.assertEqual(skipped[0]["reason"], "requires GITHUB_TOKEN")
            self.assertEqual(skipped[1]["id"], "x-api")
            self.assertEqual(skipped[1]["reason"], "requires X_BEARER_TOKEN or running X browser debug endpoint")

    def test_x_source_can_use_browser_debug_fallback_when_enabled(self) -> None:
        previous_x_token = settings.x_bearer_token
        previous_x_browser_search = settings.x_browser_search
        try:
            object.__setattr__(settings, "x_bearer_token", None)
            object.__setattr__(settings, "x_browser_search", "on")
            self.assertFalse(should_skip_x_source_without_bearer())
        finally:
            object.__setattr__(settings, "x_bearer_token", previous_x_token)
            object.__setattr__(settings, "x_browser_search", previous_x_browser_search)

    def test_filter_sources_skips_by_type_and_id(self) -> None:
        sources, _ = load_sources()
        selected, skipped = filter_sources(
            sources,
            include_types={"rss", "x_search"},
            exclude_types={"x_search"},
            skip_source_ids={"comfyui-blog"},
        )

        self.assertNotIn("x_search", {source.type for source in selected})
        self.assertNotIn("comfyui-blog", {source.id for source in selected})
        self.assertTrue(any(row["id"] == "comfyui-blog" for row in skipped))
        self.assertTrue(any(row["reason"] == "skipped by source type filter" for row in skipped))

    def test_list_items_score_sort_and_clusters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            storage.upsert_items(
                [
                    make_item("low", "Low", score=10),
                    make_item("high", "High", score=90, featured=True),
                ]
            )
            rows = storage.list_items(limit=2)
            self.assertEqual(rows[0]["title"], "High")
            self.assertEqual(rows[0]["score_breakdown"], {"source": 90})
            self.assertEqual(storage.count_items(), 2)
            self.assertEqual(storage.count_items(featured=True), 1)
            self.assertEqual(storage.count_items(channel="official"), 2)
            self.assertEqual(storage.count_items(channel="rss"), 2)

            clusters = storage.list_clusters(limit=5, featured=True)
            self.assertEqual(len(clusters), 1)
            self.assertEqual(clusters[0]["max_score"], 90)

    def test_x_and_bilibili_channels_can_be_filtered_independently(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            storage.upsert_items(
                [
                    make_item("x", "X model signal", source_type="x_search", category="community"),
                    make_item("bili", "Bilibili model signal", source_type="bilibili_search", category="community"),
                    make_item("rss", "RSS model signal"),
                ]
            )

            self.assertEqual(storage.count_items(channel="x"), 1)
            self.assertEqual(storage.count_items(channel="bilibili"), 1)
            self.assertEqual(storage.list_items(channel="x", limit=5)[0]["guid"], "x")
            self.assertEqual(storage.list_items(channel="bilibili", limit=5)[0]["guid"], "bili")

    def test_x_browser_text_helpers_match_article_shape(self) -> None:
        raw = "Author Name\n@handle\nTranslate post\n· 23m\nReplying to @someone and 2 others\nComfyUI Flux model release\nShow more"
        cleaned = clean_x_text(raw)

        self.assertEqual(parse_x_author(cleaned), ("Author Name", "@handle"))
        self.assertEqual(tweet_body(cleaned), "ComfyUI Flux model release")
        self.assertNotIn("Translate post", cleaned)
        self.assertIn("since:", x_browser_query("ComfyUI Flux"))

    def test_bilibili_search_terms_expand_news_topics(self) -> None:
        terms = bilibili_search_terms("ComfyUI 新模型 视频模型 节点 Flux Wan Qwen")
        self.assertIn("ComfyUI 模型发布", terms)
        self.assertIn("ComfyUI 节点适配", terms)
        self.assertIn("ComfyUI 低显存", terms)
        self.assertIn("ComfyUI 量化", terms)

    def test_old_social_channel_is_not_supported(self) -> None:
        client = TestClient(app)
        response = client.get("/api/items?channel=social")
        self.assertEqual(response.status_code, 422)

    def test_rescore_items_applies_current_featured_rules_to_existing_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            storage.upsert_items([make_item("chore", "chore: remove unused import", score=95, featured=True)])
            source = load_sources()[0][0]
            changed = storage.rescore_items({"test-source": source}, {"include": ["comfyui"], "exclude": []})
            rows = storage.list_items(limit=10)

            self.assertEqual(changed, 1)
            self.assertFalse(rows[0]["featured"])

    def test_rescore_items_deprioritizes_low_value_social_rows(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            storage.upsert_items(
                [
                    make_item(
                        "bad-x",
                        "Parody account #comfyui LTX 2.3 video üretimi 31",
                        score=100,
                        featured=True,
                        source_type="x_search",
                        category="community",
                    )
                ]
            )
            source = next(source for source in load_sources()[0] if source.id == "x-comfyui-models")
            changed = storage.rescore_items({"test-source": source}, {"include": ["comfyui"], "exclude": []})
            rows = storage.list_items(limit=10)

            self.assertEqual(changed, 1)
            self.assertLessEqual(rows[0]["score"], 48)
            self.assertFalse(rows[0]["featured"])

    def test_keyword_search_treats_agent_input_as_plain_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            storage.upsert_items(
                [
                    make_item("manager", "ComfyUI-Manager: Flux 2 workflow", score=80, featured=True),
                    make_item("other", "Plain ComfyUI update", score=40, featured=False),
                ]
            )

            self.assertEqual(normalize_fts_query("ComfyUI-Manager: Flux 2"), '"comfyui" "manager" "flux" "2"')
            rows = storage.list_items(limit=10, query="ComfyUI-Manager: Flux 2")
            count = storage.count_items(query="ComfyUI-Manager: Flux 2")
            facets = storage.item_facets(query="ComfyUI-Manager: Flux 2")
            clusters = storage.list_clusters(limit=5, query="ComfyUI-Manager: Flux 2")

            self.assertEqual([row["guid"] for row in rows], ["manager"])
            self.assertEqual(count, 1)
            self.assertEqual(facets["total"], 1)
            self.assertEqual(clusters[0]["items"][0]["guid"], "manager")

    def test_daily_archive_summarizes_days(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            storage.upsert_items(
                [
                    make_item("day-a", "Day A", score=70, featured=True),
                    make_item(
                        "day-b",
                        "Day B",
                        score=80,
                        featured=False,
                        published_at=datetime(2026, 5, 31, 8, 0, tzinfo=UTC),
                    ),
                ]
            )
            archive = storage.daily_archive(limit=10)

            self.assertEqual([day["date"] for day in archive], ["2026-06-01", "2026-05-31"])
            self.assertEqual(archive[0]["total"], 1)
            self.assertEqual(archive[0]["featured"], 1)
            self.assertEqual(archive[0]["top_item"]["title"], "Day A")

    def test_markdown_digest_and_webhook_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            storage.upsert_items([make_item("digest-a", "Digest A", score=80, featured=True)])
            digest = storage.daily_digest(day="2026-06-01", limit=10)
            markdown = render_markdown_digest(storage, day="2026-06-01", limit=10)
            payload = webhook_payload(
                digest=digest,
                markdown=markdown,
                collect_result={
                    "fetched": 1,
                    "inserted": 1,
                    "updated": 0,
                    "unchanged": 0,
                    "succeeded_sources": 1,
                    "failed_sources": 0,
                    "finished_at": "2026-06-01T12:01:00+00:00",
                },
            )

            self.assertIn("# ComfyUI Daily Digest - 2026-06-01", markdown)
            self.assertIn("[Digest A](https://example.com/digest-a)", markdown)
            self.assertEqual(payload["type"], "comfyui_daily_digest")
            self.assertEqual(payload["date"], "2026-06-01")
            self.assertEqual(payload["refresh"]["inserted"], 1)

    def test_source_health_from_collect_runs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            base = {
                "started_at": "2026-06-01T00:00:00+00:00",
                "finished_at": "2026-06-01T00:01:00+00:00",
                "fetched": 1,
                "inserted": 1,
                "updated": 0,
                "unchanged": 0,
                "succeeded_sources": 1,
                "failed_sources": 0,
                "errors": [],
            }
            storage.record_collect_run(
                {
                    **base,
                    "source_results": [
                        {
                            "id": "source-a",
                            "name": "Source A",
                            "tier": "T1",
                            "category": "official",
                            "ok": True,
                            "fetched": 3,
                            "error": "",
                        }
                    ],
                }
            )
            storage.record_collect_run(
                {
                    **base,
                    "finished_at": "2026-06-01T01:01:00+00:00",
                    "succeeded_sources": 0,
                    "failed_sources": 1,
                    "source_results": [
                        {
                            "id": "source-a",
                            "name": "Source A",
                            "tier": "T1",
                            "category": "official",
                            "ok": False,
                            "fetched": 0,
                            "error": "timeout",
                        }
                    ],
                }
            )
            health = storage.source_health(runs=10)
            self.assertEqual(health["run_count"], 2)
            self.assertEqual(health["sources"][0]["success_rate"], 0.5)
            self.assertEqual(health["sources"][0]["last_error"], "timeout")

    def test_source_submissions_feedback_and_wall(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            first = storage.submit_source(
                url="https://example.com/feed.xml",
                name="Example Feed",
                reason="Tracks ComfyUI custom node releases.",
                contact="tester",
            )
            duplicate = storage.submit_source(
                url="https://example.com/feed.xml",
                name="Example Feed",
                reason="Tracks ComfyUI custom node releases.",
            )
            feedback = storage.record_feedback(message="Ranking looks too strict.", contact="")
            wall = storage.source_wall([], include_pending=True)

            self.assertFalse(first["duplicate"])
            self.assertTrue(duplicate["duplicate"])
            self.assertEqual(feedback["status"], "new")
            self.assertEqual(wall["pending_submissions"], 1)
            self.assertEqual(wall["sources"][0]["kind"], "submission")

    def test_notify_webhook_posts_digest_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(Path(tmp) / "test.sqlite3")
            storage.upsert_items([make_item("webhook-a", "Webhook A", score=90, featured=True)])
            received: list[bytes] = []

            class Handler(BaseHTTPRequestHandler):
                def do_POST(self) -> None:
                    length = int(self.headers.get("content-length", "0"))
                    received.append(self.rfile.read(length))
                    self.send_response(204)
                    self.end_headers()

                def log_message(self, format: str, *args: object) -> None:
                    return

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            previous_url = settings.webhook_url
            try:
                object.__setattr__(settings, "webhook_url", f"http://127.0.0.1:{server.server_port}/hook")
                result = asyncio.run(
                    notify_webhook(
                        storage,
                        {
                            "fetched": 1,
                            "inserted": 1,
                            "updated": 0,
                            "unchanged": 0,
                            "succeeded_sources": 1,
                            "failed_sources": 0,
                            "finished_at": "2026-06-01T12:00:00+00:00",
                        },
                    )
                )
            finally:
                object.__setattr__(settings, "webhook_url", previous_url)
                server.shutdown()
                server.server_close()

            self.assertTrue(result["ok"])
            self.assertEqual(result["status_code"], 204)
            self.assertTrue(received)
            self.assertIn(b"comfyui_daily_digest", received[0])
            self.assertIn(b"Webhook A", received[0])

    def test_loopback_webhook_detection(self) -> None:
        self.assertTrue(is_loopback_webhook("http://127.0.0.1:8799/hook"))
        self.assertTrue(is_loopback_webhook("http://localhost:8799/hook"))
        self.assertFalse(is_loopback_webhook("https://example.com/hook"))


class ApiSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.previous_storage = main_module.storage
        main_module.storage = Storage(Path(self.tmp.name) / "api.sqlite3")

    def tearDown(self) -> None:
        main_module.storage = self.previous_storage
        self.tmp.cleanup()

    def test_feed_and_rss_endpoints(self) -> None:
        client = TestClient(app)
        for path in (
            "/health",
            "/daily",
            "/daily/2026-06-01",
            "/api/feed?mode=selected&limit=1",
            "/api/feed?mode=all&limit=1",
            "/api/feed?mode=daily&limit=1",
            "/api/daily/dates",
            "/api/daily/archive",
            "/api/source-wall",
            "/api/source-health",
            "/api/public",
            "/api/public/items?take=1",
            "/api/public/daily?take=1",
            "/api/public/dailies?take=1",
            "/api/public/daily/archive?take=1",
            "/api/public/briefing?hours=24&take=1",
            "/api/public/sources",
            "/api/public/health",
            "/skill",
            "/comfyui-skill/",
            "/feed?limit=1",
            "/rss?limit=1",
            "/feed.xml?limit=1",
            "/rss/selected.xml?limit=1",
            "/rss/all.xml?limit=1",
            "/rss/daily.xml?limit=1",
            "/rss/digests.xml?limit=1",
            "/rss/feeds.opml",
            "/selected.xml?limit=1",
            "/all.xml?limit=1",
            "/daily.xml?limit=1",
            "/digests.xml?limit=1",
            "/feeds.opml",
        ):
            response = client.get(path)
            self.assertEqual(response.status_code, 200, path)

    def test_submission_and_feedback_endpoints(self) -> None:
        client = TestClient(app)
        submission = client.post(
            "/api/source-submissions",
            json={
                "url": "https://example.org/comfyui.xml",
                "name": "ComfyUI Example",
                "reason": "Useful ComfyUI workflow and extension source.",
            },
        )
        feedback = client.post("/api/feedback", json={"message": "Add source wall sorting."})
        invalid = client.post(
            "/api/source-submissions",
            json={"url": "ftp://example.org/feed", "name": "Bad", "reason": "Invalid scheme should fail."},
        )

        self.assertEqual(submission.status_code, 200)
        self.assertEqual(feedback.status_code, 200)
        self.assertEqual(invalid.status_code, 422)

    def test_items_endpoint_reports_pagination(self) -> None:
        main_module.storage.upsert_items(
            [
                make_item("api-a", "API A", score=70),
                make_item("api-b", "API B", score=60),
                make_item("api-c", "API C", score=50),
            ]
        )
        client = TestClient(app)
        response = client.get("/api/items?limit=2")
        data = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["total"], 3)
        self.assertEqual(data["next_offset"], 2)
        self.assertIsNone(data["prev_offset"])

        page_response = client.get("/api/items?limit=2&page=2")
        page_data = page_response.json()
        self.assertEqual(page_response.status_code, 200)
        self.assertEqual(page_data["offset"], 2)
        self.assertEqual(page_data["page"], 2)
        self.assertEqual(page_data["pages"], 2)
        self.assertEqual(page_data["prev_page"], 1)
        self.assertIsNone(page_data["next_page"])

        channel_response = client.get("/api/items?channel=official&limit=2")
        channel_data = channel_response.json()
        self.assertEqual(channel_response.status_code, 200)
        self.assertEqual(channel_data["total"], 3)

    def test_public_items_endpoint_matches_agent_shape(self) -> None:
        main_module.storage.upsert_items(
            [
                make_item("public-a", "Public A", score=90, featured=True),
                make_item("public-b", "Public B", score=50, featured=False),
                make_item("public-c", "Public C", score=40, featured=True),
                make_item(
                    "public-old",
                    "Public Old",
                    score=95,
                    featured=True,
                    published_at=datetime(2026, 5, 20, 12, 0, tzinfo=UTC),
                ),
            ]
        )
        client = TestClient(app)

        selected = client.get("/api/public/items?mode=selected&take=2&channel=rss")
        selected_data = selected.json()
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected_data["mode"], "selected")
        self.assertEqual(selected_data["take"], 2)
        self.assertEqual(selected_data["limit"], 2)
        self.assertEqual(selected_data["total"], 2)
        self.assertEqual(selected_data["filters"]["featured"], True)
        self.assertEqual(selected_data["filters"]["channel"], "rss")
        self.assertEqual(selected_data["filters"]["hours"], main_module.DEFAULT_NEWS_HOURS)
        self.assertTrue(all(item["featured"] for item in selected_data["items"]))
        self.assertNotIn("Public Old", [item["title"] for item in selected_data["items"]])

        all_response = client.get("/api/public/items?mode=all&take=2&page=2")
        all_data = all_response.json()
        self.assertEqual(all_response.status_code, 200)
        self.assertEqual(all_data["mode"], "all")
        self.assertEqual(all_data["page"], 2)
        self.assertEqual(all_data["total"], 4)
        self.assertEqual(all_data["filters"]["featured"], None)

        selected_all_time = client.get("/api/public/items?mode=selected&take=5&hours=2160")
        selected_all_time_data = selected_all_time.json()
        self.assertEqual(selected_all_time.status_code, 200)
        self.assertIn("Public Old", [item["title"] for item in selected_all_time_data["items"]])

    def test_public_daily_and_dates_use_latest_available_day(self) -> None:
        main_module.storage.upsert_items([make_item("daily-a", "Daily A", score=90, featured=True)])
        client = TestClient(app)

        latest = client.get("/api/public/daily?take=5")
        by_date = client.get("/api/public/daily/2026-06-01?take=5")
        dates = client.get("/api/public/dailies?take=5")

        self.assertEqual(latest.status_code, 200)
        self.assertEqual(by_date.status_code, 200)
        self.assertEqual(dates.status_code, 200)
        self.assertEqual(latest.json()["date"], "2026-06-01")
        self.assertEqual(by_date.json()["total"], 1)
        self.assertEqual(dates.json()["dates"], ["2026-06-01"])

    def test_public_daily_archive_endpoint(self) -> None:
        main_module.storage.upsert_items([make_item("archive-a", "Archive A", score=90, featured=True)])
        client = TestClient(app)

        response = client.get("/api/public/daily/archive?take=5")
        data = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["take"], 5)
        self.assertEqual(data["days"][0]["date"], "2026-06-01")
        self.assertEqual(data["days"][0]["top_item"]["title"], "Archive A")

    def test_public_briefing_compacts_agent_consumption(self) -> None:
        main_module.storage.upsert_items(
            [
                make_item("briefing-a", "Briefing A", score=95, featured=True),
                make_item("briefing-b", "Briefing B", score=60, featured=False),
            ]
        )
        client = TestClient(app)

        response = client.get("/api/public/briefing?hours=24&take=1&channel=rss")
        data = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(data["type"], "comfyui_news_briefing")
        self.assertEqual(data["window"]["hours"], 24)
        self.assertEqual(data["filters"]["featured"], True)
        self.assertEqual(data["summary"]["total"], 1)
        self.assertEqual(data["summary"]["featured"], 1)
        self.assertEqual(data["top_items"][0]["title"], "Briefing A")
        self.assertEqual(data["top_items"][0]["source"]["tier"], "T1")
        self.assertEqual(data["clusters"][0]["item_count"], 1)
        self.assertIn("channel=rss", data["links"]["items"])
        self.assertIn("# ComfyUI Briefing - last 24h", data["markdown"])

    def test_digest_archive_rss_has_daily_issue_items(self) -> None:
        main_module.storage.upsert_items([make_item("digest-rss-a", "Digest RSS A", score=90, featured=True)])
        client = TestClient(app)

        response = client.get("/digests.xml?limit=5")
        text = response.text

        self.assertEqual(response.status_code, 200)
        self.assertIn("application/rss+xml", response.headers["content-type"])
        self.assertIn("ComfyUI Daily Digest Archive", text)
        self.assertIn("ComfyUI Daily Digest 2026-06-01", text)
        self.assertIn("Total signals: 1", text)

    def test_opml_feed_bundle_lists_tracker_feeds(self) -> None:
        client = TestClient(app)

        response = client.get("/feeds.opml")
        text = response.text

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/x-opml", response.headers["content-type"])
        self.assertIn('xmlUrl="http://testserver/selected.xml"', text)
        self.assertIn('xmlUrl="http://testserver/all.xml"', text)
        self.assertIn('xmlUrl="http://testserver/daily.xml"', text)
        self.assertIn('xmlUrl="http://testserver/digests.xml"', text)


if __name__ == "__main__":
    unittest.main()
