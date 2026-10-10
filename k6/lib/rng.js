// Seeded PRNG (mulberry32). Each VU gets its own stream from SEED and its VU id, so a run
// with the same seed and VU count sends the same request sequence to every stack.
import exec from 'k6/execution';
import { SEED } from './env.js';

function mulberry32(seed) {
  let a = seed >>> 0;
  return function next() {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

let stream = null;

function rand() {
  if (stream === null) stream = mulberry32(SEED ^ Math.imul(exec.vu.idInTest, 0x9e3779b1));
  return stream();
}

/** Integer in lo..hi inclusive. */
export function int(lo, hi) {
  return lo + Math.floor(rand() * (hi - lo + 1));
}

export function pick(xs) {
  return xs[Math.floor(rand() * xs.length)];
}

/** n distinct elements of xs (n <= xs.length). */
export function sample(xs, n) {
  const copy = xs.slice();
  for (let i = 0; i < n; i++) {
    const j = i + Math.floor(rand() * (copy.length - i));
    [copy[i], copy[j]] = [copy[j], copy[i]];
  }
  return copy.slice(0, n);
}

/** Pick an index by integer weights. */
export function weighted(weights) {
  const total = weights.reduce((a, b) => a + b, 0);
  let r = rand() * total;
  for (let i = 0; i < weights.length; i++) {
    r -= weights[i];
    if (r < 0) return i;
  }
  return weights.length - 1;
}
