from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.email_perf import EmailPerfTracker, EMAIL_PERF
import routes.email.email_routes as email_routes
import routes.email.email_helpers as email_helpers
import core.middleware as middleware


def test_email_perf_tracker_computes_percentiles_cache_rates_and_window_cap():
    tracker = EmailPerfTracker(window_size=20)
    for i in range(25):
        tracker.record("list", float(i + 1), cache_status="hit" if i % 2 == 0 else "miss", ok=True)
    tracker.record("send", 42.5, ok=False)

    snap = tracker.snapshot()
    assert snap["summary"]["total_operations"] == 26
    assert snap["summary"]["total_errors"] == 1
    assert snap["summary"]["cache_hits"] == 13
    assert snap["summary"]["cache_misses"] == 12
    assert snap["summary"]["overall_cache_hit_rate"] == round(13 / 25, 4)

    list_stats = snap["operations"]["list"]
    assert list_stats["count"] == 25
    assert list_stats["window_samples"] == 20
    assert list_stats["max_ms"] == 25.0
    assert list_stats["p50_ms"] > 0
    assert list_stats["p95_ms"] >= list_stats["p50_ms"]
    assert list_stats["cache"]["hit_rate"] == round(13 / 25, 4)

    send_stats = snap["operations"]["send"]
    assert send_stats["count"] == 1
    assert send_stats["error_count"] == 1
    assert send_stats["cache"]["hit_rate"] is None

    reset_snap = tracker.reset()
    assert reset_snap["summary"]["total_operations"] == 0
    assert reset_snap["operations"]["list"]["count"] == 0


def test_email_metrics_endpoint_and_route_instrumentation(monkeypatch):
    EMAIL_PERF.reset()
    monkeypatch.setattr(middleware, "require_admin", lambda req: None)

    router = email_routes.setup_email_routes()
    app = FastAPI()
    app.dependency_overrides[email_helpers.require_owner] = lambda: "admin"
    app.include_router(router)
    client = TestClient(app)

    # 1. list cached_only + cached_only with manual refresh=1
    r1 = client.get("/api/email/list?cached_only=1")
    assert r1.status_code == 200
    r2 = client.get("/api/email/list?cached_only=1&refresh=1")
    assert r2.status_code == 200

    # 2. search short-circuit
    r3 = client.get("/api/email/search?q=a")
    assert r3.status_code == 200

    # 3. attachments list (cache miss when unconfigured)
    r4 = client.get("/api/email/attachments/101")
    assert r4.status_code == 200

    # 4. send error path recorded when no SMTP account is configured
    r5 = client.post("/api/email/send", json={"to": "user@example.com", "subject": "Hi", "body": "Test"})
    assert r5.status_code == 200
    assert r5.json()["success"] is False

    metrics = client.get("/api/email/metrics")
    assert metrics.status_code == 200
    body = metrics.json()
    ops = body["operations"]
    assert ops["list"]["count"] == 2
    assert ops["refresh"]["count"] == 1
    assert ops["refresh"]["cache"]["bypasses"] == 1
    assert ops["search"]["count"] == 1
    assert ops["attachments_list"]["count"] == 1
    assert ops["attachments_list"]["cache"]["misses"] == 1
    assert ops["send"]["count"] == 1
    assert ops["send"]["error_count"] == 1
    assert body["summary"]["total_operations"] == 6
    assert ops["attachments_list"]["error_count"] == 1
    assert body["summary"]["total_errors"] == 2

    reset_resp = client.post("/api/email/metrics/reset")
    assert reset_resp.status_code == 200
    assert reset_resp.json()["summary"]["total_operations"] == 0
