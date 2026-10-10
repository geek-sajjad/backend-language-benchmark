#!/usr/bin/env python3
"""Generate the Grafana dashboards in monitoring/grafana/dashboards/ (stdlib only).

    python3 monitoring/grafana/build_dashboards.py

Edit this file, rerun it, and commit both the script and the generated JSON. Grafana
re-reads the directory every 30 s, so a running Grafana picks up the change by itself.

Metric sources (see monitoring/README.md):
  k6_*             k6 --out experimental-prometheus-rw, trends as native histograms.
                   Labels: testid, stack, test, phase, name, method, status, scenario,
                   expected_response. k6_vus / k6_vus_max only carry the global tags.
                   k6_dropped_iterations_total only exists once k6 dropped something.
  node_*           node_exporter, job="node", host=app|db|loadgen
  container_*      cAdvisor on app, job="cadvisor", the stack container is name="app"
  pg_*             postgres_exporter, job="postgres", host="db"
"""
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "dashboards"
DS = {"type": "prometheus", "uid": "prometheus"}

# Interfaces excluded from host network panels (host networking: no veths carry traffic).
NETDEV = 'device!~"lo|veth.*|docker.*|br-.*"'
# All ENA *_allowance_exceeded counters (bw_in, bw_out, pps, conntrack, linklocal).
# Subquery + label_replace keeps the counter's name, which increase() would drop.
ALLOWANCE = (
    'sum by (host, counter) (increase(label_replace('
    '{{__name__=~"node_ethtool_.+_allowance_exceeded",job="node"{sel}}}, '
    '"counter", "$1", "__name__", "node_ethtool_(.+)_allowance_exceeded")[{win}:1s]))'
)


# --------------------------------------------------------------------------- helpers

def target(expr, legend="", ref=None, instant=False):
    t = {"datasource": DS, "expr": expr, "legendFormat": legend or "__auto", "range": not instant,
         "instant": instant}
    if ref:
        t["refId"] = ref
    return t


def thresholds(*steps):
    """steps: (value, color) pairs above the green base."""
    return {"mode": "absolute",
            "steps": [{"color": "green", "value": None}] + [{"color": c, "value": v} for v, c in steps]}


def timeseries(title, targets, unit="short", desc="", w=12, h=8, min_=None, max_=None,
               threshold_lines=(), stack=False, legend_calcs=("mean", "max", "lastNotNull")):
    custom = {"drawStyle": "line", "lineWidth": 1, "fillOpacity": 10 if not stack else 40,
              "showPoints": "never", "spanNulls": False,
              "stacking": {"mode": "normal" if stack else "none", "group": "A"},
              "thresholdsStyle": {"mode": "dashed" if threshold_lines else "off"}}
    defaults = {"unit": unit, "custom": custom, "color": {"mode": "palette-classic"}}
    if threshold_lines:
        defaults["thresholds"] = thresholds(*threshold_lines)
    if min_ is not None:
        defaults["min"] = min_
    if max_ is not None:
        defaults["max"] = max_
    return {"type": "timeseries", "title": title, "description": desc, "datasource": DS,
            "targets": targets, "_w": w, "_h": h,
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"legend": {"displayMode": "table", "placement": "bottom",
                                   "calcs": list(legend_calcs)},
                        "tooltip": {"mode": "multi", "sort": "desc"}}}


def stat(title, expr, unit="short", desc="", w=4, h=4, steps=(), decimals=None, legend="", no_value=None):
    defaults = {"unit": unit, "thresholds": thresholds(*steps), "color": {"mode": "thresholds"}}
    if no_value:
        defaults["noValue"] = no_value
    if decimals is not None:
        defaults["decimals"] = decimals
    return {"type": "stat", "title": title, "description": desc, "datasource": DS,
            "targets": [target(expr, legend, instant=True)], "_w": w, "_h": h,
            "fieldConfig": {"defaults": defaults, "overrides": []},
            "options": {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "background", "graphMode": "none", "textMode": "auto",
                        "justifyMode": "center"}}


