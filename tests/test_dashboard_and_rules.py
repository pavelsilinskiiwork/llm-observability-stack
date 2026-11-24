"""The dashboard and the Prometheus rules are code: they get tests like code."""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from prometheus_client import CollectorRegistry

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "grafana"))
import build_dashboard  # noqa: E402

from llm_obs.metrics import DailySpend, Metrics  # noqa: E402

DASHBOARD = ROOT / "grafana" / "dashboards" / "llm-overview.json"
RULES = ROOT / "prometheus" / "rules" / "llm.yml"


def exposed_metric_names():
    registry = CollectorRegistry()
    Metrics(registry, 1.0, "t", DailySpend())
    names = set()
    for family in registry.collect():
        names.add(family.name)
        for sample in family.samples:
            names.add(sample.name)
    # a histogram exposes _bucket/_sum/_count; counters expose _total; a series may be queried by either
    return names | {n + "_bucket" for n in names} | {n + "_total" for n in names}


def metric_tokens(expr: str):
    expr = re.sub(r'"[^"]*"', '""', expr)  # drop label values
    expr = re.sub(r"\{[^}]*\}", "", expr)  # drop label selectors
    expr = re.sub(r"\[[^\]]*\]", "", expr)  # drop range selectors such as [$__rate_interval]
    expr = re.sub(r"\b(by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", "", expr)
    return {t for t in re.findall(r"[a-zA-Z_:][a-zA-Z0-9_:]*", expr) if "_" in t or ":" in t}


def test_committed_dashboard_matches_the_generator():
    assert DASHBOARD.read_text() == build_dashboard.render(), "run: python grafana/build_dashboard.py"


def test_dashboard_is_valid_json_with_unique_panel_ids_and_a_stable_uid():
    d = json.loads(DASHBOARD.read_text())
    ids = [p["id"] for p in d["panels"]]
    assert len(ids) == len(set(ids)) and d["uid"] == "llm-overview"


def test_panels_do_not_overlap_and_fit_the_24_column_grid():
    cells = set()
    for p in json.loads(DASHBOARD.read_text())["panels"]:
        g = p["gridPos"]
        assert g["x"] + g["w"] <= 24
        for x in range(g["x"], g["x"] + g["w"]):
            for y in range(g["y"], g["y"] + g["h"]):
                assert (x, y) not in cells, f"panel {p['title']!r} overlaps another"
                cells.add((x, y))


def test_every_panel_uses_the_provisioned_datasource_uid():
    d = json.loads(DASHBOARD.read_text())
    ds = yaml.safe_load((ROOT / "grafana/provisioning/datasources/prometheus.yml").read_text())["datasources"][0]
    assert ds["uid"] == "prometheus"
    for p in d["panels"]:
        assert p["datasource"]["uid"] == ds["uid"]
        assert all(t["datasource"]["uid"] == ds["uid"] for t in p["targets"])


def test_every_metric_in_every_query_is_actually_exposed_by_the_service():
    known = exposed_metric_names() | {"up", "ALERTS"}
    recorded = {r["record"] for g in yaml.safe_load(RULES.read_text())["groups"] for r in g["rules"] if "record" in r}
    for p in json.loads(DASHBOARD.read_text())["panels"]:
        for t in p["targets"]:
            for token in metric_tokens(t["expr"]):
                if token in {"vector", "count", "sum", "rate", "histogram_quantile", "or", "by", "le"}:
                    continue
                assert token in known | recorded, f"{p['title']!r}: unknown metric {token!r} in {t['expr']}"


def test_alert_rules_only_reference_exposed_or_recorded_metrics():
    known = exposed_metric_names() | {"up"}
    groups = yaml.safe_load(RULES.read_text())["groups"]
    recorded = {r["record"] for g in groups for r in g["rules"] if "record" in r}
    for g in groups:
        for r in g["rules"]:
            for token in metric_tokens(r["expr"]):
                if token in {"histogram_quantile"}:
                    continue
                assert token in known | recorded, f"{r.get('alert') or r.get('record')}: unknown metric {token!r}"


def test_every_alert_has_severity_summary_and_description():
    for g in yaml.safe_load(RULES.read_text())["groups"]:
        for r in g["rules"]:
            if "alert" in r:
                assert r["labels"]["severity"] in {"warning", "critical"}
                assert r["annotations"]["summary"] and r["annotations"]["description"], r["alert"]
                assert r.get("for"), f"{r['alert']} has no 'for': it would fire on a single bad sample"


def test_prometheus_scrapes_the_app_and_loads_the_rules():
    cfg = yaml.safe_load((ROOT / "prometheus/prometheus.yml").read_text())
    assert cfg["scrape_configs"][0]["static_configs"][0]["targets"] == ["app:8000"]
    assert cfg["rule_files"] == ["/etc/prometheus/rules/*.yml"]


def test_compose_mounts_what_the_configs_expect():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())["services"]
    assert set(compose) == {"app", "prometheus", "grafana"}
    assert any("prometheus.yml" in v for v in compose["prometheus"]["volumes"])
    assert any(v.endswith("/etc/grafana/provisioning:ro") for v in compose["grafana"]["volumes"])
    for service in compose.values():  # nothing is exposed beyond the local machine
        assert all(p.startswith("127.0.0.1:") for p in service["ports"])


@pytest.mark.skipif(shutil.which("promtool") is None, reason="promtool is not installed")
def test_promtool_checks_rules_and_runs_the_alert_unit_tests():
    check = subprocess.run(["promtool", "check", "rules", str(RULES)], capture_output=True, text=True)
    assert check.returncode == 0, check.stdout + check.stderr
    tests = subprocess.run(
        ["promtool", "test", "rules", str(ROOT / "prometheus/rules/tests/llm_test.yml")], capture_output=True, text=True
    )
    assert tests.returncode == 0, tests.stdout + tests.stderr
