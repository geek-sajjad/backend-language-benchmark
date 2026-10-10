// Test 9: authenticated user sessions. One iteration = one session = one journey, sent in
// order over the VU's keep-alive connection, no think time (docs/02-test-spec.md, test 9).
// RATE is in sessions/s; the runner converts from req/s (÷ avg_requests_per_session).
import exec from 'k6/execution';
import { SharedArray } from 'k6/data';
import { buildOptions, tagPhase } from '../lib/options.js';
import { get, post, json } from '../lib/http.js';
import { int, pick, sample, weighted } from '../lib/rng.js';
import { TRANSFORM_PAYLOADS } from '../lib/payloads.js';
export { handleSummary } from '../lib/summary.js';

// token i belongs to customer i + 1 (k6/data/gen-tokens.py)
const TOKENS = new SharedArray('tokens', () => JSON.parse(open('../data/jwt/tokens.json')));

const N = {
  category: '/secure/categories/{id}/products',
  product: '/secure/products/{id}',
  downstream: '/secure/downstream/{id}',
  orders: '/secure/customers/{id}/orders',
  checkout: '/secure/orders',
  transform: '/secure/transform',
  aggregate: '/secure/aggregate',
};

export const options = buildOptions({
  requestsPerIteration: Number(__ENV.AVG_REQUESTS_PER_SESSION || 4.65),
  endpoints: Object.values(N),
});

const JOURNEYS = ['browser', 'buyer', 'returning', 'cart_sync', 'analyst'];
const WEIGHTS = (__ENV.JOURNEY_WEIGHTS || '40,25,20,10,5').split(',').map(Number);
const MIN_AMOUNTS = [0, 1000, 10000, 50000, 90000];

function categoryPage(auth, category) {
  const page = json(get(`/secure/categories/${category}/products?after=${int(0, 90000)}`, N.category, { headers: auth, keepBody: true }));
  // Fallback keeps the session's request count fixed when a step fails.
  return page && page.items.length ? page.items.map((p) => p.id) : [int(1, 100000), int(1, 100000), int(1, 100000)];
}

const product = (auth, id, keepBody = false) => get(`/secure/products/${id}`, N.product, { headers: auth, keepBody });
const stock = (auth, id) => get(`/secure/downstream/${id}`, N.downstream, { headers: auth });
const myOrders = (auth, sub, keepBody = false) => get(`/secure/customers/${sub}/orders`, N.orders, { headers: auth, keepBody });

const SESSIONS = {
  browser(auth) {
    const viewed = sample(categoryPage(auth, int(1, 50)), 3);
    for (const id of viewed) product(auth, id);
    stock(auth, pick(viewed));
  },
  buyer(auth, sub) {
    const page = categoryPage(auth, int(1, 50));
    const viewed = sample(page, 2);
    for (const id of viewed) product(auth, id);
    stock(auth, pick(viewed));
    const extra = page.filter((id) => !viewed.includes(id));
    const basket = [...viewed, ...extra.slice(0, 1)].slice(0, int(1, 3));
    const items = basket.map((product_id) => ({ product_id, quantity: int(1, 3) }));
    post('/secure/orders', N.checkout, JSON.stringify({ items }), { status: 201, headers: auth });
    myOrders(auth, sub);
  },
  returning(auth, sub) {
    const history = json(myOrders(auth, sub, true));
    const bought = history ? history.orders.flatMap((o) => o.items.map((i) => i.product_id)) : [];
    const ids = bought.length >= 2 ? sample(bought, 2) : [int(1, 100000), int(1, 100000)];
    const first = json(product(auth, ids[0], true));
    product(auth, ids[1]);
    categoryPage(auth, first ? first.category_id : int(1, 50));
  },
  cart_sync(auth) {
    for (let i = 0; i < 2; i++) post('/secure/transform', N.transform, pick(TRANSFORM_PAYLOADS), { headers: auth });
  },
  analyst(auth) {
    for (const region of sample([0, 1, 2, 3, 4, 5, 6, 7], 3)) {
      get(`/secure/aggregate?region=${region}&min_amount=${pick(MIN_AMOUNTS)}`, N.aggregate, { headers: auth });
    }
  },
};

export default function () {
  tagPhase();
  const journey = JOURNEYS[weighted(WEIGHTS)];
  exec.vu.metrics.tags.journey = journey;
  const t = int(0, TOKENS.length - 1);
  SESSIONS[journey]({ Authorization: `Bearer ${TOKENS[t]}` }, t + 1);
}
