// Test 3: one page of 100 products (keyset pagination)
import { buildOptions, tagPhase } from '../lib/options.js';
import { get } from '../lib/http.js';
import { int } from '../lib/rng.js';
export { handleSummary } from '../lib/summary.js';

export const options = buildOptions();

export default function () {
  tagPhase();
  const category = int(1, 50);
  get(`/categories/${category}/products?after=${int(0, 90000)}`, '/categories/{id}/products', {
    bodyCheck: (b) => b.category_id === category && b.items.length === 100 && b.next_after === b.items[99].id,
  });
}
