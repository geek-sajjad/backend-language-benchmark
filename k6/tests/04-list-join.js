// Test 4: a customer's 10 latest orders with items
import { buildOptions, tagPhase } from '../lib/options.js';
import { get } from '../lib/http.js';
import { CHECK_BODY } from '../lib/env.js';
import { int } from '../lib/rng.js';
export { handleSummary } from '../lib/summary.js';

export const options = buildOptions();

export default function () {
  tagPhase();
  const id = int(1, 100000);
  get(`/customers/${id}/orders`, '/customers/{id}/orders', CHECK_BODY && {
    bodyCheck: (b) => b.customer_id === id && b.orders.length === 10 && b.orders[0].items.length > 0,
  });
}
