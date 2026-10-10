// Test 1: JSON hello (docs/02-test-spec.md)
import { buildOptions, tagPhase } from '../lib/options.js';
import { get } from '../lib/http.js';
export { handleSummary } from '../lib/summary.js';

export const options = buildOptions();

export default function () {
  tagPhase();
  get('/json', '/json', { bodyCheck: (b) => b.message === 'Hello, World!' });
}
