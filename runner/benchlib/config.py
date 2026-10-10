import hashlib
import tomllib

from .util import REPO

TEST_SCRIPTS = {
    1: "01-json.js",
    2: "02-single-read.js",
    3: "03-large-result.js",
    4: "04-list-join.js",
    5: "05-write.js",
    6: "06-downstream.js",
    7: "07-transform.js",
    8: "08-aggregate.js",
    9: "09-mixed.js",
}
ALL_TESTS = list(TEST_SCRIPTS)
MIXED_TEST = 9


class Config:
    """config/bench.toml. The runner is the only component that reads it; k6 gets values via -e."""

    def __init__(self, path=None):
        self.path = path or (REPO / "config" / "bench.toml")
        self.raw_bytes = open(self.path, "rb").read()
        self.data = tomllib.loads(self.raw_bytes.decode())
        self.sha256 = hashlib.sha256(self.raw_bytes).hexdigest()

    def __getitem__(self, key):
        return self.data[key]

    def get(self, *keys, default=None):
        node = self.data
        for k in keys:
            if not isinstance(node, dict) or k not in node:
                return default
            node = node[k]
        return node

    def override(self, section, key, test, default):
        """Per-test override tables, e.g. [search.warmup.to_rate_override] "8" = 100."""
        table = self.get(*section.split("."), f"{key}_override", default={})
        return table.get(str(test), self.get(*section.split("."), key, default=default))

    @property
    def stacks(self):
        return dict(self.data["stacks"])

    def requests_per_iteration(self, test):
        return self["mixed_sessions"]["avg_requests_per_session"] if test == MIXED_TEST else 1.0

    def journey_weights(self):
        m = self["mixed_sessions"]
        return ",".join(str(m[j]) for j in ("browser", "buyer", "returning", "cart_sync", "analyst"))

    def is_write_test(self, test):
        return test in self["reset"]["recreate_db_tests"]
