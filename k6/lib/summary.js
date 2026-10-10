// End-of-test summary: the full k6 summary as JSON (scoring input), plus one short line.
import { SUMMARY_FILE, MODE, RATE, MEASURE_S } from './env.js';

function metric(data, key, field) {
  const m = data.metrics[key];
  return m ? m.values[field] : undefined;
}

export function handleSummary(data) {
  const reqs = metric(data, 'http_reqs{phase:measure}', 'count') || 0;
  const line =
    MODE === 'warmup'
      ? `warmup done: ${metric(data, 'http_reqs', 'count') || 0} requests\n`
      : `rate ${RATE}/s: measured ${(reqs / MEASURE_S).toFixed(1)} req/s, ` +
        `p95 ${(metric(data, 'http_req_duration{phase:measure}', 'p(95)') || 0).toFixed(2)} ms, ` +
        `p99 ${(metric(data, 'http_req_duration{phase:measure}', 'p(99)') || 0).toFixed(2)} ms, ` +
        `errors ${((metric(data, 'http_req_failed{phase:measure}', 'rate') || 0) * 100).toFixed(2)}%, ` +
        `dropped ${metric(data, 'dropped_iterations', 'count') || 0}\n`;
  const out = { stdout: line };
  if (SUMMARY_FILE) out[SUMMARY_FILE] = JSON.stringify(data, null, 1);
  return out;
}
