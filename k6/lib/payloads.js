// Test 7 request bodies, shared read-only across VUs (k6/data/transform/, written by
// conformance/reference/gen_transform_payloads.py).
import { SharedArray } from 'k6/data';

export const TRANSFORM_PAYLOADS = new SharedArray('transform', () => {
  const out = [];
  for (let i = 1; i <= 200; i++) out.push(open(`../data/transform/${String(i).padStart(4, '0')}.json`));
  return out;
});
