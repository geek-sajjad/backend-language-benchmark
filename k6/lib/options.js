// Builds k6 options for every test script, so all tests share the exact same load shape.
import exec from 'k6/execution';
import { MODE, RATE, SETTLE_S, SETTLE_TAIL_S, MEASURE_S, FROM_RATE, TO_RATE, DURATION_S, SLO, CHECK_BODY } from './env.js';

// VUs needed ≈ rate × iteration time. Pre-allocate for 100 ms per request, allow up to
// 500 ms per request. A step that would need more is already far beyond the p99 SLO.
function vus(rate, requestsPerIteration) {
  const perIteration = requestsPerIteration;
  return {
    preAllocatedVUs: Math.max(10, Math.ceil(rate * perIteration * 0.1)),
    maxVUs: Math.max(100, Math.ceil(rate * perIteration * 0.5)),
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

/** Call at the start of every iteration: tags everything it sends with the current phase. */
export function tagPhase() {
  let phase = MODE === 'warmup' ? 'warmup' : 'measure';
  if (MODE !== 'warmup') {
    const elapsed = (Date.now() - exec.scenario.startTime) / 1000;
    if (elapsed < SETTLE_S - SETTLE_TAIL_S) phase = 'settle';
    else if (elapsed < SETTLE_S) phase = 'settle_tail';
  }
  exec.vu.metrics.tags.phase = phase;
  return phase;
}
