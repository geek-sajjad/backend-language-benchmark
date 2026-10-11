// Test 2: single read by primary key
import { buildOptions, tagPhase } from '../lib/options.js';
import { get } from '../lib/http.js';
import { CHECK_BODY } from '../lib/env.js';
import { int } from '../lib/rng.js';
export { handleSummary } from '../lib/summary.js';

export const options = buildOptions();

export default function () {
  tagPhase();
  const id = int(1, 100000);
  get(`/products/${id}`, '/products/{id}', CHECK_BODY && { bodyCheck: (b) => b.id === id && typeof b.description === 'string' });
}
