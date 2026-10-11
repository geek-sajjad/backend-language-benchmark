// Request helpers: fixed `name` tags; status and body checks in smoke runs only.
// Load runs skip check(): http_req_failed already counts non-2xx (and expected 422s as passes),
// and a check() per request cost ~20% of k6's CPU in profiling. Smoke runs verify every response.
import http from 'k6/http';
import { check } from 'k6';
import { BASE_URL, TIMEOUT, CHECK_BODY } from './env.js';

const JSON_HEADERS = { 'Content-Type': 'application/json' };
const EXPECT_422 = http.expectedStatuses(422);

function build(name, headers, expect422, keepBody) {
  const p = { tags: { name }, timeout: TIMEOUT, headers };
  if (expect422) p.responseCallback = EXPECT_422;
  if (keepBody || CHECK_BODY) p.responseType = 'text';
  return p;
}

// Params objects without per-request headers are built once and reused (k6 converts the JS
// object on every call, so fewer and stable objects mean less work and less garbage).
const cache = {};

function params(name, headers, json, expect422, keepBody) {
  if (headers) return build(name, json ? { ...JSON_HEADERS, ...headers } : headers, expect422, keepBody);
  const key = json || expect422 || keepBody ? `${name}|${json}|${expect422}|${keepBody}` : name;
  return cache[key] || (cache[key] = build(name, json ? JSON_HEADERS : {}, expect422, keepBody));
}

/**
 * @param {string} name  route template, e.g. '/products/{id}'
 * @param {number} status  expected status
 * @param {(body: any) => boolean} [bodyCheck]  smoke runs only
 */
function verify(res, name, status, bodyCheck) {
  if (!CHECK_BODY) return res;
  const checks = { [`${name} status ${status}`]: (r) => r.status === status };
  if (CHECK_BODY && bodyCheck) {
    checks[`${name} body`] = (r) => {
      try {
        return bodyCheck(r.json());
      } catch (_) {
        return false;
      }
    };
  }
  check(res, checks, { name });
  return res;
}

export function get(path, name, opts) {
  if (!opts) return verify(http.get(BASE_URL + path, params(name)), name, 200); // hot path (load runs)
  const { status = 200, bodyCheck, headers, keepBody = false } = opts;
  return verify(http.get(BASE_URL + path, params(name, headers, false, false, keepBody)), name, status, bodyCheck);
}

export function post(path, name, body, { status = 200, bodyCheck, headers, keepBody } = {}) {
  const p = params(name, headers, true, status === 422, keepBody === true);
  return verify(http.post(BASE_URL + path, body, p), name, status, bodyCheck);
}

/** Parsed JSON body, or null (failed request, or body discarded). */
export function json(res) {
  if (res.status < 200 || res.status >= 300 || !res.body) return null;
  try {
    return res.json();
  } catch (_) {
    return null;
  }
}