def row(title):
    return {"type": "row", "title": title, "collapsed": False, "panels": [], "_w": 24, "_h": 1}


def layout(panels):
    """Assign ids and left-to-right, top-to-bottom gridPos (24 columns)."""
    x = y = row_h = 0
    for i, p in enumerate(panels, start=1):
        w, h = p.pop("_w"), p.pop("_h")
        if x + w > 24 or p["type"] == "row":
            x, y, row_h = 0, y + row_h, 0
        p["id"] = i
        p["gridPos"] = {"x": x, "y": y, "w": w, "h": h}
        if p["type"] == "row":
            y, row_h = y + h, 0
            continue
        x += w
        row_h = max(row_h, h)
        for j, t in enumerate(p.get("targets", [])):
            t.setdefault("refId", chr(ord("A") + j))
    return panels


def query_var(name, label, query, include_all=True, multi=True):
    return {"type": "query", "name": name, "label": label, "datasource": DS,
            "query": {"qryType": 1, "query": query, "refId": "PrometheusVariableQueryEditor-VariableQuery"},
            "definition": query, "refresh": 2, "sort": 1, "includeAll": include_all, "multi": multi,
            "allValue": ".*", "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
            "options": [], "regex": "", "hide": 0}


def window_var(default="10s"):
    opts = ["5s", "10s", "30s", "1m"]
    return {"type": "custom", "name": "window", "label": "Rate window",
            "description": "Range for rate()/increase(). Must cover >= 2 k6 remote-write pushes "
                           "(K6_PROMETHEUS_RW_PUSH_INTERVAL, the runner uses 1s; k6 default 5s).",
            "query": ",".join(opts), "includeAll": False, "multi": False, "hide": 0,
            "current": {"selected": True, "text": default, "value": default},
            "options": [{"selected": o == default, "text": o, "value": o} for o in opts]}


def host_var():
    return {"type": "custom", "name": "host", "label": "Host", "query": "app,db,loadgen",
            "includeAll": True, "multi": True, "allValue": "app|db|loadgen", "hide": 0,
            "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
            "options": []}


def dashboard(uid, title, desc, panels, variables, tags=("benchmark",)):
    return {"uid": uid, "title": title, "description": desc, "tags": list(tags),
            "editable": False, "graphTooltip": 1, "timezone": "browser",
            "refresh": "5s", "time": {"from": "now-15m", "to": "now"},
            "timepicker": {"refresh_intervals": ["5s", "10s", "30s", "1m", "5m"]},
            "schemaVersion": 41, "version": 1, "fiscalYearStartMonth": 0, "liveNow": False,
            "links": [{"type": "dashboards", "tags": ["benchmark"], "asDropdown": False,
                       "title": "Benchmark", "includeVars": False, "keepTime": True}],
            "annotations": {"list": []},
            "templating": {"list": variables},
            "panels": layout(panels)}


# --------------------------------------------------------------------------- Run overview

K6 = 'testid=~"$testid",stack=~"$stack",test=~"$test",phase=~"$phase"'


def lat(q, by=""):
    agg = f"sum by (name) " if by else "sum "
    return f"histogram_quantile({q}, {agg}(rate(k6_http_req_duration_seconds{{{K6}}}[$window])))"


