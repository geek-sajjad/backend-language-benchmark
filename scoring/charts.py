#!/usr/bin/env python3
"""Per-test charts from a campaign's results.csv (docs/08-roadmap.md M8).

Usage: python3 scoring/charts.py results/<campaign-id> [--out DIR]

One horizontal bar chart per test (max_rps per stack, best first) → <campaign>/charts/test-<n>.png.
DB-limited results (`db_bound`) are hatched and labelled, loadgen-limited ones (`loadgen_bound`,
a lower bound) are hatched and shown as "≥", and tests 2-5 show the calibrated
Postgres ceiling (pgbench, `calibrate`) as a dashed line (04 "When Postgres is the bottleneck").
Failed stacks keep their row with the failure status, so a missing bar is never mistaken for 0.

Needs matplotlib (runs on the operator's machine, not on the loadgen).
"""
import argparse
import csv
import json
import pathlib

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
TEST_NAMES = {  # docs/02-test-spec.md
    2: "Test 2 · Single read", 3: "Test 3 · Large result set (50 rows)", 4: "Test 4 · List with join",
    5: "Test 5 · Write transaction", 6: "Test 6 · Slow downstream call (50 ms)", 7: "Test 7 · Heavy JSON",
    8: "Test 8 · In-memory aggregation", 9: "Test 9 · Mixed workload (sessions)",
}
LANG_COLORS = {"go": "#00add8", "rust": "#ce422b", "node": "#5fa04e", "python": "#3776ab",
               "java": "#e76f00", "dotnet": "#512bd4", "php": "#777bb4"}


def db_ceilings(campaign):
    """{test: req/s} from the manifest's calibration copy, else calibration.json."""
    manifest = campaign / "manifest.json"
    cal = json.loads(manifest.read_text()).get("calibration") if manifest.exists() else None
    for path in (campaign / "calibration.json", REPO / "results" / "calibration.json"):
        if not cal and path.exists():
            cal = json.loads(path.read_text())
    out = {}
    for test, r in ((cal or {}).get("db_ceiling") or {}).items():
        if r.get("tps"):
            out[int(test)] = float(r["tps"])
    return out


def chart(test, rows, ceiling, path):
    rows = sorted(rows, key=lambda r: float(r["max_rps"] or 0))  # barh draws bottom-up: best on top
    labels = [r["stack"] for r in rows]
    values = [float(r["max_rps"]) if r["status"] == "ok" else 0 for r in rows]
    top = max(values + [ceiling or 0]) or 1

    fig, ax = plt.subplots(figsize=(16, 9), dpi=120)
    bars = ax.barh(labels, values, color=[LANG_COLORS.get(r["language"], "#888") for r in rows])
    for bar, r, v in zip(bars, rows, values):
        y = bar.get_y() + bar.get_height() / 2
        if r["status"] != "ok":
            ax.text(top * 0.01, y, f"failed ({r['status']})", va="center", fontsize=13, color="#b00020")
            continue
        text = f"{v:,.0f}"
        if r.get("db_bound") == "1":
            bar.set_hatch("//")
            bar.set_edgecolor("white")
            text += "  DB-limited"
        if r.get("loadgen_bound") == "1":
            bar.set_hatch("\\\\")
            bar.set_edgecolor("white")
            text = f"≥ {text}  loadgen-limited"
        ax.text(v + top * 0.01, y, text, va="center", fontsize=13, zorder=3,
                bbox={"facecolor": "white", "edgecolor": "none", "pad": 1})

    if ceiling:
        ax.axvline(ceiling, color="#222", linestyle="--", linewidth=2, zorder=1)
        ax.text(ceiling, len(rows) - 0.4, f" Postgres ceiling {ceiling:,.0f} req/s",
                ha="right" if ceiling > top * 0.7 else "left", va="bottom", fontsize=12)

    unit = "requests/s (measured)" if test == 9 else "max requests/s within the SLO"
    ax.set_xlabel(unit, fontsize=13)
    ax.set_xlim(0, top * 1.18)
    ax.set_ylim(-0.6, len(rows) - 0.4 + (0.5 if ceiling else 0))
    ax.set_title(TEST_NAMES.get(test, f"Test {test}"), fontsize=18, loc="left")
    ax.tick_params(axis="y", labelsize=14)
    ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("campaign", type=pathlib.Path, help="results/<campaign-id> (holds results.csv)")
    ap.add_argument("--out", type=pathlib.Path, help="default: <campaign>/charts")
    args = ap.parse_args()

    rows = list(csv.DictReader(open(args.campaign / "results.csv")))
    ceilings = db_ceilings(args.campaign)
    out = args.out or args.campaign / "charts"
    out.mkdir(parents=True, exist_ok=True)
    for test in sorted({int(r["test"]) for r in rows}):
        path = out / f"test-{test}.png"
        chart(test, [r for r in rows if int(r["test"]) == test], ceilings.get(test), path)
        bound = sum(1 for r in rows if int(r["test"]) == test and r.get("db_bound") == "1")
        print(f"{path}" + (f"  ceiling {ceilings[test]:,.0f}" if test in ceilings else "")
              + (f"  {bound} DB-limited" if bound else ""))


if __name__ == "__main__":
    main()
