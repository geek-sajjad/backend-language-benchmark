#!/usr/bin/env python3
"""Compute benchmark scores from results.csv.

Usage: python score.py results.csv [bench.toml]
Weights, failure score and tie threshold come from config/bench.toml [scoring] (the frozen config);
a campaign folder's own bench.toml copy is used when it sits next to results.csv.
"""
import csv, math, pathlib, sys, tomllib

COMPOSITE_TESTS = [2, 3, 4, 5, 6, 7, 8]   # test 1 (JSON hello) removed; numbers kept
MIXED_TEST = 9
CATEGORIES = {"database": [2, 3, 4, 5], "concurrency": [6], "cpu": [7, 8]}
OVERALL_WEIGHTS = {}     # [scoring] weights
FAILURE_SCORE = None     # [scoring] failure_score: a failed test (OOM, crash, SLO never met)
TIE_THRESHOLD = None     # [scoring] tie_threshold: relative gap below which two results are a tie

def load_config(results_path, config_path=None):
    global OVERALL_WEIGHTS, FAILURE_SCORE, TIE_THRESHOLD
    candidates = [config_path, pathlib.Path(results_path).parent / "bench.toml",
                  pathlib.Path(__file__).resolve().parents[1] / "config" / "bench.toml"]
    path = next(pathlib.Path(c) for c in candidates if c and pathlib.Path(c).exists())
    scoring = tomllib.loads(path.read_text())["scoring"]
    OVERALL_WEIGHTS = dict(scoring["weights"])
    FAILURE_SCORE = float(scoring["failure_score"])
    TIE_THRESHOLD = float(scoring["tie_threshold"])
    return path

def geomean(scores, weights=None):
    weights = weights or [1.0] * len(scores)
    return math.exp(sum(w * math.log(s) for s, w in zip(scores, weights)) / sum(weights))

def normalize(values, higher_is_better=True):
    """values: {stack: value or None}. Returns {stack: score 1..100}."""
    ok = [v for v in values.values() if v is not None]
    if not ok:
        return {stack: FAILURE_SCORE for stack in values}
    best = max(ok) if higher_is_better else min(ok)
    out = {}
    for stack, v in values.items():
        if v is None:
            out[stack] = FAILURE_SCORE
        else:
            ratio = v / best if higher_is_better else best / v
            out[stack] = max(FAILURE_SCORE, 100 * ratio)
    return out

def main(path, config_path=None):
    print(f"config: {load_config(path, config_path)}")
    results = {}            # (stack, test) -> max_rps, or None if failed
    p99, rss = {}, {}       # stack -> test 9 value, or None if failed
    language = {}
    db_bound = set()        # (stack, test) whose max_rps was limited by Postgres: reported, scored as measured
    for r in csv.DictReader(open(path)):
        stack, test = r["stack"], int(r["test"])
        language[stack] = r["language"]
        if (stack, test) in results:
            print(f"WARNING duplicate row: {stack} test {test} (expected one run)")
        ok = r["status"] == "ok"
        results[(stack, test)] = float(r["max_rps"]) if ok else None
        if ok and r.get("db_bound") == "1":
            db_bound.add((stack, test))
        if test == MIXED_TEST:
            # empty until the common-load session (session 8) has run
            p99[stack] = float(r["p99_common_ms"]) if ok and r["p99_common_ms"] else None
            rss[stack] = float(r["peak_rss_mb"]) if ok and r["peak_rss_mb"] else None

    stacks = sorted(language)
    test_scores = {}        # test -> {stack: score}
    for test in COMPOSITE_TESTS + [MIXED_TEST]:
        for s in stacks:
            if (s, test) not in results:
                print(f"WARNING missing result: {s} test {test} -> scored as failure")
        test_scores[test] = normalize({s: results.get((s, test)) for s in stacks})

    lat_scores = normalize({s: p99.get(s) for s in stacks}, higher_is_better=False)
    mem_scores = normalize({s: rss.get(s) for s in stacks}, higher_is_better=False)

    rows = []
    for s in stacks:
        comp = geomean([test_scores[t][s] for t in COMPOSITE_TESTS])
        mixed = test_scores[MIXED_TEST][s]
        parts = {"mixed": mixed, "composite": comp,
                 "latency": lat_scores[s], "memory": mem_scores[s]}
        overall = geomean(list(parts.values()), [OVERALL_WEIGHTS[k] for k in parts])
        cats = {c: geomean([test_scores[t][s] for t in ts]) for c, ts in CATEGORIES.items()}
        failed = [t for t in COMPOSITE_TESTS + [MIXED_TEST] if results.get((s, t)) is None]
        rows.append((overall, s, comp, mixed, lat_scores[s], mem_scores[s], cats, failed))

    print(f"{'stack':<16}{'language':<10}{'overall':>8}{'compos.':>8}{'mixed':>8}"
          f"{'latency':>8}{'memory':>8}" + "".join(f"{c[:8]:>9}" for c in CATEGORIES)
          + "  failed tests")
    for overall, s, comp, mixed, lat, mem, cats, failed in sorted(rows, reverse=True):
        print(f"{s:<16}{language[s]:<10}{overall:8.1f}{comp:8.1f}{mixed:8.1f}"
              f"{lat:8.1f}{mem:8.1f}" + "".join(f"{cats[c]:9.1f}" for c in CATEGORIES)
              + "  " + (", ".join(map(str, failed)) or "-"))

    if db_bound:
        print("DB-limited (Postgres stopped these at max_rps): "
              + ", ".join(f"{s} test {t}" for s, t in sorted(db_bound)))

    ranked = sorted(rows, reverse=True)
    ties = [f"{a[1]} ~ {b[1]}" for a, b in zip(ranked, ranked[1:]) if (a[0] - b[0]) / a[0] < TIE_THRESHOLD]
    if ties:
        print(f"ties (overall within {TIE_THRESHOLD:.0%}): " + "; ".join(ties))

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "results.csv", sys.argv[2] if len(sys.argv) > 2 else None)
