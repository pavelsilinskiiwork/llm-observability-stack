"""Send one request, then check the trace really arrived in Tempo (app -> OTLP -> Tempo -> query API).

Exits non-zero if the trace is missing after the timeout. Used by the `stack` job in CI.
"""

import argparse
import sys
import time

import httpx


def span_names(trace: dict) -> set[str]:
    names = set()
    for batch in trace.get("batches", []):
        for scope in batch.get("scopeSpans", []):
            names.update(span["name"] for span in scope.get("spans", []))
    return names


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--app-url", default="http://localhost:8000")
    ap.add_argument("--tempo-url", default="http://localhost:3200")
    ap.add_argument("--timeout", type=float, default=60)
    args = ap.parse_args()

    r = httpx.post(f"{args.app_url}/chat", json={"message": "trace me", "model": "fast-small"}, timeout=30)
    trace_id = r.headers.get("x-trace-id")
    if not trace_id:
        print("FAIL: /chat returned no x-trace-id header")
        return 1
    print(f"sent request, trace id {trace_id}")

    deadline = time.time() + args.timeout
    names: set[str] = set()
    while time.time() < deadline:
        try:
            resp = httpx.get(f"{args.tempo_url}/api/traces/{trace_id}", timeout=10)
            if resp.status_code == 200:
                names = span_names(resp.json())
                if {"chat", "llm.generate"} <= names:
                    print(f"OK: Tempo has the trace with spans {sorted(names)}")
                    return 0
        except httpx.HTTPError:
            pass
        time.sleep(2)
    print(f"FAIL: trace {trace_id} not found in Tempo within {args.timeout}s (spans seen: {sorted(names)})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
