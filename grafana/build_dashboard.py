"""Dashboard as code: this script generates grafana/dashboards/llm-overview.json.

    python grafana/build_dashboard.py          # rewrite the JSON
    python grafana/build_dashboard.py --check  # exit 1 if the committed JSON is out of date (used by tests and CI)

Edit this file, not the JSON: a hand-edited export is unreadable in review and drifts from what was intended.
"""

import json
import sys
from pathlib import Path

OUT = Path(__file__).parent / "dashboards" / "llm-overview.json"
DS = {"type": "prometheus", "uid": "prometheus"}
FILTER = 'model=~"$model"'
R = "$__rate_interval"


def target(expr: str, legend: str = "", ref: str = "A") -> dict:
    return {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": ref, "editorMode": "code"}


def stat(pid, title, x, expr, unit, thresholds=None, description=""):
    steps = thresholds or [{"color": "green", "value": None}]
    return {
        "id": pid, "type": "stat", "title": title, "description": description, "datasource": DS,
        "gridPos": {"x": x, "y": 0, "w": 4, "h": 4},
        "targets": [target(expr)],
        "fieldConfig": {"defaults": {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}}, "overrides": []},
        "options": {"reduceOptions": {"calcs": ["lastNotNull"]}, "colorMode": "value", "graphMode": "area"},
    }  # fmt: skip


def series(pid, title, x, y, w, targets, unit, description="", stack=False):
    custom = {"lineWidth": 2, "fillOpacity": 15, "showPoints": "never"}
    if stack:
        custom["stacking"] = {"mode": "normal"}
    return {
        "id": pid, "type": "timeseries", "title": title, "description": description, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": 8},
        "targets": targets,
        "fieldConfig": {"defaults": {"unit": unit, "custom": custom}, "overrides": []},
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
    }  # fmt: skip


def dashboard() -> dict:
    ok = f'rate(llm_request_duration_seconds_bucket{{{FILTER},status="ok"}}[{R}])'
    red_at = lambda v: [{"color": "green", "value": None}, {"color": "red", "value": v}]  # noqa: E731
    panels = [
        stat(1, "Requests / s", 0, f"sum(rate(llm_requests_total{{{FILTER}}}[{R}]))", "reqps"),
        stat(2, "Error ratio", 4,
             f'sum(rate(llm_requests_total{{{FILTER},status="error"}}[{R}])) / sum(rate(llm_requests_total{{{FILTER}}}[{R}]))',
             "percentunit", red_at(0.05), "Share of LLM calls that failed. Alert fires above 5%."),
        stat(3, "p95 latency (ok)", 8, f"histogram_quantile(0.95, sum by (le) ({ok}))", "s", red_at(3),
             "95% of successful calls finish faster than this. Alert fires above 3 s."),
        stat(4, "Budget used today", 12, "llm_daily_spend_usd / llm_daily_budget_usd", "percentunit",
             [{"color": "green", "value": None}, {"color": "orange", "value": 0.8}, {"color": "red", "value": 1}],
             "Share of the daily spending cap already used."),
        stat(5, "Spend today", 16, "llm_daily_spend_usd", "currencyUSD"),
        stat(6, "Firing alerts", 20, 'count(ALERTS{alertstate="firing"}) or vector(0)', "none",
             [{"color": "green", "value": None}, {"color": "red", "value": 1}]),
        series(7, "Requests / s by outcome", 0, 4, 12,
               [target(f"sum by (status) (rate(llm_requests_total{{{FILTER}}}[{R}]))", "{{status}}")], "reqps",
               stack=True),
        series(8, "Latency of successful calls", 12, 4, 12,
               [target(f"histogram_quantile({q}, sum by (le) ({ok}))", f"p{int(q * 100)}", ref)
                for q, ref in ((0.5, "A"), (0.95, "B"), (0.99, "C"))], "s",
               "Quantiles estimated from histogram buckets, so precision depends on the bucket edges."),
        series(9, "Tokens / s", 0, 12, 8,
               [target(f"sum by (direction) (rate(llm_tokens_total{{{FILTER}}}[{R}]))", "{{direction}}")], "short"),
        series(10, "Cost per hour by model", 8, 12, 8,
               [target(f"sum by (model) (rate(llm_cost_usd_total{{{FILTER}}}[{R}])) * 3600", "{{model}}")],
               "currencyUSD", "Current spend rate, scaled to one hour."),
        series(11, "Calls in flight", 16, 12, 8, [target("sum(llm_inflight_requests)", "in flight")], "short",
               "Climbs when the provider slows down: requests pile up."),
        series(12, "HTTP requests by route and status", 0, 20, 24,
               [target(f"sum by (route, code) (rate(http_requests_total[{R}]))", "{{route}} {{code}}")], "reqps"),
    ]  # fmt: skip
    return {
        "uid": "llm-overview",
        "title": "LLM service overview",
        "tags": ["llm", "generated"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "refresh": "5s",
        "time": {"from": "now-15m", "to": "now"},
        "editable": False,
        "templating": {"list": [{
            "name": "model", "label": "Model", "type": "query", "datasource": DS,
            "query": {"query": "label_values(llm_requests_total, model)", "refId": "model"},
            "refresh": 2, "multi": True, "includeAll": True, "allValue": ".*",
            "current": {"selected": True, "text": "All", "value": "$__all"},
        }]},
        "annotations": {"list": []},
        "panels": panels,
    }  # fmt: skip


def render() -> str:
    return json.dumps(dashboard(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    text = render()
    if "--check" in sys.argv:
        sys.exit(0 if OUT.exists() and OUT.read_text() == text else 1)
    OUT.write_text(text)
    print(f"wrote {OUT}")
