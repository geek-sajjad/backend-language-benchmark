# 07 · Runbook

Step-by-step procedure for a scored campaign. Follow it in order and tick the boxes in the campaign's notes.

## 0. Prerequisites (local machine)

- AWS account with a **spot** vCPU quota ("All Standard Spot Instance Requests", covers both the x86 and the Graviton hosts) of at least the three hosts together, plus room for one resize (≥ 64 vCPU; request an increase early)
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

- [ ] All 7 stacks pass conformance
- [ ] Versions are pinned in every Dockerfile and lockfile
- [ ] `config/bench.toml` is reviewed
- [ ] `git tag methodology-v1 && git push --tags`

## 3. Provision

```bash
cd infra/terraform
terraform init
terraform apply -var region=eu-north-1 -var operator_ip=$(curl -s ifconfig.me)/32   # ~5 min
terraform output                             # public_ips / private_ips maps by role, ssh_commands, hosts_toml
```

## 4. Deploy and seed

```bash
./infra/host-setup/deploy.sh                 # sysctl, Docker, exporters, repo checkout on all hosts
./infra/host-setup/seed-db.sh                # schema + seed → bench_template (~1 min locally; time it on AWS)
./infra/host-setup/build-images.sh           # build every stack that has a compose.yml ON the app host, record image IDs
terraform -chdir=infra/terraform output grafana_url                  # public live dashboard (view-only)
terraform -chdir=infra/terraform output -raw grafana_admin_password  # admin login: use via ssh -L 3000:localhost:3000 ubuntu@<loadgen-public-ip>
```

## 5. Check and calibrate (on loadgen)

```bash
ssh ubuntu@<loadgen-public-ip>                     # see: terraform output ssh_commands
cd ~/bench
python3 runner/bench.py check
python3 runner/bench.py conformance --stacks all   # same commit, now on AWS
python3 runner/bench.py calibrate
```

- [ ] DB ceiling for tests 2–5 is recorded (expected near the db-sizing test: ~130k / 7.1k / 15.1k / 9.0k req/s). It goes on the charts; the db is not upsized.
- [ ] Grafana is stopped on loadgen (`docker compose -f monitoring/compose.yml stop grafana`) and stays stopped through calibration, the pilot and the scored campaign. Prometheus keeps running.
- [ ] k6 ceiling is recorded, with Prometheus running on the same host. If the best expected rate of any test (likely test 2 or 6) is ≥ 70% of it, resize the loadgen to `c7g.8xlarge` (`terraform apply -var loadgen_type=c7g.8xlarge`; only the loadgen restarts) and recalibrate. A resize changes the host's **public IP**: rerun `./infra/host-setup/deploy.sh` afterwards.
- [ ] The downstream mock sustains ≥ 2× the expected best test 6 rate.

## 6. Pilot (not scored)

Run one fast stack (Rust) and one slow stack (PHP) on tests 2, 6 and 9:

```bash
python3 runner/bench.py run --stacks rust-axum,php-laravel --tests 2,6,9 --campaign pilot
```

Check that:
- no step is INVALID for db or loadgen headroom (Grafana is off; check `search.json`, or start Grafana briefly after the pilot to look at the headroom dashboard)
- the probe estimate E lands near the final `max_rps` (within ~25%) and the search converges in ≤ 8 steps
- no step is flagged `settle_unstable`. If one is, raise `settle_s` in `config/bench.toml` for **all** stacks and rerun the pilot.
- recovery restarts are rare (a few per pair at most)

**Pilot results are discarded.**

## 7. Scored campaign

```bash
tmux new -s bench
python3 runner/bench.py run --stacks all           # ~10 h; detach with Ctrl-b d
# if interrupted or paused:
python3 runner/bench.py resume <campaign-id>
```

Start it in the evening so it runs unattended overnight; idle hours with the hosts up cost as much as measured ones. Grafana stays off: follow progress in the tmux session. The runner stops itself on any INVALID step. Fix the cause, then run `resume`.

**Spot interruption** (status `paused_spot`, see [06](06-runner-and-results.md#spot-interruptions)): results so far are saved. Wait until AWS has started the stopped host again (`aws ec2 describe-instances` shows `running`), then:

```bash
cd infra/terraform && terraform apply -refresh-only && cd ../..   # new public IPs
./infra/host-setup/deploy.sh
ssh ubuntu@<loadgen-public-ip> 'cd ~/bench && docker compose -f monitoring/compose.yml stop grafana'
# on loadgen, in tmux:
python3 runner/bench.py resume <campaign-id>   # refuses if the hardware fingerprint differs: stay paused, try later
```

## 8. Collect and score

```bash
# on loadgen: Grafana back on, to browse the recorded runs and capture clips for the video
docker compose -f monitoring/compose.yml start grafana

# from the local machine
./infra/host-setup/fetch-results.sh <campaign-id>   # copies results/ from loadgen
python3 scoring/score.py results/<campaign-id>/results.csv
python3 scoring/charts.py results/<campaign-id>      # per-test charts with the DB ceiling → charts/ (needs matplotlib)
git add results/<campaign-id> && git commit -m "results: campaign <campaign-id>"
```

- [ ] Go through the publishing checklist in [05-scoring.md](05-scoring.md#checklist-before-publishing-results)

## 9. Tear down

```bash
cd infra/terraform && terraform destroy
```

Check the AWS console afterwards: no running instances or EBS volumes should be left.

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `dropped_iterations > 0` at a rate the app clearly handles | k6 out of VUs or CPU | Raise `preAllocatedVUs`/`maxVUs`; resize the loadgen to `c7g.8xlarge` |
| `pps_allowance_exceeded` increases | Packet rate limit on app ENI (highest-rate tests: 2, 6) | Check whether the response size is unexpectedly large; this is a hard instance limit, so document it |
| db CPU > 70% in tests 2–5, 9 | DB ceiling reached | Expected: the step is flagged `db_bound`, not invalid. In tests 6, 7, 8 it means the downstream mock or something else on the db host is busy: investigate |
| Errors are `connection reset` at low rate | Accept backlog or `somaxconn` too small, or container ulimit | Check sysctls; `ulimits.nofile` in compose |
| PHP fails test 6 early | Expected: FPM concurrency = `max_children` | Not a setup bug; it's the result |
