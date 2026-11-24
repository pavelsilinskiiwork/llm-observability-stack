import pytest
from conftest import sample

OK = {"provider": "fake", "model": "fast-small", "status": "ok"}


def test_a_successful_call_updates_requests_latency_tokens_and_cost(client, registry):
    r = client.post("/chat", json={"message": "hello there"})
    assert r.status_code == 200
    usage = r.json()["usage"]
    assert sample(registry, "llm_requests_total", **OK) == 1
    assert sample(registry, "llm_request_duration_seconds_count", **OK) == 1
    assert (
        sample(registry, "llm_tokens_total", provider="fake", model="fast-small", direction="input")
        == usage["input_tokens"]
    )
    assert (
        sample(registry, "llm_tokens_total", provider="fake", model="fast-small", direction="output")
        == usage["output_tokens"]
    )
    assert sample(registry, "llm_cost_usd_total", provider="fake", model="fast-small") == pytest.approx(
        r.json()["cost_usd"], rel=1e-5
    )


def test_a_failed_call_counts_as_error_and_costs_nothing(make_client, registry, always_fail):
    client = make_client()
    assert client.post("/chat", json={"message": "hello"}).status_code == 502
    assert sample(registry, "llm_requests_total", provider="fake", model="fast-small", status="error") == 1
    assert (
        sample(registry, "llm_request_duration_seconds_count", provider="fake", model="fast-small", status="error") == 1
    )
    assert sample(registry, "llm_cost_usd_total", provider="fake", model="fast-small") == 0
    assert sample(registry, "llm_daily_spend_usd") == 0


def test_inflight_gauge_returns_to_zero_even_when_the_call_fails(make_client, registry, always_fail):
    client = make_client()
    client.post("/chat", json={"message": "hello"})
    assert sample(registry, "llm_inflight_requests") == 0


def test_daily_spend_adds_up_and_budget_is_exposed(make_client, registry):
    client = make_client(daily_budget_usd=2.5)
    total = sum(client.post("/chat", json={"message": f"q{i}"}).json()["cost_usd"] for i in range(5))
    assert sample(registry, "llm_daily_spend_usd") == pytest.approx(total, rel=1e-4)
    assert sample(registry, "llm_daily_budget_usd") == 2.5


def test_models_are_separate_series(client, registry):
    client.post("/chat", json={"message": "a", "model": "fast-small"})
    client.post("/chat", json={"message": "a", "model": "big-smart"})
    assert sample(registry, "llm_requests_total", provider="fake", model="big-smart", status="ok") == 1
    assert sample(registry, "llm_requests_total", provider="fake", model="fast-small", status="ok") == 1


def test_unknown_model_is_rejected_and_creates_no_series(client, registry):
    assert client.post("/chat", json={"message": "a", "model": "gpt-9000"}).status_code == 422
    assert all("gpt-9000" not in str(s.labels) for m in registry.collect() for s in m.samples)


def test_http_metrics_use_the_route_template_not_the_raw_url(client, registry):
    for path in ["/nope", "/nope/123", "/other"]:
        client.get(path)
    assert sample(registry, "http_requests_total", method="GET", route="unmatched", code="404") == 3
    labels = [s.labels.get("route") for m in registry.collect() for s in m.samples if "route" in s.labels]
    assert not any(v in {"/nope", "/nope/123", "/other"} for v in labels)


def test_scraping_metrics_does_not_count_as_traffic(client, registry):
    client.get("/metrics")
    client.get("/metrics")
    assert sample(registry, "http_requests_total", method="GET", route="/metrics", code="200") == 0


def test_metrics_endpoint_serves_prometheus_text_with_every_metric_family(client):
    client.post("/chat", json={"message": "hello"})
    r = client.get("/metrics")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    for name in ["llm_requests_total", "llm_request_duration_seconds_bucket", "llm_tokens_total", "llm_cost_usd_total",
                 "llm_inflight_requests", "llm_daily_spend_usd", "llm_daily_budget_usd", "http_requests_total",
                 "llm_app_info"]:  # fmt: skip
        assert name in r.text


def test_build_version_is_exposed(make_client, registry):
    make_client(version="abc123")
    assert sample(registry, "llm_app_info", version="abc123") == 1


def test_daily_spend_resets_at_midnight():
    from llm_obs.metrics import DailySpend

    day = ["2026-01-01"]
    spend = DailySpend(today=lambda: day[0])
    spend.add(0.5)
    assert spend.value() == 0.5
    day[0] = "2026-01-02"
    assert spend.value() == 0
    spend.add(0.1)
    assert spend.value() == pytest.approx(0.1)


def test_series_exist_at_zero_before_any_traffic(client, registry):
    """Otherwise the error ratio is an empty result, not 0, until the first failure."""
    for model in ("fast-small", "big-smart"):
        for status in ("ok", "error"):
            assert (
                registry.get_sample_value("llm_requests_total", {"provider": "fake", "model": model, "status": status})
                == 0.0
            )
    assert registry.get_sample_value("llm_cost_usd_total", {"provider": "fake", "model": "big-smart"}) == 0.0
