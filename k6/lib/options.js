// Builds k6 options for every test script, so all tests share the exact same load shape.
import exec from 'k6/execution';
import { MODE, RATE, SETTLE_S, SETTLE_TAIL_S, MEASURE_S, FROM_RATE, TO_RATE, DURATION_S, SLO, CHECK_BODY } from './env.js';

// VUs needed ≈ rate × iteration time. A VU started mid-step is slow to initialise and k6 drops
// iterations meanwhile, so the pre-allocated pool must absorb any stall a passing app can have:
// - 100 ms → 250 ms: a 169 ms Node stall (test 2, 22k req/s) invalidated a passing step.
// - 250 ms → up to 1 s: a ~0.5 s .NET stall (test 4, 4.2k req/s, 2026-11-10; p99 still under the
//   SLO) did the same twice. 1 s at every rate would cost too much memory (~1 MB per VU) and a
//   much larger pool made k6 drop more at 40k (results/sizing/k6-split-2026-10), so the 1 s pool
//   is capped at 2,500 VUs per k6 process (rate here is per process); the 250 ms floor stays.
function vus(rate, requestsPerIteration) {
  const perSecond = rate * requestsPerIteration;
  const pre = Math.max(10, Math.ceil(perSecond * 0.25), Math.min(Math.ceil(perSecond * 1.0), 2500));
  return {
    preAllocatedVUs: pre,
    maxVUs: Math.max(100, pre, Math.ceil(perSecond * 1.0)),
  };
}

// Rates may be fractional (test 9 sessions/s = req/s ÷ 4.65): k6 takes an integer count per
// timeUnit, so rates are expressed per 10 s. Iterations are still spread evenly.
const per10s = (rate) => Math.max(1, Math.round(rate * 10));

function scenario(requestsPerIteration) {
  if (MODE === 'warmup') {
    return {
      executor: 'ramping-arrival-rate',
      startRate: per10s(FROM_RATE),
      timeUnit: '10s',
      stages: [{ target: per10s(TO_RATE), duration: `${DURATION_S}s` }],
      ...vus(TO_RATE, requestsPerIteration),
    };
  }
  return {
    executor: 'constant-arrival-rate',
    rate: per10s(RATE),
    timeUnit: '10s',
    duration: `${SETTLE_S + MEASURE_S}s`,
    ...vus(RATE, requestsPerIteration),
  };
}

/**
 * @param {object} o
 * @param {number} [o.requestsPerIteration=1]  4.65 for test 9 sessions
 * @param {string[]} [o.endpoints=[]]  `name` tags that get their own latency numbers in the summary
 */
export function buildOptions({ requestsPerIteration = 1, endpoints = [] } = {}) {
  // A tagged submetric only appears in the summary if a threshold references it (09 R12).
  // These are pass-through thresholds; the runner judges the numbers.
  const thresholds = {
    'http_req_duration{phase:measure}': [`p(95)<${SLO.p95_ms}`, `p(99)<${SLO.p99_ms}`],
    'http_req_failed{phase:measure}': [`rate<${SLO.error_rate}`],
    'http_reqs{phase:measure}': ['count>=0'],
    'iterations{phase:measure}': ['count>=0'],
    'http_req_duration{phase:settle_tail}': ['p(99)>=0'],
    'http_reqs{phase:settle_tail}': ['count>=0'],
    dropped_iterations: ['count>=0'],
  };
  if (CHECK_BODY) thresholds.checks = ['rate>=0'];
  for (const name of endpoints) {
    thresholds[`http_req_duration{phase:measure,name:${name}}`] = ['p(99)>=0'];
    thresholds[`http_req_failed{phase:measure,name:${name}}`] = ['rate>=0'];
  }
  return {
    scenarios: { load: scenario(requestsPerIteration) },
    thresholds,
    summaryTrendStats: ['avg', 'min', 'med', 'max', 'p(50)', 'p(90)', 'p(95)', 'p(99)'],
    // Only the tags the runner and dashboards use (09 R11). `url` is dropped on purpose:
    // every request carries a fixed `name` instead.
    systemTags: ['name', 'method', 'status', 'scenario', 'expected_response', 'error_code'],
    discardResponseBodies: !CHECK_BODY,
    noConnectionReuse: false,
    noVUConnectionReuse: false, // connections are reused across iterations, as in production clients (09 R10)
    insecureSkipTLSVerify: true,
    summaryTimeUnit: 'ms',
  };
}

// Per VU. Reading `exec.scenario` builds a new object on every access, and setting a VU tag
// isn't free either: profiling showed 8-12% of k6's CPU here when done on every iteration.
// So the start time is read once and the tag is only written when the phase changes.
let scenarioStart = 0;
let currentPhase = '';

/** Call at the start of every iteration: tags everything it sends with the current phase. */
export function tagPhase() {
  let phase = MODE === 'warmup' ? 'warmup' : 'measure';
  if (MODE !== 'warmup') {
    if (scenarioStart === 0) scenarioStart = exec.scenario.startTime;
    const elapsed = (Date.now() - scenarioStart) / 1000;
    if (elapsed < SETTLE_S - SETTLE_TAIL_S) phase = 'settle';
    else if (elapsed < SETTLE_S) phase = 'settle_tail';
  }
  if (phase !== currentPhase) {
    exec.vu.metrics.tags.phase = phase;
    currentPhase = phase;
  }
  return phase;
}