def run_overview():
    reqs = f"sum(rate(k6_http_reqs_total{{{K6}}}[$window]))"
    errs = (f'(sum(rate(k6_http_reqs_total{{{K6},expected_response="false"}}[$window])) or vector(0))'
            f" / {reqs}")
    iters = f"sum(rate(k6_iterations_total{{{K6}}}[$window]))"
    dropped_rate = f"(sum(rate(k6_dropped_iterations_total{{{K6}}}[$window])) or vector(0))"
    panels = [
        row("Now"),
        stat("Achieved req/s", reqs, "reqps", w=4, decimals=0),
        stat("p95", lat(0.95), "s", "SLO: p95 < 100 ms", w=4, steps=((0.1, "red"),)),
        stat("p99", lat(0.99), "s", "SLO: p99 < 250 ms", w=4, steps=((0.25, "red"),)),
        stat("Error rate", errs, "percentunit", "SLO: < 1% (non-expected responses)", w=4,
             steps=((0.01, "red"),), decimals=2),
        stat("Dropped iterations (time range)",
             f"sum(increase(k6_dropped_iterations_total{{{K6}}}[$__range])) or vector(0)", "short",
             "Validity: must be 0 in the measure window, otherwise k6 couldn't keep up", w=4,
             steps=((1, "red"),), decimals=0),
        stat("VUs", f'sum(k6_vus{{testid=~"$testid",stack=~"$stack",test=~"$test",phase=~"$phase"}})',
             "short", w=4, decimals=0),
        row("Throughput"),
        timeseries("Offered vs achieved", [
            target(f"{iters} + {dropped_rate}", "offered (iterations/s)"),
            target(iters, "achieved (iterations/s)"),
            target(reqs, "HTTP requests/s"),
        ], "short", "Offered = completed + dropped iterations (constant-arrival-rate). "
                    "A gap between offered and achieved means k6 dropped iterations.", w=12),
        timeseries("Requests/s by phase",
                   [target(f"sum by (phase) (rate(k6_http_reqs_total{{{K6}}}[$window]))", "{{phase}}")],
                   "reqps", "settle | measure | smoke | warmup | probe | common | recovery", w=12),
        row("Latency (k6 native histogram, approximate)"),
        timeseries("Latency p50 / p95 / p99", [
            target(lat(0.5), "p50"), target(lat(0.95), "p95"), target(lat(0.99), "p99"),
        ], "s", "Live view only; scoring uses k6's exact end-of-test summary. "
                "Dashed lines: SLO p95 100 ms, p99 250 ms.", w=12,
            threshold_lines=((0.1, "orange"), (0.25, "red")), min_=0),
        timeseries("p95 / p99 by endpoint", [
            target(lat(0.95, by=True), "p95 {{name}}"), target(lat(0.99, by=True), "p99 {{name}}"),
        ], "s", w=12, min_=0),
        row("Errors and generator health"),
        timeseries("Error rate", [target(errs, "error rate")], "percentunit",
                   "Share of responses k6 did not expect (non-2xx, timeouts, connection errors). "
                   "Dashed line: SLO 1%.", w=8, threshold_lines=((0.01, "red"),), min_=0),
        timeseries("Responses/s by status",
                   [target(f"sum by (status) (rate(k6_http_reqs_total{{{K6}}}[$window]))", "{{status}}")],
                   "reqps", "status 0 = no response (timeout or connection error)", w=8),
        timeseries("Dropped iterations/s", [target(dropped_rate, "dropped/s")], "short",
                   "Validity: any drop in the measure window makes the step invalid.", w=8,
                   threshold_lines=((0.000001, "red"),), min_=0),
        timeseries("VUs", [
            target(f'sum(k6_vus{{testid=~"$testid",stack=~"$stack",test=~"$test",phase=~"$phase"}})', "active VUs"),
            target(f'sum(k6_vus_max{{testid=~"$testid",stack=~"$stack",test=~"$test",phase=~"$phase"}})', "allocated VUs"),
        ], "short", "Active VUs approaching allocated VUs means the app is slowing down "
                    "(constant-arrival-rate needs more concurrency).", w=12),
        timeseries("Loadgen CPU busy", [
            target('1 - avg(rate(node_cpu_seconds_total{job="node",host="loadgen",mode="idle"}[$window]))', "loadgen"),
        ], "percentunit", "Validity: < 70% over the step.", w=12,
            threshold_lines=((0.7, "red"),), min_=0, max_=1),
    ]
    variables = [
        query_var("stack", "Stack", "label_values(k6_vus, stack)"),
        query_var("test", "Test", 'label_values(k6_vus{stack=~"$stack"}, test)'),
        query_var("testid", "Run (testid)", 'label_values(k6_vus{stack=~"$stack",test=~"$test"}, testid)'),
        query_var("phase", "Phase", 'label_values(k6_vus{testid=~"$testid"}, phase)'),
        window_var(),
    ]
    return dashboard("bench-run-overview", "Run overview",
                     "Live k6 metrics for the current step: offered vs achieved rate, latency, errors, "
                     "dropped iterations.", panels, variables)


