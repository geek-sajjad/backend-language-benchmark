// Test 8: in-memory aggregation over 50,000 records
import { buildOptions, tagPhase } from '../lib/options.js';
import { get } from '../lib/http.js';
import { CHECK_BODY } from '../lib/env.js';
import { int, pick } from '../lib/rng.js';
export { handleSummary } from '../lib/summary.js';

export const options = buildOptions();

const MIN_AMOUNTS = [0, 1000, 10000, 50000, 90000];

export default function () {
  tagPhase();
  const region = int(0, 7);
  const min = pick(MIN_AMOUNTS);
  get(`/aggregate?region=${region}&min_amount=${min}`, '/aggregate', CHECK_BODY && {
    bodyCheck: (b) => b.region === region && b.min_amount === min && b.groups.length === 10,
  });
}
