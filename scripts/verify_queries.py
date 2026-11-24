"""Run every query of the dashboard against a live Prometheus and fail if one is broken or returns nothing.

    python scripts/verify_queries.py --prometheus-url http://localhost:9090

A dashboard that "looks fine" can hide a typo in a metric name: the panel just stays empty. This script catches
that. Grafana variables are replaced by plain values first ($model -> .*, $__rate_interval -> 1m).
"""

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

DASHBOARD = Path(__file__).resolve().parent.parent / "grafana" / "dashboards" / "llm-overview.json"


def queries(dashboard: dict):
    for panel in dashboard["panels"]:
        for t in panel.get("targets", []):
            yield panel["title"], t["expr"].replace("$__rate_interval", "1m").replace("$model", ".*")


def run_query(base: str, expr: str) -> list:
    url = f"{base.rstrip('/')}/api/v1/query?" + urllib.parse.urlencode({"query": expr})
    with urllib.request.urlopen(url, timeout=15) as response:
        body = json.loads(response.read())
    if body["status"] != "success":
        raise RuntimeError(body.get("error", "query failed"))
    return body["data"]["result"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prometheus-url", default="http://localhost:9090")
    parser.add_argument("--allow-empty", nargs="*", default=["Firing alerts"], help="panel titles that may be empty")
    args = parser.parse_args(argv)
    dashboard = json.loads(DASHBOARD.read_text())
    failures = 0
    for title, expr in queries(dashboard):
        try:
            result = run_query(args.prometheus_url, expr)
        except Exception as e:  # noqa: BLE001 - report every broken query, not just the first
            print(f"FAIL  {title}: {e}\n      {expr}")
            failures += 1
            continue
        if not result and title not in args.allow_empty:
            print(f"EMPTY {title}\n      {expr}")
            failures += 1
        else:
            print(f"ok    {title}: {len(result)} series")
    print(f"\n{failures} problem(s)" if failures else "\nall dashboard queries return data")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
