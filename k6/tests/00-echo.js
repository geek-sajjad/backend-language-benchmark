// Calibration only (runner/bench.py calibrate): the load generator's own ceiling against a
// trivial server, and the downstream mock's ceiling. Not a scored test.
import { buildOptions, tagPhase } from '../lib/options.js';
import { get } from '../lib/http.js';
import { int } from '../lib/rng.js';
export { handleSummary } from '../lib/summary.js';

export const options = buildOptions();

export default function () {
  tagPhase();
  get(`/inventory/${int(1, 100000)}`, '/inventory/{id}');
}
