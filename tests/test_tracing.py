import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from llm_obs.app import create_app
from llm_obs.settings import Settings
from llm_obs.tracing import build_tracer_provider


@pytest.fixture
def exporter():
    return InMemorySpanExporter()


@pytest.fixture
def traced(provider, registry, exporter):
    tp = build_tracer_provider("test", processor=SimpleSpanProcessor(exporter))
    return TestClient(create_app(Settings(latency_scale=0), provider, registry, tracer_provider=tp))


def by_name(exporter):
    return {s.name: s for s in exporter.get_finished_spans()}


def test_chat_creates_root_and_child_span(traced, exporter):
    r = traced.post("/chat", json={"message": "hello", "model": "big-smart", "max_tokens": 50})
    spans = by_name(exporter)
    assert set(spans) == {"chat", "llm.generate"}
    assert spans["llm.generate"].parent.span_id == spans["chat"].context.span_id
    assert spans["llm.generate"].context.trace_id == spans["chat"].context.trace_id
    assert r.headers["x-trace-id"] == format(spans["chat"].context.trace_id, "032x")


def test_model_span_carries_genai_attributes_but_not_the_prompt(traced, exporter):
    r = traced.post("/chat", json={"message": "my secret prompt", "model": "big-smart", "max_tokens": 50})
    attrs = dict(by_name(exporter)["llm.generate"].attributes)
    assert attrs["gen_ai.request.model"] == "big-smart"
    assert attrs["gen_ai.usage.output_tokens"] == r.json()["usage"]["output_tokens"]
    assert attrs["gen_ai.usage.input_tokens"] == r.json()["usage"]["input_tokens"]
    assert "my secret prompt" not in str([dict(s.attributes) for s in exporter.get_finished_spans()])


def test_provider_failure_marks_the_trace_as_error(provider, registry, exporter):
    from llm_obs.providers import Behavior

    provider.behavior = Behavior(1.0, 1.0)
    tp = build_tracer_provider("test", processor=SimpleSpanProcessor(exporter))
    client = TestClient(create_app(Settings(latency_scale=0), provider, registry, tracer_provider=tp))
    r = client.post("/chat", json={"message": "hi"})
    assert r.status_code == 502 and r.headers["x-trace-id"]
    root = by_name(exporter)["chat"]
    assert root.status.status_code == StatusCode.ERROR
    assert any(e.name == "exception" for e in root.events)


def test_incoming_traceparent_is_continued(traced, exporter):
    tid = "0af7651916cd43dd8448eb211c80319c"
    traced.post("/chat", json={"message": "hi"}, headers={"traceparent": f"00-{tid}-b7ad6b7169203331-01"})
    assert format(by_name(exporter)["chat"].context.trace_id, "032x") == tid


def test_no_endpoint_means_no_exporter():
    tp = build_tracer_provider("test", endpoint="")
    assert tp._active_span_processor._span_processors == ()


def test_endpoint_gets_the_traces_path():
    tp = build_tracer_provider("test", endpoint="http://tempo:4318/")
    exporter = tp._active_span_processor._span_processors[0].span_exporter
    assert exporter._endpoint == "http://tempo:4318/v1/traces"
    tp.shutdown()


def test_settings_reads_endpoint():
    assert Settings.from_env({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://t:4318"}).otlp_endpoint == "http://t:4318"
