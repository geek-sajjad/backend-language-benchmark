#!/usr/bin/env bash
# Phase 1, runs ON the m7a.2xlarge: setup | conformance | measure.
# Cores: 0-3 rust-axum (WORKERS=4), 4-5 Postgres, 6-7 k6 (and the downstream mock, conformance only).
set -euo pipefail
cd ~/bench
OUT=~/bench/out RAW=~/bench/out/raw
mkdir -p "$RAW"
export PGPASSWORD=bench PGHOST=127.0.0.1 PGUSER=bench
log() { echo "[phase1 $(date +%T)] $*" >&2; }
reset_db() { psql -q -d postgres -f db/reset.sql >/dev/null; }

setup() {
  log "building rust-axum in the background"
  docker compose -f stacks/rust-axum/compose.yml build >"$RAW/build.log" 2>&1 &
  local build=$!
  log "starting Postgres (shared_buffers 4GB) and waiting for the seed"
  PG_SHARED_BUFFERS=4GB PG_EFFECTIVE_CACHE_SIZE=12GB docker compose -f db/compose.yml up -d
  local i
  for i in $(seq 1 180); do # TCP, not the socket: the init-time server listens on the socket only
    pg_isready -q -h 127.0.0.1 -U bench -d bench && break
    [[ $i == 180 ]] && { log "seed did not finish in 15 min"; exit 1; }
    sleep 5
  done
  log "seeded after ~$((i * 5)) s; reset + prewarm"
  reset_db
  wait "$build" || { tail -50 "$RAW/build.log"; exit 1; }
  docker compose -f downstream/compose.yml up -d --build >"$RAW/downstream-build.log" 2>&1
  WORKERS=4 DB_POOL_SIZE=64 docker compose -f stacks/rust-axum/compose.yml up -d
  docker update --cpuset-cpus 0-3 app >/dev/null
  docker update --cpuset-cpus 4-5 bench-db >/dev/null
  docker update --cpuset-cpus 6-7 downstream >/dev/null
  for i in $(seq 1 60); do curl -fsS -o /dev/null http://127.0.0.1:8080/health && break; sleep 1; done
  docker inspect -f '{{.Name}} cpuset={{.HostConfig.CpusetCpus}}' app bench-db downstream | tee "$OUT/cpusets.txt"
  [[ "$(docker inspect -f '{{.HostConfig.CpusetCpus}}' app bench-db | tr '\n' ' ')" == "0-3 4-5 " ]] ||
    { log "cpusets wrong"; exit 1; }
  docker exec app env | grep -E '^(WORKERS|DB_POOL_SIZE)=' | tee -a "$OUT/cpusets.txt"
}

conformance() {
  python3 conformance/check.py http://127.0.0.1:8080 2>&1 | tee "$OUT/conformance.txt"
  local rc=${PIPESTATUS[0]}
  docker rm -f downstream >/dev/null # only needed for the test 6 checks; must not run while measuring
  reset_db                           # conformance wrote orders
  return "$rc"
}

# ------------------------------------------------------------ measurement helpers
cgfile() { echo "/sys/fs/cgroup/system.slice/docker-$(docker inspect -f '{{.Id}}' "$1").scope/cpu.stat"; }
usage() { awk '/^usage_usec/ {print $2}' "$1"; }
# Average busy % over a set of cores from an `mpstat -P ALL` file: group_busy <file> "0 1 2 3"
group_busy() {
  awk -v set=" $2 " '$1 == "Average:" && $2 ~ /^[0-9]+$/ && index(set, " " $2 " ") { s += 100 - $NF; n++ }
    END { printf "%.1f", n ? s / n : -1 }' "$1"
}
sumval() { jq -r --arg k "$2" --arg f "$3" '.metrics[$k].values[$f] // 0' "$1"; }
k6run() { # k6run <script> <summary-file> KEY=VAL...
  local script="$1" sum="$2" a=() kv; shift 2
  for kv in "$@"; do a+=(-e "$kv"); done
  taskset -c 6,7 k6 run --quiet -e BASE_URL=http://127.0.0.1:8080 -e SUMMARY_FILE="$sum" "${a[@]}" \
    "k6/tests/$script" >>"$RAW/k6.log" 2>&1 || true # k6's own threshold verdict is ignored
}

