// Test 2: single read by primary key
import { buildOptions, tagPhase } from '../lib/options.js';
import { get } from '../lib/http.js';
import { int } from '../lib/rng.js';
export { handleSummary } from '../lib/summary.js';

export const options = buildOptions();

export default function () {
  tagPhase();
  const id = int(1, 100000);
  get(`/products/${id}`, '/products/{id}', { bodyCheck: (b) => b.id === id && typeof b.description === 'string' });
}