# --------------------------------------------------------------------------- App host

APP = 'job="node",host="app"'
CTR = 'name="app"'


def app_host():
    panels = [
        row("CPU"),
        timeseries("App host CPU per core",
                   [target(f'1 - rate(node_cpu_seconds_total{{{APP},mode="idle"}}[$window])', "cpu {{cpu}}")],
                   "percentunit", "Busy fraction of every core on the app host.", w=12, min_=0, max_=1,
                   legend_calcs=("mean", "max")),
        timeseries("App container CPU (cores)", [
            target(f"sum(rate(container_cpu_usage_seconds_total{{{CTR}}}[$window]))", "total"),
            target(f"sum(rate(container_cpu_user_seconds_total{{{CTR}}}[$window]))", "user"),
            target(f"sum(rate(container_cpu_system_seconds_total{{{CTR}}}[$window]))", "system"),
            target('count(node_cpu_seconds_total{job="node",host="app",mode="idle"})', "cores on host"),
        ], "short", "Cores used by the stack container (cAdvisor). The runner's recovery check "
                    "waits for < 0.05.", w=12, min_=0),
        timeseries("App host CPU by mode",
                   [target(f'sum by (mode) (rate(node_cpu_seconds_total{{{APP},mode!="idle"}}[$window]))', "{{mode}}")],
                   "short", "Cores busy per mode (softirq rising with rate = network stack cost).",
                   w=12, stack=True, min_=0),
        timeseries("CPU pressure (PSI)", [
            target(f"rate(container_pressure_cpu_waiting_seconds_total{{{CTR}}}[$window])", "app waiting"),
            target(f"rate(container_pressure_cpu_stalled_seconds_total{{{CTR}}}[$window])", "app stalled"),
        ], "percentunit", "Share of time tasks in the container waited for a CPU.", w=12, min_=0),
        row("Memory"),
        timeseries("App container memory", [
            target(f"container_memory_rss{{{CTR}}}", "rss (anon)"),
            target(f"container_memory_working_set_bytes{{{CTR}}}", "working set"),
            target(f"container_memory_usage_bytes{{{CTR}}}", "usage (incl. page cache)"),
            target(f"container_memory_mapped_file{{{CTR}}}", "mapped file"),
        ], "bytes", "rss = cgroup v2 'anon' (the scored memory metric, see 09-review-findings R2: "
                    "shared memory such as OPcache is 'shmem' and not included here).", w=12, min_=0),
        timeseries("Threads, processes, FDs, sockets", [
            target(f"container_threads{{{CTR}}}", "threads"),
            target(f"container_processes{{{CTR}}}", "processes"),
            target(f"container_file_descriptors{{{CTR}}}", "file descriptors"),
            target(f"container_sockets{{{CTR}}}", "sockets"),
        ], "short", w=12, min_=0),
        timeseries("App host memory", [
            target(f'node_memory_MemTotal_bytes{{{APP}}} - node_memory_MemAvailable_bytes{{{APP}}}', "used"),
            target(f'node_memory_MemTotal_bytes{{{APP}}}', "total"),
        ], "bytes", w=12, min_=0),
        timeseries("OOM events", [target(f"sum(increase(container_oom_events_total{{{CTR}}}[$window]))", "oom")],
                   "short", w=12, min_=0),
        row("Network"),
        timeseries("App host network throughput", [
            target(f'sum(rate(node_network_receive_bytes_total{{{APP},{NETDEV}}}[$window])) * 8', "rx"),
            target(f'sum(rate(node_network_transmit_bytes_total{{{APP},{NETDEV}}}[$window])) * 8', "tx"),
        ], "bps", w=8, min_=0),
        timeseries("App host packets/s", [
            target(f'sum(rate(node_network_receive_packets_total{{{APP},{NETDEV}}}[$window]))', "rx"),
            target(f'sum(rate(node_network_transmit_packets_total{{{APP},{NETDEV}}}[$window]))', "tx"),
        ], "pps", w=8, min_=0),
        timeseries("TCP", [
            target(f'node_netstat_Tcp_CurrEstab{{{APP}}}', "established"),
            target(f'node_sockstat_TCP_tw{{{APP}}}', "time-wait"),
            target(f'rate(node_netstat_TcpExt_ListenDrops{{{APP}}}[$window])', "listen drops/s"),
            target(f'rate(node_netstat_Tcp_RetransSegs{{{APP}}}[$window])', "retransmits/s"),
        ], "short", w=8, min_=0),
        timeseries("ENA allowance exceeded (app)",
                   [target(ALLOWANCE.format(sel=',host="app"', win="$window"), "{{counter}}")],
                   "short", "Increase of the ENA *_allowance_exceeded counters. Any increase makes "
                            "the step invalid. No data on non-ENA NICs (local dev).", w=24, min_=0),
    ]
    return dashboard("bench-app-host", "App host",
                     "The machine under test: CPU per core, container CPU and RSS, threads, network.",
                     panels, [window_var()])


