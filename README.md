# Backend Language Benchmark

This repo benchmarks seven backend languages for a YouTube video. Each language runs one production-style stack. All stacks get the same hardware, the same database and the same nine workloads. Each one is scored with a published, reproducible formula.

| Language | Stack (one per language) | Folder |
|---|---|---|
| Node.js | NestJS + Fastify adapter + TypeORM, cluster mode | [`stacks/node-nestjs`](stacks/node-nestjs) |
| Rust | Axum + Tokio + SQLx | [`stacks/rust-axum`](stacks/rust-axum) |
| Go | Gin + GORM (pgx driver) | [`stacks/go-gin`](stacks/go-gin) |
| Python | FastAPI + SQLAlchemy 2 (async) + Uvicorn workers | [`stacks/python-fastapi`](stacks/python-fastapi) |
| Java | Spring Boot (MVC + virtual threads) + Spring Data JPA | [`stacks/java-spring`](stacks/java-spring) |
| .NET | ASP.NET Core Minimal APIs + EF Core | [`stacks/dotnet-aspnetcore`](stacks/dotnet-aspnetcore) |
| PHP | Laravel on Nginx + PHP-FPM (OPcache + JIT) | [`stacks/php-laravel`](stacks/php-laravel) |

[docs/03-tech-stacks.md](docs/03-tech-stacks.md) explains why each stack was chosen and which optimizations were deliberately left out.

## The nine tests

| # | Test | Type | Reveals |
|---|---|---|---|
| 1 | JSON hello | Baseline | Framework overhead |
| 2 | Single read | I/O | Driver and pool efficiency |
| 3 | Large result set | I/O + ORM hydration | Turning 100 rows into objects and JSON |
| 4 | List with join | I/O + serialization | Realistic read path |
| 5 | Write transaction | I/O | Write path and validation |
| 6 | Slow downstream call | I/O (waiting) | Concurrency model |
| 7 | Heavy JSON | CPU (realistic) | Parsing and transformation |
| 8 | In-memory aggregation | CPU (pure language) | Raw compute and GC |
| 9 | Mixed workload: user sessions (JWT-authenticated) | Everything | Headline "real app" result |

[docs/02-test-spec.md](docs/02-test-spec.md) has the exact endpoint contracts.

## Documentation

| Doc | What it covers |
|---|---|
| [00 Overview](docs/00-overview.md) | Goals, principles, what this benchmark does and does not claim |
| [01 Architecture](docs/01-architecture.md) | AWS layout (VPC, instances, placement), Docker, monitoring |
| [02 Test spec](docs/02-test-spec.md) | Data model, seed data, endpoint contracts, k6 traffic per test |
| [03 Tech stacks](docs/03-tech-stacks.md) | Stack choice per language, reasons, config rules, potential optimizations |
| [04 Methodology](docs/04-methodology.md) | SLO, max-rate search, warm-up, fairness rules, validity checks, DB-not-bottleneck proof |
| [05 Scoring](docs/05-scoring.md) | Scoring guide (normalization, geometric means, overall score) |
| [06 Runner and results](docs/06-runner-and-results.md) | How the orchestration script works and how results are stored |
| [07 Runbook](docs/07-runbook.md) | Step-by-step: provision, deploy, calibrate, run, score, tear down |
| [08 Roadmap](docs/08-roadmap.md) | Milestones, open decisions, risks, budget, video outline |
| [09 Review findings](docs/09-review-findings.md) | Open review concerns on docs 00–05 with recommended fixes (stack code reviewed separately) |

## Repository layout

```
config/       bench.toml: SLO, search parameters, weights (frozen before first run)
docs/         all project documentation
infra/        Terraform (VPC, EC2, placement group) and host setup scripts
db/           Postgres schema, deterministic seed, postgresql.conf
downstream/   mock "slow downstream" HTTP service for test 6
stacks/       one folder per language: app source, Dockerfile, compose file, stack notes
k6/           load test scripts (lib/ shared helpers, tests/ one script per test, data/ payloads)
conformance/  correctness checks: every stack must return identical results before benchmarking
monitoring/   Prometheus + Grafana configs and dashboards
runner/       orchestration script: runs every test for every stack and saves results
scoring/      score.py: turns results.csv into all published scores
results/      one folder per campaign: raw k6 summaries, metrics, results.csv, manifest
```

## Quick start (once implemented)

```bash
cd infra/terraform && terraform apply           # provision VPC + instances
./infra/host-setup/deploy.sh                    # push compose files, seed Postgres
python3 runner/bench.py calibrate               # prove DB and load generator have headroom
python3 runner/bench.py run --stacks all        # full campaign, results/<campaign-id>/
python3 scoring/score.py results/<campaign-id>/results.csv
cd infra/terraform && terraform destroy         # stop paying
```

The full procedure is in [docs/07-runbook.md](docs/07-runbook.md).