# Fixed-rate run: 10 s settle + 30 s measure. CPU is read over a 28 s window inside the measure
# phase (starting 11.5 s after launch, so ~1.5 s of k6 init can't push it outside), and
# cpu_per_req = (Δusage / Δt) / measured_rps, i.e. CPU rate over request rate.
fixed() { # fixed <test> <script> <rate> <tag>  -> one JSON line
  local t="$1" s="$2" rate="$3" tag="$4"
  local sum="$RAW/t$t-fixed-$tag.json" mp="$RAW/t$t-fixed-$tag.mpstat"
  k6run "$s" "$sum" RATE="$rate" SETTLE_S=10 MEASURE_S=30 &
  local k6pid=$!
  sleep 11.5
  local a0 p0 t0 a1 p1 t1
  a0=$(usage "$APP_CG") p0=$(usage "$PG_CG") t0=$(date +%s.%N)
  LC_ALL=C mpstat -P ALL 1 28 >"$mp"
  a1=$(usage "$APP_CG") p1=$(usage "$PG_CG") t1=$(date +%s.%N)
  wait "$k6pid"
  jq -nc --argjson test "$t" --argjson rate "$rate" --arg tag "$tag" \
    --argjson reqs "$(sumval "$sum" 'http_reqs{phase:measure}' count)" \
    --argjson p99 "$(sumval "$sum" 'http_req_duration{phase:measure}' 'p(99)')" \
    --argjson err "$(sumval "$sum" 'http_req_failed{phase:measure}' rate)" \
    --argjson dropped "$(sumval "$sum" dropped_iterations count)" \
    --argjson app_us "$((a1 - a0))" --argjson pg_us "$((p1 - p0))" \
    --argjson dt "$(awk -v a="$t0" -v b="$t1" 'BEGIN{print b - a}')" \
    --argjson rust_busy "$(group_busy "$mp" "0 1 2 3")" --argjson pg_busy "$(group_busy "$mp" "4 5")" \
    --argjson k6_busy "$(group_busy "$mp" "6 7")" \
    '{test: $test, rate: $rate, tag: $tag, measured_rps: ($reqs / 30), p99_ms: $p99, error_rate: $err,
      dropped: $dropped, window_s: $dt, rust_cpu_s: ($app_us / 1e6), pg_cpu_s: ($pg_us / 1e6),
      cpu_per_req_s: (($app_us / 1e6 / $dt) / ($reqs / 30)),
      pg_cpu_per_req_s: (($pg_us / 1e6 / $dt) / ($reqs / 30)),
      rust_busy_pct: $rust_busy, pg_busy_pct: $pg_busy, k6_busy_pct: $k6_busy}'
}

