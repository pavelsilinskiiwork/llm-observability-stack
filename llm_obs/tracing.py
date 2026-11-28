"""OpenTelemetry tracing: one trace per /chat request, with a child span around the model call.

Tracing is off unless an OTLP endpoint is configured (OTEL_EXPORTER_OTLP_ENDPOINT). With no endpoint the
spans are still created (so the code path is identical) but nothing is exported and the cost is negligible.

Span attributes follow the OpenTelemetry GenAI conventions where one exists (gen_ai.*). The prompt text is
never recorded: traces are stored longer and read by more people than the request log.
"""

from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanProcessor

SERVICE_NAME = "llm-obs-demo"


def build_tracer_provider(
    version: str, endpoint: str | None = None, processor: SpanProcessor | None = None
) -> TracerProvider:
    """Build a provider. `processor` lets tests collect spans in memory; `endpoint` exports to an OTLP/HTTP collector.

    The provider is returned, not installed globally, so several apps (and tests) in one process stay independent.
    """
    provider = TracerProvider(resource=Resource.create({"service.name": SERVICE_NAME, "service.version": version}))
    if processor is not None:
        provider.add_span_processor(processor)
    elif endpoint:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        base = endpoint.rstrip("/")
        url = base if base.endswith("/v1/traces") else f"{base}/v1/traces"
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=url)))
    return provider
