# 07 · Runbook

Step-by-step procedure for a scored campaign. Follow it in order and tick the boxes in the campaign's notes.

The campaign runs as **8 AWS sessions** (decided 2026-10-17, [06](06-runner-and-results.md#sessions-one-stack-per-day)): sessions 1–7 run one stack each, session 8 runs the test 9 common-load phase for all stacks. Steps 1–2 happen once. **Steps 3–9 are repeated for every session**, each ending with `terraform destroy`; a session takes about 2 hours, so one per day is comfortable.

## 0. Prerequisites (local machine)

- AWS account with a **spot** vCPU quota ("All Standard Spot Instance Requests") of at least the three hosts together, plus room for one resize (≥ 64 vCPU; request an increase early)
- Everything in steps 1–2 is local; AWS is only switched on from step 3
- Terraform ≥ 1.9, AWS CLI v2, an SSH key pair, `rsync`, `python3` (3.11+, with `pyjwt[crypto]` for conformance) and `openssl`
- Docker (to build and test stacks locally)
- k6 (to run the k6 scripts locally against a single stack)

## 1. Develop and verify locally

```bash
docker compose -f db/compose.yml up -d                    # local Postgres + seed (~1 min)
docker compose -f downstream/compose.yml up -d --build    # test 6 mock
k6/data/jwt/gen-keys.sh && python3 k6/data/gen-tokens.py  # test-only JWT keys + test 9 tokens
docker compose -f stacks/<stack>/compose.yml up -d --build
python3 conformance/check.py http://localhost:8080        # must pass 100%
k6 run -e BASE_URL=http://localhost:8080 -e RATE=200 k6/tests/02-single-read.js
```

Optional: `MONITORING_CPUSET=6-7 docker compose -f monitoring/compose.yml up -d` plus the exporters ([monitoring/README.md](../monitoring/README.md)), and a short local campaign with `python3 runner/bench.py run --stacks <stack> --tests 1` (local mode: no `runner/hosts.toml`).

A local run only shows that things work. Local numbers are never published.

## 2. Freeze the methodology

Conformance runs **only here, locally**, at the commit that gets tagged. It is not rerun on AWS.

- [ ] All 7 stacks pass conformance locally
- [ ] Calibration is done manually, before the campaign session (DB ceilings for tests 2–5 with pgbench, k6's own ceiling with the echo script, loadgen size; see [04](04-methodology.md#when-postgres-is-the-bottleneck)). The results are written by hand to `results/calibration.json` and committed. The format is in [06](06-runner-and-results.md#calibrationjson). The test 3 ceiling must be measured with the 50-row page, since the db-sizing figure (7.1k) is for 100 rows. If the k6 ceiling demands it, the campaign uses `loadgen_type = c7a.8xlarge` from the start.
- [ ] Versions are pinned in every Dockerfile and lockfile
- [ ] `config/bench.toml` is reviewed
- [ ] `git tag methodology-v1 && git push --tags`

## 3. Provision (every session)

```bash
cd infra/terraform
terraform init
terraform apply -var region=eu-north-1 -var ami_id=<pinned>   # ~5 min; add -var loadgen_type=c7a.8xlarge if calibration chose it
terraform output                             # public_ips / private_ips maps by role, ssh_commands, hosts_toml
```

- [ ] Session 1: leave out `-var ami_id`, then write down `terraform output ami_id` in the campaign notes. Every later session passes exactly this, plus the same region, AZ, instance types and the `methodology-v1` checkout. The runner refuses a session whose hosts don't match the hardware spec recorded by session 1.

## 4. Deploy and seed (every session)

```bash
./infra/host-setup/deploy.sh                 # sysctl, Docker, exporters, repo checkout on all hosts
./infra/host-setup/seed-db.sh                # schema + seed → bench_template (~1 min locally; time it on AWS)
terraform -chdir=infra/terraform output grafana_url                  # public live dashboard (view-only)
terraform -chdir=infra/terraform output -raw grafana_admin_password  # admin login: use via ssh -L 3000:localhost:3000 ubuntu@<loadgen-public-ip>
```

## 5. Check on loadgen (every session)

```bash
# from the local machine: deploy.sh doesn't sync results/, so copy the calibration file
ssh ubuntu@<loadgen-public-ip> 'mkdir -p ~/bench/results'
scp results/calibration.json ubuntu@<loadgen-public-ip>:bench/results/

ssh ubuntu@<loadgen-public-ip>                     # see: terraform output ssh_commands
cd ~/bench
python3 runner/bench.py check
```

- [ ] `results/calibration.json` is on loadgen (the runner copies it into the campaign manifest).
- [ ] Session 2 onwards: the campaign folder is on loadgen too (`scp -r results/<campaign-id> ubuntu@<loadgen-public-ip>:bench/results/` from the local machine), so `run --campaign` and `common-load` find the manifest and the earlier sessions' results.
- [ ] Grafana is stopped on loadgen (`docker compose -f monitoring/compose.yml stop grafana`) and stays stopped through the scored session. Prometheus keeps running.

No calibration and no pilot run in any session (decided 2026-10-11): calibration was done manually beforehand (step 2), and the session starts right after the check.

## 6. Scored session

Sessions 1–7, one stack each, in any order (write the order in the campaign notes):

```bash
tmux new -s bench
# session 1 creates the campaign; note the printed <campaign-id>
python3 runner/bench.py run --stacks <stack>                          # ~1.5 h incl. image build; detach with Ctrl-b d
# sessions 2–7 add their stack to the same campaign
python3 runner/bench.py run --stacks <stack> --campaign <campaign-id>
# if interrupted or paused:
python3 runner/bench.py resume <campaign-id>
```

Session 8, after all 7 stacks have a test 9 result:

```bash
python3 runner/bench.py common-load <campaign-id>                     # ~50 min: 120 s at L_common per stack
```

A later session refuses to start (exit 3) if its hosts don't match the hardware spec of session 1, and `common-load` refuses until every configured stack has a test 9 result ([06](06-runner-and-results.md#sessions-one-stack-per-day)).

Images are not built in advance. Before each benchmark the runner builds that stack's image on the app host (`docker compose build`) and records its image ID. The first build of a stack in a session takes 1–10 min (Rust and Java are the slowest); later builds are Docker cache hits and take seconds, so every test of a stack runs the same image. Session 8 builds all 7 images again. The reset and warm-up come after the build, so the build doesn't affect the measurement. **Runner support is not implemented yet:** today the runner starts stacks with `--no-build` and expects `infra/host-setup/build-images.sh` to have run.

Grafana stays off: follow progress in the tmux session. The runner stops itself on any INVALID step. Fix the cause, then run `resume`.

**Spot interruption** (status `paused_spot`, see [06](06-runner-and-results.md#spot-interruptions)): results so far are saved. Only the interrupted pair is rerun. Wait until AWS has started the stopped host again (`aws ec2 describe-instances` shows `running`), then:

```bash
cd infra/terraform && terraform apply -refresh-only && cd ../..   # new public IPs
./infra/host-setup/deploy.sh
ssh ubuntu@<loadgen-public-ip> 'cd ~/bench && docker compose -f monitoring/compose.yml stop grafana'
# on loadgen, in tmux:
python3 runner/bench.py resume <campaign-id>   # refuses if the hardware spec differs: stay paused, try later
```

If AWS doesn't bring the host back soon, don't pay for the other two hosts while waiting: do step 7 and 9 now, and finish the same stack with `resume` in a new session (steps 3–5 first).

## 7. Collect (every session)

```bash
# on loadgen: Grafana back on, to browse this session's runs and capture clips for the video
docker compose -f monitoring/compose.yml start grafana

# from the local machine
./infra/host-setup/fetch-results.sh <campaign-id>   # copies results/ from loadgen
git add results/<campaign-id> && git commit -m "results: <campaign-id> session <n> (<stack>)"
```

Prometheus data lives on the loadgen host and is destroyed with it: capture this session's Grafana clips **before** step 9.

## 8. Score (after session 8)

```bash
python3 scoring/score.py results/<campaign-id>/results.csv
python3 scoring/charts.py results/<campaign-id>      # per-test charts with the DB ceiling → charts/ (needs matplotlib)
git add results/<campaign-id> && git commit -m "results: campaign <campaign-id>"
```

- [ ] Go through the publishing checklist in [05-scoring.md](05-scoring.md#checklist-before-publishing-results)

## 9. Tear down (every session)

```bash
cd infra/terraform && terraform destroy
```

Check the AWS console afterwards: no running instances or EBS volumes should be left.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `dropped_iterations > 0` at a rate the app clearly handles | k6 out of VUs or CPU | Raise `preAllocatedVUs`/`maxVUs`; resize the loadgen to `c7a.8xlarge` |
| `pps_allowance_exceeded` increases | Packet rate limit on app ENI (highest-rate tests: 2, 6) | Check whether the response size is unexpectedly large; this is a hard instance limit, so document it |
| db CPU > 70% in tests 2–5, 9 | DB ceiling reached | Expected: the step is flagged `db_bound`, not invalid. In tests 6, 7, 8 it means the downstream mock or something else on the db host is busy: investigate |
| Errors are `connection reset` at low rate | Accept backlog or `somaxconn` too small, or container ulimit | Check sysctls; `ulimits.nofile` in compose |
| PHP fails test 6 early | Expected: FPM concurrency = `max_children` | Not a setup bug; it's the result |
