// Test 7: heavy JSON transform (~4 KB request, 15 line items)
import { buildOptions, tagPhase } from '../lib/options.js';
import { post } from '../lib/http.js';
import { pick } from '../lib/rng.js';
import { TRANSFORM_PAYLOADS } from '../lib/payloads.js';
export { handleSummary } from '../lib/summary.js';

export const options = buildOptions();

export default function () {
  tagPhase();
  post('/transform', '/transform', pick(TRANSFORM_PAYLOADS), {
    bodyCheck: (b) => b.item_count === 15 && b.categories.length > 0 && b.categories[0].top_items.length > 0,
  });
}
