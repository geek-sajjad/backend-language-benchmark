"""Hosts (local shell or SSH), the stack container on the app host, and the database."""
import json
import pathlib
import shlex
import subprocess
import time
import tomllib
import urllib.request

from .util import REPO, log


class CommandError(RuntimeError):
    pass


class Host:
    def __init__(self, name, ip, local, user, repo_dir):
        self.name, self.ip, self.local, self.user, self.repo_dir = name, ip, local, user, repo_dir

    def _argv(self, cmd):
        if self.local:
            return ["bash", "-c", cmd]
        return ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=10", f"{self.user}@{self.ip}",
                f"cd {shlex.quote(self.repo_dir)} && {cmd}"]

    def sh(self, cmd, check=True, timeout=600):
        p = subprocess.run(self._argv(cmd), cwd=REPO if self.local else None,
                           capture_output=True, text=True, timeout=timeout)
        if check and p.returncode != 0:
            raise CommandError(f"[{self.name}] `{cmd}` exited {p.returncode}: {p.stderr.strip()[-2000:]}")
        return p.stdout

    def popen(self, cmd):
        return subprocess.Popen(self._argv(cmd), cwd=REPO if self.local else None,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)


class Cluster:
    """runner/hosts.toml (written by infra/host-setup/deploy.sh). Missing file = local mode."""

    def __init__(self, hosts_file=None):
        path = pathlib.Path(hosts_file or REPO / "runner" / "hosts.toml")
        data = tomllib.loads(path.read_text()) if path.exists() else {"mode": "local"}
        self.mode = data.get("mode", "local")
        self.local = self.mode == "local"
        self.prometheus_url = data.get("prometheus_url", "http://127.0.0.1:9090")
        self.net_iface = data.get("net_iface", "ens5")
        user = data.get("ssh_user", "ubuntu")
        repo = data.get("repo_dir", str(REPO))
        hosts = data.get("hosts", {})
        self.hosts = {}
        for name in ("app", "db", "loadgen"):
            ip = hosts.get(name, {}).get("private_ip", "127.0.0.1")
            # The runner itself runs on loadgen, so loadgen commands are always local.
            self.hosts[name] = Host(name, ip, self.local or name == "loadgen", user, repo if not self.local else str(REPO))
        self.app, self.db, self.loadgen = self.hosts["app"], self.hosts["db"], self.hosts["loadgen"]

    @property
    def base_url(self):
        return f"http://{self.app.ip}:8080"

    def ethtool(self):
        """allowance_exceeded counters per host; empty in local mode (no ENA interface)."""
        if self.local:
            return {}
        out = {}
        for name, host in self.hosts.items():
            text = host.sh(f"ethtool -S {self.net_iface} | grep allowance_exceeded || true", check=False)
            out[name] = {k.strip(): int(v) for k, v in
                         (line.split(":", 1) for line in text.splitlines() if ":" in line)}
        return out


class Stack:
    """The stack under test: one compose project on the app host, container name `app`."""

    def __init__(self, cluster, name):
        self.c, self.name = cluster, name
        self.compose = f"stacks/{name}/compose.yml"

    def exists(self):
        return (REPO / self.compose).exists()

    def _env(self):
        db = self.c.db.ip
        return f"DB_HOST={db} DOWNSTREAM_URL=http://{db}:9000"

    def up(self):
        # On AWS the images are built in advance (infra/host-setup/build-images.sh).
        build = "" if self.c.local else " --no-build"
        self.c.app.sh(f"{self._env()} docker compose -f {self.compose} up -d --force-recreate{build}", timeout=900)

    def down(self):
        self.c.app.sh(f"docker compose -f {self.compose} down -t 10 --remove-orphans", check=False)

    def restart(self):
        self.down()
        self.up()
        return self.wait_healthy()

    def wait_healthy(self, timeout=300):
        start = time.time()
        while time.time() - start < timeout:
            try:
                with urllib.request.urlopen(self.c.base_url + "/health", timeout=2) as r:
                    if r.status == 200:
                        return round(time.time() - start, 2)
            except OSError:
                pass
            state = self.state()
            if state and not state.get("Running") and state.get("Status") == "exited":
                raise CommandError(f"{self.name} exited during startup (exit {state.get('ExitCode')})")
            time.sleep(0.25)
        raise CommandError(f"{self.name} not healthy after {timeout} s")

    def state(self):
        out = self.c.app.sh("docker inspect app --format '{{json .State}}'", check=False).strip()
        return json.loads(out) if out else None

    def image(self):
        return self.c.app.sh("docker inspect app --format '{{.Image}}'", check=False).strip()

    def logs(self):
        return self.c.app.sh("docker logs --tail 2000 app 2>&1", check=False)

    def cpu_cores(self):
        return int(self.c.app.sh("nproc").strip())

    def docker_cpu_fraction(self, cores):
        """Fallback when Prometheus is not running (local dev)."""
        out = self.c.app.sh("docker stats --no-stream --format '{{.CPUPerc}}' app", check=False).strip()
        try:
            return float(out.rstrip("%")) / 100 / cores
        except ValueError:
            return None

    def memory_sampler(self, seconds):
        """Samples the container cgroup's anon + shmem bytes once per second (09 R2).
        Counts each page once (no double counting across processes) and includes shared
        memory such as PHP's OPcache segment, which cAdvisor's RSS leaves out."""
        script = (
            "pid=$(docker inspect -f '{{.State.Pid}}' app); "
            "f=/sys/fs/cgroup$(cut -d: -f3 /proc/$pid/cgroup)/memory.stat; "
            f"for i in $(seq {int(seconds)}); do "
            "awk '/^anon /{a=$2} /^shmem /{s=$2} END{print a+s}' $f; sleep 1; done"
        )
        return self.c.app.popen(script)


class Database:
    def __init__(self, cluster):
        self.c = cluster

    def psql(self, sql, db="bench"):
        return self.c.db.sh(f"docker exec bench-db psql -X -At -U bench -d {db} -c {shlex.quote(sql)}").strip()

    def reset(self):
        """Recreate bench from bench_template, VACUUM ANALYZE, pg_prewarm (db/reset.sql)."""
        self.c.db.sh("docker exec -i bench-db psql -X -q -U bench -d postgres -v ON_ERROR_STOP=1 < db/reset.sql",
                     timeout=900)

    def active_app_queries(self):
        return int(self.psql("SELECT count(*) FROM pg_stat_activity WHERE datname = 'bench' "
                             "AND state = 'active' AND backend_type = 'client backend' "
                             "AND pid <> pg_backend_pid() "
                             "AND application_name <> 'postgres_exporter'"))

    def checkpoint(self):
        self.psql("CHECKPOINT")

    def version(self):
        return self.psql("SHOW server_version")


def require(cond, msg):
    if not cond:
        log(f"check failed: {msg}")
    return bool(cond)
