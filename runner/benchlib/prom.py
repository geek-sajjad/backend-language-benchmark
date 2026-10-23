"""Prometheus HTTP API queries for a step window (expressions documented in monitoring/README.md)."""
import json
import time
import urllib.parse
import urllib.request


class Prom:
    def __init__(self, url):
        self.url = url.rstrip("/")
        self._available = None

    def available(self):
        if self._available is None:
            try:
                with urllib.request.urlopen(self.url + "/-/ready", timeout=2) as r:
                    self._available = r.status == 200
            except OSError:
                self._available = False
        return self._available

    def query(self, expr, at=None):
        """Instant query; first sample's value as float, or None."""
        if not self.available():
            return None
        params = {"query": expr}
        if at is not None:
            params["time"] = f"{at:.3f}"
        try:
            with urllib.request.urlopen(f"{self.url}/api/v1/query?{urllib.parse.urlencode(params)}", timeout=10) as r:
                result = json.loads(r.read())["data"]["result"]
        except (OSError, KeyError, ValueError):
            return None
        if not result:
            return None
        return float(result[0]["value"][1])

    def window(self, end, seconds):
        """Metrics over [end - seconds, end]. Called a few seconds after the window so the last
        1 s scrapes have landed."""
        w = f"{int(seconds)}s"
        q = lambda e: self.query(e, at=end)
        host_cpu = lambda h: q(f'1 - avg(rate(node_cpu_seconds_total{{job="node",host="{h}",mode="idle"}}[{w}]))')
        rss = q(f'max_over_time(container_memory_rss{{name="app"}}[{w}])')
        return {
            "app_cpu_cores": q(f'avg_over_time(sum(rate(container_cpu_usage_seconds_total{{name="app"}}[5s]))[{w}:1s])'),
            "app_rss_mb": round(rss / 2**20, 1) if rss is not None else None,
            "app_host_cpu": host_cpu("app"),
            "db_cpu": host_cpu("db"),
            "loadgen_cpu": host_cpu("loadgen"),
            "pg_tps": q(f'sum(rate(pg_stat_database_xact_commit{{datname="bench"}}[{w}]))'),
        }

    def counter_delta(self, metric, testid, start, end):
        """Increase of a k6 counter for one run between start and end (summed over k6 processes).
        Not increase(): a series that first appears with a non-zero value (drops in the first
        second) would be missed. 0 if the run has no such series; None without Prometheus or on
        any query error (the caller then falls back to k6's own total)."""
        if not self.available():
            return None
        expr = f'sum(last_over_time({metric}{{testid="{testid}"}}[10m]))'
        values = []
        for at in (start, end):
            params = urllib.parse.urlencode({"query": expr, "time": f"{at:.3f}"})
            try:
                with urllib.request.urlopen(f"{self.url}/api/v1/query?{params}", timeout=10) as r:
                    body = json.loads(r.read())
                result = body["data"]["result"]
            except (OSError, KeyError, ValueError):
                return None
            if body.get("status") != "success":
                return None
            values.append(float(result[0]["value"][1]) if result else 0.0)
        return max(0.0, values[1] - values[0])

    def app_cpu_now(self):
        return self.query('sum(rate(container_cpu_usage_seconds_total{name="app"}[5s]))', at=time.time())