# Ramp: 10 s steps (5 settle + 5 measure), ×2 until the first failure, then ×1.2 from the last
# pass until the next one. Fail = p99 ≥ 250 ms, errors ≥ 1 %, dropped iterations, or no requests.
ramp() { # ramp <test> <script> <start-rate>  -> one JSON line
  local t="$1" s="$2" rate="$3" factor=2 best_rate=0 best_rps=0 fail_mp="" pass_mp="" i
  for i in $(seq 1 30); do
    (( rate > 60000 )) && break # k6 on 2 cores is long gone by then
    local sum="$RAW/t$t-ramp-$rate.json" mp="$RAW/t$t-ramp-$rate.mpstat"
    k6run "$s" "$sum" RATE="$rate" SETTLE_S=5 MEASURE_S=5 &
    local k6pid=$!
    sleep 6
    LC_ALL=C mpstat -P ALL 1 4 >"$mp"
    wait "$k6pid"
    local reqs p99 err dropped ok
    reqs=$(sumval "$sum" 'http_reqs{phase:measure}' count)
    p99=$(sumval "$sum" 'http_req_duration{phase:measure}' 'p(99)')
    err=$(sumval "$sum" 'http_req_failed{phase:measure}' rate)
    dropped=$(sumval "$sum" dropped_iterations count)
    ok=$(awk -v r="$reqs" -v p="$p99" -v e="$err" -v d="$dropped" 'BEGIN{print (r > 0 && p < 250 && e < 0.01 && d == 0) ? 1 : 0}')
    log "  test $t ramp $rate/s: $([[ $ok == 1 ]] && echo pass || echo FAIL) (p99 $p99 ms, err $err, dropped $dropped," \
      "busy rust $(group_busy "$mp" "0 1 2 3") pg $(group_busy "$mp" "4 5") k6 $(group_busy "$mp" "6 7"))"
    if [[ $ok == 1 ]]; then
      best_rate=$rate best_rps=$(awk -v r="$reqs" 'BEGIN{print r / 5}') pass_mp=$mp
      rate=$(awk -v r="$rate" -v f="$factor" 'BEGIN{printf "%d", r * f}')
    else
      fail_mp=$mp
      if [[ $factor == 2 && $best_rate != 0 ]]; then
        factor=1.2 rate=$(awk -v r="$best_rate" 'BEGIN{printf "%d", r * 1.2}')
      else
        break
      fi
    fi
  done
  # The resource that was full: the busiest core group at the final failing step (≥ 85 % counts as full).
  local m="${fail_mp:-$pass_mp}" rb pb kb
  rb=$(group_busy "$m" "0 1 2 3") pb=$(group_busy "$m" "4 5") kb=$(group_busy "$m" "6 7")
  jq -nc --argjson test "$t" --argjson max_rate "$best_rate" --argjson max_rps "$best_rps" \
    --argjson rb "$rb" --argjson pb "$pb" --argjson kb "$kb" \
    '{test: $test, ramp_max_rate: $max_rate, ramp_max_4c: $max_rps, at_limit_busy: {rust: $rb, pg: $pb, k6: $kb},
      ramp_limited_by: ([["rust", $rb], ["postgres", $pb], ["k6", $kb]] | max_by(.[1])
        | if .[1] >= 85 then .[0] else .[0] + " (" + (.[1] | tostring) + "%, none full: latency)" end)}'
}

measure() {
  APP_CG=$(cgfile app) PG_CG=$(cgfile bench-db)
  [[ -r "$APP_CG" && -r "$PG_CG" ]] || { log "cgroup files not found: $APP_CG $PG_CG"; exit 1; }
  : >"$OUT/measurements.jsonl"
  local t s start
  for spec in 2:02-single-read.js:2000 3:03-large-result.js:500 4:04-list-join.js:2000 5:05-write.js:1000; do
    IFS=: read -r t s start <<<"$spec"
    log "test $t: warm-up"
    k6run "$s" "$RAW/t$t-warmup.json" MODE=warmup FROM_RATE=50 TO_RATE=1000 DURATION_S=20
    log "test $t: fixed rate $start/s"
    local r b rate=$start
    r=$(fixed "$t" "$s" "$rate" first)
    b=$(jq .rust_busy_pct <<<"$r")
    log "  $r"
    if awk -v b="$b" 'BEGIN{exit !(b < 45 || b > 65)}'; then
      rate=$(awk -v r="$rate" -v b="$b" 'BEGIN{n = r * 55 / b; if (n > 4 * r) n = 4 * r; printf "%d", n}')
      log "test $t: rust cores at $b %, adjusting once to $rate/s"
      echo "$r" | jq -c '. + {kind: "fixed_discarded"}' >>"$OUT/measurements.jsonl"
      r=$(fixed "$t" "$s" "$rate" adjusted)
      log "  $r"
    fi
    echo "$r" | jq -c '. + {kind: "fixed"}' >>"$OUT/measurements.jsonl"
    log "test $t: ramp from $rate/s"
    ramp "$t" "$s" "$rate" | jq -c '. + {kind: "ramp"}' >>"$OUT/measurements.jsonl"
    if [[ $t == 5 ]]; then log "reset after test 5"; reset_db; fi
  done
  log "done"
}

"$@"
