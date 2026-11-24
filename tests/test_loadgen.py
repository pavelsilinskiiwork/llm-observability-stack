import httpx
import pytest
from prometheus_client import CollectorRegistry

from llm_obs import loadgen
from llm_obs.app import create_app
from llm_obs.providers import FakeProvider
from llm_obs.settings import Settings


async def test_loadgen_drives_the_app_through_every_mode(monkeypatch):
    monkeypatch.setitem(loadgen.SCENARIOS, "tiny", [("normal", 0.3), ("failing", 0.3), ("slow", 0.3)])
    registry = CollectorRegistry()
    provider = FakeProvider(latency_scale=0)
    app = create_app(Settings(latency_scale=0, demo_controls=True), provider, registry)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await loadgen.main("http://test", "tiny", rps=40, client=client, seed=3)
    ok = (
        registry.get_sample_value("llm_requests_total", {"provider": "fake", "model": "fast-small", "status": "ok"})
        or 0
    )
    assert ok > 0
    assert provider.mode == "slow"  # the last phase left the service in the mode it set


def test_every_scenario_only_uses_known_modes():
    from llm_obs.providers import MODES

    for phases in loadgen.SCENARIOS.values():
        assert all(mode in MODES and seconds > 0 for mode, seconds in phases)


def test_demo_scenario_is_long_enough_for_the_alert_windows():
    failing = sum(s for m, s in loadgen.SCENARIOS["demo"] if m == "failing")
    slow = sum(s for m, s in loadgen.SCENARIOS["demo"] if m == "slow")
    assert failing >= 150 and slow >= 150  # a 2 minute window plus "for: 1m" needs about 3 minutes


@pytest.mark.parametrize("scenario", ["steady", "demo"])
def test_cli_parses(scenario, monkeypatch):
    called = {}

    async def fake_main(base_url, scenario, rps, scale=1.0, **kw):
        called.update(base_url=base_url, scenario=scenario, rps=rps, scale=scale)

    monkeypatch.setattr(loadgen, "main", fake_main)
    loadgen.cli(["--scenario", scenario, "--rps", "2", "--scale", "0.5"])
    assert called == {"base_url": "http://localhost:8000", "scenario": scenario, "rps": 2.0, "scale": 0.5}


async def test_run_phase_reports_every_response_not_just_the_last_ones():
    """Regression: finished tasks used to be dropped from the list that gets counted."""
    import random

    provider = FakeProvider(latency_scale=0)
    app = create_app(Settings(latency_scale=0, demo_controls=True), provider, CollectorRegistry())
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        codes = await loadgen.run_phase(client, "normal", 0.5, 40, random.Random(5))
    assert sum(codes.values()) == provider.calls
