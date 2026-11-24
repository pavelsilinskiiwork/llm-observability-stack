"""Traffic generator that also breaks the service on purpose, so dashboards and alerts have something to show.

    python -m llm_obs.loadgen --base-url http://localhost:8000 --scenario demo

Scenarios: `steady` (normal traffic) and `demo` (normal, failing, normal, slow, normal). The service must run
with DEMO_CONTROLS=true to accept the mode switches.
"""

import argparse
import asyncio
import random
import time

import httpx

PROMPTS = [
    "Summarize the refund policy in two sentences.",
    "What is a sliding window rate limiter?",
    "Draft a polite reminder about an unpaid invoice.",
    "Explain exponential backoff to a new developer.",
    "List three risks of caching LLM answers.",
]

# (mode, seconds). Long enough for 2-minute alert windows to fill and fire.
SCENARIOS = {
    "steady": [("normal", 60)],
    "demo": [("normal", 90), ("failing", 180), ("normal", 90), ("slow", 180), ("normal", 60)],
}


async def one_request(client: httpx.AsyncClient, rng: random.Random) -> int:
    body = {
        "message": rng.choice(PROMPTS),
        "model": rng.choices(["fast-small", "big-smart"], [0.7, 0.3])[0],
    }
    try:
        return (await client.post("/chat", json=body, timeout=30)).status_code
    except httpx.HTTPError:
        return 0


async def run_phase(client: httpx.AsyncClient, mode: str, seconds: float, rps: float, rng: random.Random) -> dict:
    if mode:
        await client.post("/demo/mode", json={"mode": mode})
    codes: dict[int, int] = {}
    tasks: list[asyncio.Task] = []
    deadline = time.monotonic() + seconds
    interval = 1 / rps
    while time.monotonic() < deadline:
        tasks.append(asyncio.create_task(one_request(client, rng)))
        await asyncio.sleep(rng.expovariate(1 / interval))  # Poisson arrivals look like real traffic
    for code in await asyncio.gather(*tasks):
        codes[code] = codes.get(code, 0) + 1
    return codes


async def main(base_url: str, scenario: str, rps: float, scale: float = 1.0, client=None, seed: int | None = None):
    rng = random.Random(seed)
    own = client is None
    client = client or httpx.AsyncClient(base_url=base_url)
    try:
        for mode, seconds in SCENARIOS[scenario]:
            print(f"phase: {mode} for {seconds * scale:.0f}s at {rps} req/s", flush=True)
            codes = await run_phase(client, mode, seconds * scale, rps, rng)
            print(f"  responses: {dict(sorted(codes.items()))}", flush=True)
    finally:
        if own:
            await client.post("/demo/mode", json={"mode": "normal"})
            await client.aclose()


def cli(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), default="demo")
    parser.add_argument("--rps", type=float, default=5)
    parser.add_argument("--scale", type=float, default=1.0, help="multiply every phase length (0.5 = half)")
    args = parser.parse_args(argv)
    asyncio.run(main(args.base_url, args.scenario, args.rps, args.scale))


if __name__ == "__main__":
    cli()
