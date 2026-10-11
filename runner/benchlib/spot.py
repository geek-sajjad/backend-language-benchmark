"""Spot interruptions (docs/06-runner-and-results.md "Spot interruptions").

SpotWatch polls the interruption notice on every host in a background thread. When AWS announces
a stop (about 2 min ahead), or a host stops answering SSH, it trips: the running k6 process is
killed and the pair raises SpotInterruption, so nothing from that pair is scored. The campaign then
writes paused.json and exits; `resume` checks the hardware fingerprint before continuing.
"""
import subprocess
import threading
import time

from .util import log, utc_now

POLL_S = 10
SSH_LOST_POLLS = 3  # consecutive failed polls (~30 s) before a silent host counts as interrupted

# Compared on resume: any difference keeps the campaign paused.
FINGERPRINT_KEYS = ("instance_type", "instance_id", "cpu_model", "cores", "ami", "kernel", "docker")


class SpotInterruption(RuntimeError):
    def __init__(self, host, reason):
        super().__init__(f"{host}: {reason}")
        self.host, self.reason = host, reason


def imds(path, fail=False):
    """EC2 instance metadata (IMDSv2). fail=True: curl exits non-zero on 404."""
    f = "f" if fail else ""
    return ("t=$(curl -s -m 2 -X PUT http://169.254.169.254/latest/api/token "
            "-H 'X-aws-ec2-metadata-token-ttl-seconds: 60'); "
            f"curl -s{f} -m 2 -H \"X-aws-ec2-metadata-token: $t\" http://169.254.169.254/latest/meta-data/{path}")


def fingerprint(cluster):
    """Per-host hardware and software identity. boot_id changes whenever the host restarts, which
    for a stopped spot instance means it now runs on a different physical machine."""
    out = {}
    for name, h in cluster.hosts.items():
        def sh(cmd, h=h):
            try:
                return h.sh(cmd, check=False, timeout=30).strip()
            except (OSError, subprocess.SubprocessError) as e:
                return f"unavailable: {e}"
        local = cluster.local
        out[name] = {
            "private_ip": h.ip,
            "instance_type": "local" if local else sh(imds("instance-type")),
            "instance_id": "local" if local else sh(imds("instance-id")),
            "ami": "local" if local else sh(imds("ami-id")),
            "cpu_model": sh("lscpu | sed -n 's/^Model name: *//p' | head -1"),
            "cores": sh("nproc"),
            "kernel": sh("uname -r"),
            "docker": sh("docker version --format '{{.Server.Version}}'"),
            "boot_id": sh("cat /proc/sys/kernel/random/boot_id"),
        }
    return out


class SpotWatch:
    def __init__(self, cluster):
        self.cluster = cluster
        self.tripped = threading.Event()
        self.host = self.reason = None
        self._stop = threading.Event()
        self._misses = {}
        self._thread = None

    def start(self):
        if self.cluster.local:
            return self
        self._thread = threading.Thread(target=self._loop, name="spot-watch", daemon=True)
        self._thread.start()
        return self

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set() and not self.tripped.is_set():
            self.poll()
            self._stop.wait(POLL_S)

    def poll(self):
        """One round over all hosts. Returns True if tripped."""
        for name, h in self.cluster.hosts.items():
            try:
                rc, out = h.sh_rc(imds("spot/instance-action", fail=True) + " || echo none", timeout=20)
            except subprocess.TimeoutExpired:
                rc, out = 255, ""
            if rc == 255:  # ssh itself failed
                self._misses[name] = self._misses.get(name, 0) + 1
                if self._misses[name] >= SSH_LOST_POLLS:
                    self.trip(name, "ssh lost")
                continue
            self._misses[name] = 0
            out = out.strip()
            if out and out != "none":
                self.trip(name, f"spot notice {out}")
        return self.tripped.is_set()

    def trip(self, host, reason):
        if not self.tripped.is_set():
            self.host, self.reason = host, reason
            self.tripped.set()
            log(f"SPOT INTERRUPTION: {host}: {reason}")

    def check(self):
        if self.tripped.is_set():
            raise SpotInterruption(self.host, self.reason)

    def explains(self):
        """After a failed command: is a host being interrupted? Polls right away, up to
        SSH_LOST_POLLS times, so a lost host is recognised instead of reported as INVALID."""
        if self.cluster.local:
            return False
        for _ in range(SSH_LOST_POLLS):
            if self.tripped.is_set() or self.poll():
                return True
            if not any(self._misses.values()):
                return False
            time.sleep(5)
        return self.tripped.is_set()

    def paused_record(self, stack, test):
        return {"time": utc_now().isoformat(), "host": self.host, "reason": self.reason,
                "stack": stack, "test": test}
