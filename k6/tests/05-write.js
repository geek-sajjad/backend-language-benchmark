// Test 5: order write transaction. 2% of requests are deliberately invalid and must get 422.
import { buildOptions, tagPhase } from '../lib/options.js';
import { post } from '../lib/http.js';
import { int, pick } from '../lib/rng.js';
export { handleSummary } from '../lib/summary.js';

export const options = buildOptions();

function validItems() {
  const n = int(1, 5);
  const ids = new Set();
  while (ids.size < n) ids.add(int(1, 100000)); // distinct: duplicates are a 422 (09 R6)
  return [...ids].map((product_id) => ({ product_id, quantity: int(1, 10) }));
}

const INVALID = [
  () => ({ customer_id: 0, items: validItems() }),
  () => ({ customer_id: int(1, 100000), items: [] }),
  () => ({ customer_id: int(1, 100000), items: [{ product_id: int(1, 100000), quantity: 11 }] }),
  () => ({ customer_id: int(1, 100000), items: [{ product_id: 0, quantity: 1 }] }),
  () => {
    const id = int(1, 100000);
    return { customer_id: int(1, 100000), items: [{ product_id: id, quantity: 1 }, { product_id: id, quantity: 2 }] };
  },
];

export default function () {
  tagPhase();
  if (int(1, 100) <= 2) {
    post('/orders', '/orders', JSON.stringify(pick(INVALID)()), { status: 422 });
    return;
  }
  const body = { customer_id: int(1, 100000), items: validItems() };
  post('/orders', '/orders', JSON.stringify(body), {
    status: 201,
    bodyCheck: (b) => b.status === 'pending' && b.items.length === body.items.length && b.total_cents > 0,
  });
}
