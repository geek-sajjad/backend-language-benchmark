// Run parameters. The runner passes every value with -e (it is the only reader of
// config/bench.toml); the defaults make a plain `k6 run` useful during development.

const num = (name, def) => (__ENV[name] !== undefined && __ENV[name] !== '' ? Number(__ENV[name]) : def);

export const BASE_URL = (__ENV.BASE_URL || 'http://localhost:8080').replace(/\/$/, '');

// MODE=step:   constant arrival rate, settle (with a 5 s tail) then measure. Smoke, probe stages,
//              the recovery probe and the common-load run are all steps with different durations.
// MODE=warmup: ramping arrival rate FROM_RATE → TO_RATE over DURATION_S.
export const MODE = __ENV.MODE || 'step';
export const RATE = num('RATE', 100);              // iterations/s (test 9: sessions/s)
export const SETTLE_S = num('SETTLE_S', 15);
export const SETTLE_TAIL_S = Math.min(num('SETTLE_TAIL_S', 5), SETTLE_S);
export const MEASURE_S = num('MEASURE_S', 30);
export const FROM_RATE = num('FROM_RATE', 50);
export const TO_RATE = num('TO_RATE', 500);
export const DURATION_S = num('DURATION_S', 60);
export const TIMEOUT = `${num('TIMEOUT_S', 5)}s`;
export const SEED = num('SEED', 20261010);
// Smoke runs verify response bodies; load runs only check status codes and discard bodies.
export const CHECK_BODY = __ENV.CHECK_BODY === '1';

// Only used for k6's own end-of-test pass/fail line during development. The runner makes
// the real decision from the summary numbers and config/bench.toml.
export const SLO = { p95_ms: num('P95_MS', 100), p99_ms: num('P99_MS', 250), error_rate: num('ERROR_RATE', 0.01) };

export const SUMMARY_FILE = __ENV.SUMMARY_FILE || '';
