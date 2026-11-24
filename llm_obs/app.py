"""FastAPI service: POST /chat, GET /metrics, GET /health (and POST /demo/mode when enabled)."""

import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, generate_latest
from pydantic import BaseModel, Field, field_validator

from .metrics import DailySpend, Metrics
from .providers import MODES, PROFILES, FakeProvider, ProviderError
from .settings import Settings


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    model: str = "fast-small"
    max_tokens: int = Field(default=300, ge=1, le=1000)

    @field_validator("model")
    @classmethod
    def known_model(cls, value: str) -> str:
        if value not in PROFILES:
            raise ValueError(f"model must be one of {sorted(PROFILES)}")
        return value


class ModeBody(BaseModel):
    mode: str


def create_app(settings: Settings | None = None, provider: FakeProvider | None = None, registry=None) -> FastAPI:
    settings = settings or Settings.from_env()
    provider = provider or FakeProvider(settings.latency_scale)
    registry = registry or CollectorRegistry()
    metrics = Metrics(registry, settings.daily_budget_usd, settings.version, DailySpend())
    metrics.init_series(provider.name, PROFILES)
    app = FastAPI(title="LLM observability demo", version=settings.version)
    app.state.metrics = metrics
    app.state.provider = provider

    @app.middleware("http")
    async def http_metrics(request: Request, call_next):
        if request.url.path == "/metrics":
            return await call_next(request)
        started = time.perf_counter()
        response = await call_next(request)
        route = request.scope.get("route")
        template = route.path if route is not None else "unmatched"  # never the raw URL: that explodes cardinality
        metrics.http_requests.labels(request.method, template, str(response.status_code)).inc()
        metrics.http_duration.labels(request.method, template).observe(time.perf_counter() - started)
        return response

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": settings.version}

    @app.get("/metrics", include_in_schema=False)
    async def scrape():
        return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)

    @app.post("/chat")
    async def chat(body: ChatBody):
        metrics.inflight.inc()
        started = time.perf_counter()
        try:
            result = await provider.generate(body.model, body.message, body.max_tokens)
        except ProviderError:
            metrics.record_llm(provider.name, body.model, "error", time.perf_counter() - started)
            return JSONResponse({"error": "provider_failed"}, status_code=502)
        finally:
            metrics.inflight.dec()
        metrics.record_llm(provider.name, body.model, "ok", time.perf_counter() - started, result)
        return {
            "text": result.text,
            "model": body.model,
            "usage": {"input_tokens": result.input_tokens, "output_tokens": result.output_tokens},
            "cost_usd": round(result.cost_usd, 8),
        }

    if settings.demo_controls:

        @app.get("/demo/mode")
        async def get_mode():
            return {"mode": provider.mode, "available": sorted(MODES)}

        @app.post("/demo/mode")
        async def set_mode(body: ModeBody):
            if body.mode not in MODES:
                raise HTTPException(422, f"mode must be one of {sorted(MODES)}")
            provider.set_mode(body.mode)
            return {"mode": provider.mode}

    return app


app = create_app()
