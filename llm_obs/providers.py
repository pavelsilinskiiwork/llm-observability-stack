"""A simulated model provider with two model profiles and a switchable failure mode.

Nothing here calls a real API: latency, token counts and errors are generated, so the dashboards can be
shown, tested and alerted on without spending money. The numbers are illustrative, not a benchmark.
"""

import asyncio
import math
import random
from dataclasses import dataclass


class ProviderError(Exception):
    pass


@dataclass(frozen=True)
class ModelProfile:
    name: str
    median_latency: float  # seconds
    input_per_mtok: float  # USD per million tokens
    output_per_mtok: float
    output_tokens: tuple[int, int]  # (min, max) of the generated answer


PROFILES = {
    "fast-small": ModelProfile("fast-small", 0.3, 0.2, 1.0, (40, 200)),
    "big-smart": ModelProfile("big-smart", 1.0, 3.0, 15.0, (100, 400)),
}


@dataclass(frozen=True)
class Behavior:
    latency_multiplier: float = 1.0
    error_rate: float = 0.01


MODES = {
    "normal": Behavior(1.0, 0.01),
    "slow": Behavior(4.0, 0.01),
    "failing": Behavior(1.0, 0.4),
}


@dataclass
class LLMResult:
    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


class FakeProvider:
    name = "fake"

    def __init__(self, latency_scale: float = 1.0, rng: random.Random | None = None):
        self.latency_scale = latency_scale
        self.rng = rng or random.Random()
        self.mode = "normal"
        self.behavior = MODES["normal"]
        self.calls = 0

    def set_mode(self, mode: str) -> None:
        self.mode = mode
        self.behavior = MODES[mode]

    async def generate(self, model: str, message: str, max_tokens: int) -> LLMResult:
        self.calls += 1
        profile = PROFILES[model]
        # log-normal latency: mostly near the median, with a realistic slow tail
        latency = profile.median_latency * math.exp(self.rng.gauss(0, 0.35)) * self.behavior.latency_multiplier
        await asyncio.sleep(latency * self.latency_scale)
        if self.rng.random() < self.behavior.error_rate:
            raise ProviderError("simulated provider failure")
        input_tokens = 50 + max(1, len(message) // 4)
        output_tokens = min(max_tokens, self.rng.randint(*profile.output_tokens))
        cost = (input_tokens * profile.input_per_mtok + output_tokens * profile.output_per_mtok) / 1_000_000
        return LLMResult(f"Echo: {message}", input_tokens, output_tokens, cost)
