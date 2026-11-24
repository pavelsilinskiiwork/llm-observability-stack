"""Every metric the service exposes, in one place.

Label discipline: labels are low-cardinality on purpose (provider, model, status, route template). Never put a
user id, a prompt or a raw URL in a label: each distinct value creates a new time series.
"""

import threading
from datetime import UTC, datetime

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram

# Seconds. Tuned for LLM calls: fine resolution around 0.25-2 s, a tail for slow and stuck requests.
LATENCY_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30)


class DailySpend:
    """Dollars spent today (UTC), in this process. Resets at midnight UTC."""

    def __init__(self, today=lambda: datetime.now(UTC).strftime("%Y-%m-%d")):
        self._today = today
        self._day = today()
        self._total = 0.0
        self._lock = threading.Lock()

    def add(self, amount: float) -> None:
        with self._lock:
            self._roll()
            self._total += amount

    def value(self) -> float:
        with self._lock:
            self._roll()
            return self._total

    def _roll(self) -> None:
        day = self._today()
        if day != self._day:
            self._day, self._total = day, 0.0


class Metrics:
    def __init__(self, registry: CollectorRegistry, daily_budget_usd: float, version: str, spend: DailySpend):
        self.registry = registry
        self.spend = spend
        self.requests = Counter(
            "llm_requests_total", "LLM calls by outcome", ["provider", "model", "status"], registry=registry
        )
        self.duration = Histogram(
            "llm_request_duration_seconds",
            "Wall time of an LLM call",
            ["provider", "model", "status"],
            buckets=LATENCY_BUCKETS,
            registry=registry,
        )
        self.tokens = Counter(
            "llm_tokens_total", "Tokens processed", ["provider", "model", "direction"], registry=registry
        )
        self.cost = Counter(
            "llm_cost_usd_total", "Money spent on model calls, in USD", ["provider", "model"], registry=registry
        )
        self.inflight = Gauge("llm_inflight_requests", "LLM calls running right now", registry=registry)
        self.daily_spend = Gauge("llm_daily_spend_usd", "USD spent today (UTC)", registry=registry)
        self.daily_spend.set_function(spend.value)  # evaluated at scrape time, so it also resets at midnight
        self.daily_budget = Gauge("llm_daily_budget_usd", "Daily spending cap in USD", registry=registry)
        self.daily_budget.set(daily_budget_usd)
        self.http_requests = Counter(
            "http_requests_total", "HTTP requests", ["method", "route", "code"], registry=registry
        )
        self.http_duration = Histogram(
            "http_request_duration_seconds",
            "HTTP request time",
            ["method", "route"],
            buckets=LATENCY_BUCKETS,
            registry=registry,
        )
        info = Gauge("llm_app_info", "Build information", ["version"], registry=registry)
        info.labels(version=version).set(1)

    def init_series(self, provider: str, models) -> None:
        """Create every expected series at 0 so rate() and ratios work from the first scrape.

        A labelled counter does not exist until its first increment, so "error ratio" would be an empty result
        (not 0) until the first failure, and the first failure would be invisible to rate().
        """
        for model in models:
            for status in ("ok", "error"):
                self.requests.labels(provider, model, status)
                self.duration.labels(provider, model, status)
            for direction in ("input", "output"):
                self.tokens.labels(provider, model, direction)
            self.cost.labels(provider, model)

    def record_llm(self, provider: str, model: str, status: str, seconds: float, result=None) -> None:
        self.requests.labels(provider, model, status).inc()
        self.duration.labels(provider, model, status).observe(seconds)
        if result is not None:
            self.tokens.labels(provider, model, "input").inc(result.input_tokens)
            self.tokens.labels(provider, model, "output").inc(result.output_tokens)
            self.cost.labels(provider, model).inc(result.cost_usd)
            self.spend.add(result.cost_usd)
