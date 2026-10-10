// Test 6: slow downstream call (50 ms mock)
import { buildOptions, tagPhase } from '../lib/options.js';
import { get } from '../lib/http.js';
import { int } from '../lib/rng.js';
export { handleSummary } from '../lib/summary.js';

export const options = buildOptions();

export default function () {
  tagPhase();
  const id = int(1, 100000);
  get(`/downstream/${id}`, '/downstream/{id}', {
    bodyCheck: (b) => b.product_id === id && b.available === (id * 7919) % 1000 && b.source === 'downstream',
  });
}
