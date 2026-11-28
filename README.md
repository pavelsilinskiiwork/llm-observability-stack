# LLM observability stack: Prometheus, Grafana, alerts

[![ci](https://github.com/pavelsilinskiiwork/llm-observability-stack/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/pavelsilinskiiwork/llm-observability-stack/actions/workflows/ci.yml)

A small LLM service instrumented with Prometheus metrics, plus everything needed to watch it: Prometheus,
Grafana with a provisioned dashboard, alert rules with unit tests, and a load script that breaks the service on
purpose so you can see the alerts fire. The model is simulated (no API key, no cost); the telemetry is the project.

```
load script ──POST /chat──▶  app (FastAPI)  ──/metrics──▶  Prometheus  ──▶  Grafana (dashboard as code)
 (normal / failing / slow)    llm_* and http_* metrics      scrapes every 5 s,    reads the same data
                                                            recording + alert rules
```

## What is measured

| Metric | Type | Labels | Answers |
| --- | --- | --- | --- |
| `llm_requests_total` | counter | provider, model, status | how many calls, how many failed |
| `llm_request_duration_seconds` | histogram | provider, model, status | how slow (p50, p95, p99) |
| `llm_tokens_total` | counter | provider, model, direction | how much is processed |
| `llm_cost_usd_total` | counter | provider, model | how much it costs |
| `llm_inflight_requests` | gauge | none | are requests piling up |
| `llm_daily_spend_usd`, `llm_daily_budget_usd` | gauges | none | how much of today's budget is gone |
| `http_requests_total`, `http_request_duration_seconds` | counter, histogram | method, route, code | the HTTP layer |
| `llm_app_info` | gauge | version | which build is running |

## Alerts

| Alert | Fires when | For | Severity |
| --- | --- | --- | --- |
| `LLMAppDown` | the service is not scraped | 30 s | critical |
| `LLMHighErrorRate` | more than 5% of calls fail (and there is traffic) | 1 min | critical |
| `LLMHighLatencyP95` | p95 of successful calls is above 3 s (and there is traffic) | 1 min | warning |
| `LLMBudgetMostlySpent` | more than 80% of today's budget is spent | 1 min | warning |
| `LLMSpendRateAboveBudget` | the current spend rate projects past the daily budget | 5 min | warning |

Shared expressions are recording rules (`llm:error_ratio:rate2m`, `llm:latency_seconds:p95_2m`, ...), so the
dashboard and the alerts cannot disagree about what "error ratio" means.

## Run it

```bash
docker compose up --build
```

- Grafana: http://127.0.0.1:3000 (user `admin`, password `admin`: local demo only, set `GRAFANA_PASSWORD` to change it).
  The "LLM service overview" dashboard is already there, in the folder "LLM".
- Prometheus: http://127.0.0.1:9090 (the *Alerts* page shows the rules)
- Traces: Grafana → Explore → *Tempo* → Search. Every `/chat` response carries an `x-trace-id` header; paste it into
  the *TraceQL* / trace-id box to open that request (a `chat` span with an `llm.generate` child).
- Service: http://127.0.0.1:8000/docs

Then generate traffic and break things (about 10 minutes):

```bash
pip install httpx
python -m llm_obs.loadgen --scenario demo --rps 5
```

The `demo` scenario runs: normal, failing (40% of calls fail), normal, slow (4x latency), normal. Watch the error
ratio and latency panels move, and the alerts go pending, then firing, then resolved.
`--scenario steady` sends normal traffic only. `--scale 0.5` halves every phase.

## Tracing (OpenTelemetry)

Metrics say *that* something is slow; a trace says *where*. Each `/chat` request produces one trace: a `chat` span
and an `llm.generate` child around the model call, exported over OTLP/HTTP to Grafana Tempo
(`OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4318`, set in `docker-compose.yml`; unset, nothing is exported).

- The model span uses the GenAI attribute names (`gen_ai.request.model`, `gen_ai.usage.input_tokens`,
  `gen_ai.usage.output_tokens`) plus `llm.cost_usd`. **The prompt text is never recorded.**
- A provider failure sets the root span to `ERROR` and records the exception.
- An incoming `traceparent` header is continued, so the service can join a trace started by a caller.
- Tempo is provisioned as a Grafana datasource next to Prometheus.

What is and is not checked: unit tests collect spans in memory and assert structure, attributes, the error path,
header propagation and that no prompt leaks. The `stack` job in CI starts Tempo, sends a request and polls Tempo's
query API for that trace id (`scripts/verify_traces.py`). Logs are not part of this; only traces and metrics.

## Dashboard as code

`grafana/build_dashboard.py` generates `grafana/dashboards/llm-overview.json`; Grafana loads it through file
provisioning (`allowUiUpdates: false`). To change the dashboard, edit the Python, run
`python grafana/build_dashboard.py`, commit both. A test fails if the JSON and the generator disagree, so a hand
edit cannot sneak in. The datasource is provisioned with a fixed `uid`, so the dashboard survives a rebuild.

## How it is checked

```bash
python -m pytest --cov=llm_obs                       # 43 tests
promtool test rules prometheus/rules/tests/llm_test.yml   # alert unit tests
python scripts/verify_queries.py                     # against a running Prometheus
```

- **Alert unit tests** (`promtool test rules`): synthetic series with known values prove each alert fires when it
  should and stays quiet when it should not, including the "errors but no traffic" case. I checked that they bite:
  raising a threshold in the rules makes them fail.
- **Dashboard tests**: valid JSON, unique panel ids, no overlapping panels, every query uses the provisioned
  datasource, and every metric named in any query or alert actually exists in the service (a misspelled metric
  fails the test instead of leaving an empty panel).
- **`verify_queries.py`** runs every dashboard query against a live Prometheus and fails on an error or an empty result.
- **CI** (`.github/workflows/ci.yml`): lint, tests on Python 3.11-3.13, generated-dashboard check, `docker compose config`,
  `promtool check` and unit tests, and a `stack` job that starts Prometheus and Tempo with the service, sends traffic, runs `verify_queries.py` and checks that a request's trace is stored in Tempo.

## Evidence

Screenshots from one `demo` run of the Compose stack (Grafana, Prometheus and the service, load at 3-5 requests/s).
The two shots taken during the failing and slow phases (2 and 4) were captured while the p95 threshold was still
2 s; it is 3 s now (see the design decisions below).

**1. Normal traffic.** Errors near 0%, p95 under the threshold, 40% of the demo budget spent after a two-minute
steady run, no alerts. The "Requests / s" tile reads 0 only because the load had just stopped.

![dashboard during normal traffic](docs/01-dashboard-normal.png)

**2. The failing phase.** 40% of calls fail: the error ratio goes red, the `error` line appears in the outcome
chart, calls in flight climb, and "Firing alerts" turns to 1.

![dashboard during the failing phase](docs/02-dashboard-failing.png)

**3. Prometheus alerts page.** `LLMHighErrorRate` and `LLMBudgetMostlySpent` are firing, the other three rules are
inactive. (The demo load spends the $1 demo budget in a few minutes; the service reports spend but does not enforce
a cap, which is what [llm-cache-ratelimit](https://github.com/pavelsilinskiiwork/llm-cache-ratelimit) is for.)

![Prometheus alerts page with two firing alerts](docs/03-alerts-firing.png)

**4. The slow phase.** Latency is 4x: p95 of successful calls reads 7.6 s, p99 reaches about 10 s, in-flight calls
pile up, and two alerts are firing while the error ratio is back to normal.

![dashboard during the slow phase](docs/04-dashboard-slow.png)

## A real run

I ran the `demo` scenario (3 requests/s, phases shortened to 80%) against the service with a real Prometheus 2.55.1 and these
exact rule files. Measured from Prometheus itself:

| Event | What happened |
| --- | --- |
| Failures begin | 18:18:15. Error ratio above 5% five seconds later; peak 40.1% |
| `LLMHighErrorRate` | pending at 18:18:25, **firing at 18:19:25**, resolved at 18:22:20 |
| Slow phase begins | median latency above 1.5 s from 18:22:20; p95 of successful calls peaked at 6.5 s (that run used a 2 s threshold; it is now 3 s, see below) |
| `LLMHighLatencyP95` | pending at 18:22:15, **firing at 18:23:15**, resolved at 18:26:00 |
| `LLMBudgetMostlySpent` | with a $1 budget and 8 requests/s: fired once spend passed 80% (the service does not enforce the cap; see [llm-cache-ratelimit](https://github.com/pavelsilinskiiwork/llm-cache-ratelimit) for that) |
| `verify_queries.py` | all 14 dashboard queries returned data |

Grafana itself was not part of that run: what was exercised is the queries Grafana sends, against a real Prometheus.
The two delays you see are by design: `for: 1m` (one bad sample should not page anyone) and the 2-minute rate window,
which is why alerts resolve a minute or two after the problem ends.

## Design decisions

| Decision | Why |
| --- | --- |
| Labels are provider, model, status, route template | every distinct label value is a new time series; no user ids, prompts or raw URLs. Unknown paths collapse into `unmatched` (tested) |
| Every expected series is created at 0 on start | a labelled counter does not exist until its first increment, so "error ratio" would be empty until the first failure and the first failure would be invisible to `rate()` |
| `llm_daily_spend_usd` is computed at scrape time | it resets at midnight UTC without a background job |
| Histogram buckets from 50 ms to 30 s | fine resolution where LLM calls live (0.25-2 s) and a tail for stuck requests; quantiles are estimates from buckets |
| Latency is measured for `ok` calls separately from `error` | fast failures would otherwise make p95 look better exactly when the service is broken |
| The p95 threshold is 3 s, not 2 s | with 30% of traffic on the slower model the normal p95 wanders between about 1.7 and 2.4 s (and bucket interpolation widens it); a threshold inside normal variation turns the panel red for no reason. The slow scenario reaches 5-7 s |
| Every alert has `for` and a traffic guard | no paging on one bad sample or on 1 error in 3 requests at 3 a.m. |
| Alert windows of 2 minutes | so a demo shows results in minutes. Production would use longer windows or multi-window burn rates |
| Ports bound to `127.0.0.1` | the stack is a local demo, nothing is exposed to the network |

## Limitations

- **No Alertmanager.** Alerts are visible in Prometheus and on the dashboard, but nothing sends an email or a Slack
  message. A real setup adds Alertmanager with routing and silences.
- The provider is simulated; latency, token counts and errors come from a generator, so the absolute numbers mean nothing.
- Metrics and traces, no logs. The trace has only two spans because the provider is simulated: a real service would add spans for retrieval, tool calls and each model call. Traces are not sampled (every request is kept), which is fine for a demo and not for production traffic.
- One process: counters and the daily spend live in memory. With several workers each exposes its own numbers and
  the daily spend would need to be aggregated (or kept in Redis).
- `/metrics` has no authentication; keep it on an internal network.
- Grafana runs with a default local password and anonymous access disabled; change the password before exposing the port.
