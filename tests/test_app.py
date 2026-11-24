import pytest


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "version": "dev"}


def test_chat_validation(client):
    assert client.post("/chat", json={"message": ""}).status_code == 422
    assert client.post("/chat", json={"message": "x", "max_tokens": 5000}).status_code == 422
    assert client.post("/chat", json={}).status_code == 422


def test_max_tokens_caps_the_answer(client):
    r = client.post("/chat", json={"message": "hello", "model": "big-smart", "max_tokens": 5})
    assert r.json()["usage"]["output_tokens"] <= 5


def test_demo_controls_are_off_by_default(client):
    assert client.get("/demo/mode").status_code == 404
    assert client.post("/demo/mode", json={"mode": "failing"}).status_code in (404, 405)


def test_demo_mode_can_be_switched_and_changes_behavior(make_client, provider):
    client = make_client(demo_controls=True)
    assert client.get("/demo/mode").json()["mode"] == "normal"
    assert client.post("/demo/mode", json={"mode": "failing"}).json() == {"mode": "failing"}
    assert provider.behavior.error_rate == 0.4
    assert client.post("/demo/mode", json={"mode": "nonsense"}).status_code == 422


@pytest.mark.parametrize("mode,multiplier", [("normal", 1.0), ("slow", 4.0)])
def test_modes_define_latency(make_client, provider, mode, multiplier):
    client = make_client(demo_controls=True)
    client.post("/demo/mode", json={"mode": mode})
    assert provider.behavior.latency_multiplier == multiplier


def test_settings_from_env():
    from llm_obs.settings import Settings

    s = Settings.from_env({"DAILY_BUDGET_USD": "3", "DEMO_CONTROLS": "true", "GIT_SHA": "abc"})
    assert (s.daily_budget_usd, s.demo_controls, s.version) == (3.0, True, "abc")
    assert Settings.from_env({}).demo_controls is False
