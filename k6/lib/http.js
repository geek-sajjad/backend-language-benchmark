// Request helpers: fixed `name` tags, status checks, and body checks in smoke runs.
import http from 'k6/http';
import { check } from 'k6';
import { BASE_URL, TIMEOUT, CHECK_BODY } from './env.js';

const JSON_HEADERS = { 'Content-Type': 'application/json' };
const EXPECT_422 = http.expectedStatuses(422);

function params(name, { headers = {}, expect422 = false, keepBody = false } = {}) {
  const p = { tags: { name }, timeout: TIMEOUT, headers };
  if (expect422) p.responseCallback = EXPECT_422;
  if (keepBody || CHECK_BODY) p.responseType = 'text';
  return p;
}

/**
 * @param {string} name  route template, e.g. '/products/{id}'
 * @param {number} status  expected status
 * @param {(body: any) => boolean} [bodyCheck]  smoke runs only
 */
function verify(res, name, status, bodyCheck) {
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

export function get(path, name, { status = 200, bodyCheck, headers, keepBody } = {}) {
  return verify(http.get(BASE_URL + path, params(name, { headers, keepBody })), name, status, bodyCheck);
}

export function post(path, name, body, { status = 200, bodyCheck, headers = {}, keepBody } = {}) {
  const p = params(name, { headers: { ...JSON_HEADERS, ...headers }, expect422: status === 422, keepBody });
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
