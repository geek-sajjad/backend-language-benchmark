# k6 split: several k6 processes per step (2026-10-17)

Campaign lab, session 1 hosts (eu-north-1c, AMI `ami-0fd9576cca965bd19`): loadgen `c7a.4xlarge` (k6 on cores 0-13, Prometheus 14-15), target = the downstream mock as echo server (`DELAY_MS=0`) on the app host `c7a.xlarge`. k6 2.2.0, `GOGC=off`, Prometheus remote-write output on unless noted. Every row is one step: 15 s settle + 30 s measure. Script: [`infra/k6-sizing/remote/split.py`](../../../infra/k6-sizing/remote/split.py); rows in `split.jsonl`, per-process k6 summaries next to it.

## Problem

`bench.py calibrate` stopped at 19.2k req/s (echo) with only 16-27% loadgen CPU. Repeated with full-length steps:

| Rate | k6 processes | Prometheus output | Dropped | p99 | Loadgen CPU |
|---|---|---|---|---|---|
| 10k / 15k / 20k | 1 | on | 0 | 0.16 / 0.18 / 0.19 ms | |
| 25k | 1 | on | 166-207 | 0.20 ms | 20-21% |
| 25k | 1 | off | 0 | 0.20 ms | 18% |
| 30k | 1 | off | 890 | 0.23 ms | |
| 40k | 1 | off | 1,670-2,041 | 0.9-1.6 ms | 31% |

One k6 process drops iterations above ~20k req/s (~25k without the Prometheus output) while most of the loadgen is idle: the limit is per process, not CPU, so a bigger loadgen would not help. A larger `preAllocatedVUs` pool (10× the default) made it worse (40k: 22,796 dropped, p99 128 ms).

## Fix: N processes on the shared k6 cpuset

`runner/benchlib/k6.py` runs a step above `k6_max_rate_per_process` (config `[loadgen]`) as N processes with RATE/N each, started paused and resumed together through k6's REST API, and merges the summaries.

| Rate | Per-process limit | Processes | Dropped | p99 (process min-max) | Loadgen CPU | App host CPU |
|---|---|---|---|---|---|---|
| 15k | 1 process | 1 | 0 | 0.179 ms | 13% | 26% |
| 15k | 7,500 | 2 | 0 | 0.174 ms (0.171-0.174) | 15% | 26% |
| 25k | 1 process | 1 | 207 | 0.202 ms | 21% | 39% |
| 25k | 12,000 | 3 | 0 | 0.194 ms (0.190-0.194) | 25% | 39% |
| 40k | 12,000 | 4 | **0** | 0.399 ms (0.383-0.399) | 42% | 54% |
| 55k | 12,000 | 5 | **0** | 2.45 ms (1.69-2.45) | 57% | 64% |
| 40k | 6,000 | 7 | 0 | 0.398 ms (0.367-0.398) | 51% | 53% |
| 55k | 6,000 | 10 | 0 | 2.78 ms (1.98-2.78) | 66% | 64% |

- Split and single process measure the same latency where both are clean (15k: 0.174 vs 0.179 ms).
- **12,000 per process** is chosen: as clean as 6,000 and less loadgen CPU (fewer processes, less overhead).
- At 55k the echo server's host is at 64% CPU (a quarter of it softirq), and the p99 rise comes from the target. Extrapolating the loadgen CPU, the 70% validity limit is reached at ~65-70k req/s.

## Dead end: one core range per process

The first implementation pinned each process to its own slice of cores 0-13 (rows `split-40k` … `p6k-60k-prom` in `split.jsonl`). With 10 processes on 14 cores some got a single core, ran at 55-68% busy and couldn't absorb bursts: 91-5,565 dropped at 40-60k with a wide p99 spread (e.g. 1.7-17.7 ms at 55k) while every core of both hosts was at least 31% idle and no ENA allowance was exceeded (`ethtool -S`). Giving every process the whole cpuset fixed it (rows `shared-*`).

## Merging percentiles

Percentiles of different processes can't be averaged. A percentile of the combined requests lies between the processes' own values (the combined distribution is a mixture), so the runner reports the highest process value: an upper bound. Every process sends the same request mix at the same rate, so the bound is tight: the process spread above is 1-4% at 15-40k. `p99_spread_ms` is kept in every merged step file (`*.merged.json`).
