# 00 · Overview

## Goal

Answer one question for the video, with numbers anyone can reproduce:

> On the same 4-core / 8 GB server, using the stack a typical team would actually pick for a new production API, how much traffic can each backend language serve within a fixed latency SLO, and how much memory does it use doing it?

Each language's results come from:

- the **max req/s under the SLO** for each of the eight workloads
- the **p99 latency at a common load** (test 9)
- the **peak memory (RSS)** under load (test 9)

These combine into one score per language using [the scoring guide](05-scoring.md).

## Principles

1. **Realistic stacks, not benchmark specials.** Each language uses its mainstream framework, its mainstream data layer (ORM) and the production deployment recommended by the framework docs. TechEmpower-style raw-driver tricks are out of the main round. They are listed as "potential optimizations" in [03-tech-stacks.md](03-tech-stacks.md) and are candidates for a follow-up "tuned" episode.
2. **Production config is allowed. Hand-tuning is not.** Anything a competent team does on day one is allowed: release builds, production mode, worker count = cores, a sized connection pool, keep-alive, and no per-request logging. Swapping libraries, rewriting queries in raw SQL or using unusual runtime flags is not allowed in the main round.
3. **Same contract for everyone.** Every stack implements identical endpoints with identical inputs and outputs, checked by an automated conformance suite before any load is applied.
4. **The app is the only bottleneck.** Postgres, the load generator, the downstream mock and the network get extra headroom, and that headroom is measured. If something other than the app saturates, the run is invalid ([04-methodology.md](04-methodology.md)).
5. **Decide before you measure.** SLO thresholds, weights, failure score, test parameters and stack versions are frozen in `config/bench.toml` and in git before the first scored run.
6. **Everything is published.** That includes the code, configs, raw k6 output, metrics snapshots, `results.csv`, `score.py` and these docs.

## What this benchmark claims, and what it doesn't

| Claims | Does not claim |
|---|---|
| How these specific stacks, configured this way, behave on this hardware | That a language is "faster" in all contexts |
| Relative throughput under a fixed SLO | Absolute numbers you will see in your own app |
| Memory footprint under a realistic mixed load | Developer productivity, ecosystem quality or hiring |
| How each concurrency model handles slow I/O | Results for other frameworks in the same language |

Every published score names its stack, for example "Node: 38.7 (NestJS)".

## Deliverables

- Seven stack implementations that pass the conformance suite
- Infrastructure as code that recreates the exact environment
- One automated campaign: `results/<campaign-id>/`, run as one AWS session per stack plus a final common-load session
- Final scores and charts for the video
- This documentation set, published with the video
