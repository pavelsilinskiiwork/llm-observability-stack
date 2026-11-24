import os
from dataclasses import dataclass


def _bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    daily_budget_usd: float = 1.0
    latency_scale: float = 1.0  # multiplies the simulated model latency; tests use 0
    demo_controls: bool = False  # exposes POST /demo/mode so a load script can break the service on purpose
    version: str = "dev"

    @classmethod
    def from_env(cls, env=None) -> "Settings":
        env = os.environ if env is None else env
        d = cls()
        return cls(
            daily_budget_usd=float(env.get("DAILY_BUDGET_USD", d.daily_budget_usd)),
            latency_scale=float(env.get("LATENCY_SCALE", d.latency_scale)),
            demo_controls=_bool(env.get("DEMO_CONTROLS", str(d.demo_controls))),
            version=env.get("GIT_SHA", d.version) or d.version,
        )