# --------------------------------------------------------------------------- Headroom

def busy(host, win="$window"):
    return f'1 - avg(rate(node_cpu_seconds_total{{job="node",host="{host}",mode="idle"}}[{win}]))'


PG = 'job="postgres",datname="bench"'


def headroom():
    cpu_desc = "Validity: average over the measure window must stay < 70%."
    panels = [
        row("Validity checks (the app must be the bottleneck)"),
        stat("db CPU (30s avg)", busy("db", "30s"), "percentunit", cpu_desc, w=6,
             steps=((0.6, "orange"), (0.7, "red")), decimals=1),
        stat("loadgen CPU (30s avg)", busy("loadgen", "30s"), "percentunit", cpu_desc, w=6,
             steps=((0.6, "orange"), (0.7, "red")), decimals=1),
        stat("app CPU (30s avg)", busy("app", "30s"), "percentunit",
             "Should be the busy one.", w=6, decimals=1),
        stat("Allowance exceeded (time range)",
             ALLOWANCE.format(sel="", win="$__range").replace("sum by (host, counter)", "sum"),
             "short", "Sum of all ENA *_allowance_exceeded increases on all hosts. Must be 0. "
             "'no ENA counters' means node_exporter found none (non-ENA NIC, e.g. local dev).",
             w=6, steps=((1, "red"),), decimals=0, no_value="no ENA counters"),
        row("Host CPU"),
        timeseries("db host CPU busy", [target(busy("db"), "db")], "percentunit",
                   "Postgres + downstream mock. " + cpu_desc, w=8,
                   threshold_lines=((0.7, "red"),), min_=0, max_=1),
        timeseries("loadgen host CPU busy", [target(busy("loadgen"), "loadgen")], "percentunit",
                   "k6 + Prometheus + Grafana. " + cpu_desc, w=8,
                   threshold_lines=((0.7, "red"),), min_=0, max_=1),
        timeseries("loadgen CPU per core",
                   [target('1 - rate(node_cpu_seconds_total{job="node",host="loadgen",mode="idle"}[$window])',
                           "cpu {{cpu}}")],
                   "percentunit", "k6 is pinned to 0-13, Prometheus + Grafana to 14-15.", w=8,
                   min_=0, max_=1, legend_calcs=("mean", "max")),
        row("Postgres"),
        timeseries("Postgres TPS (bench)", [
            target(f"sum(rate(pg_stat_database_xact_commit{{{PG}}}[$window]))", "commits/s"),
            target(f"sum(rate(pg_stat_database_xact_rollback{{{PG}}}[$window]))", "rollbacks/s"),
        ], "short", w=12, min_=0),
        timeseries("Connections (bench) by state", [
            target(f'sum by (state) (pg_stat_activity_count{{{PG}}})', "{{state}}"),
            target(f'sum(pg_stat_database_numbackends{{{PG}}})', "backends"),
        ], "short", "Sessions connected to the bench DB (postgres_exporter connects to the "
                    "postgres DB, so it is not counted here).", w=12, min_=0),
        timeseries("Locks (bench) by mode",
                   [target(f'sum by (mode) (pg_locks_count{{{PG}}})', "{{mode}}")], "short", w=12, min_=0),
        timeseries("Buffer cache hit ratio (bench)", [
            target(f"sum(rate(pg_stat_database_blks_hit{{{PG}}}[$window])) / "
                   f"(sum(rate(pg_stat_database_blks_hit{{{PG}}}[$window])) + "
                   f"sum(rate(pg_stat_database_blks_read{{{PG}}}[$window])))", "hit ratio"),
        ], "percentunit", "Should stay ~100%: the dataset is prewarmed into shared_buffers.", w=12,
            min_=0, max_=1),
        timeseries("Rows/s (bench)", [
            target(f"sum(rate(pg_stat_database_tup_fetched{{{PG}}}[$window]))", "fetched"),
            target(f"sum(rate(pg_stat_database_tup_inserted{{{PG}}}[$window]))", "inserted"),
            target(f"sum(rate(pg_stat_database_tup_updated{{{PG}}}[$window]))", "updated"),
            target(f"sum(rate(pg_stat_database_tup_deleted{{{PG}}}[$window]))", "deleted"),
        ], "short", w=12, min_=0),
        timeseries("Deadlocks, temp files, longest transaction", [
            target(f"sum(increase(pg_stat_database_deadlocks{{{PG}}}[$window]))", "deadlocks"),
            target(f"sum(increase(pg_stat_database_temp_files{{{PG}}}[$window]))", "temp files"),
            target('max(pg_stat_activity_max_tx_duration{job="postgres",datname="bench"})',
                   "max tx duration (s)"),
        ], "short", w=12, min_=0),
        row("Network (all hosts)"),
        timeseries("ENA allowance exceeded",
                   [target(ALLOWANCE.format(sel=',host=~"$host"', win="$window"), "{{host}} {{counter}}")],
                   "short", "Increase of bw_in/bw_out/pps/conntrack/linklocal _allowance_exceeded "
                            "per host. Any increase makes the step invalid. No data on non-ENA NICs "
                            "(local dev).", w=24, min_=0),
        timeseries("Throughput by host", [
            target(f'sum by (host) (rate(node_network_receive_bytes_total{{job="node",host=~"$host",{NETDEV}}}[$window])) * 8', "{{host}} rx"),
            target(f'sum by (host) (rate(node_network_transmit_bytes_total{{job="node",host=~"$host",{NETDEV}}}[$window])) * 8', "{{host}} tx"),
        ], "bps", w=12, min_=0),
        timeseries("Packets/s by host", [
            target(f'sum by (host) (rate(node_network_receive_packets_total{{job="node",host=~"$host",{NETDEV}}}[$window]))', "{{host}} rx"),
            target(f'sum by (host) (rate(node_network_transmit_packets_total{{job="node",host=~"$host",{NETDEV}}}[$window]))', "{{host}} tx"),
        ], "pps", w=12, min_=0),
    ]
    return dashboard("bench-headroom", "Headroom",
                     "Proves the app was the bottleneck: db and loadgen CPU under 70%, Postgres "
                     "healthy, no network allowance exceeded.", panels, [window_var(), host_var()])


def main():
    OUT.mkdir(exist_ok=True)
    for name, build in [("run-overview", run_overview), ("app-host", app_host), ("headroom", headroom)]:
        path = OUT / f"{name}.json"
        path.write_text(json.dumps(build(), indent=2) + "\n")
        print(f"wrote {path}")


if __name__ == "__main__":
    main()
