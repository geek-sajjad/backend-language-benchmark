import { availableParallelism } from 'node:os';

export function env(key: string, fallback: string): string {
  const value = process.env[key];
  return value ? value : fallback;
}

function envInt(key: string, fallback: number): number {
  const value = Number(env(key, String(fallback)));
  if (!Number.isInteger(value) || value < 1) throw new Error(`${key} must be a positive integer`);
  return value;
}

/** Cluster workers: one per core. */
export const workers = availableParallelism();

/** DB_POOL_SIZE is the stack's total, split evenly across the workers (64 total = 4 × 16). */
export const poolPerWorker = Math.max(1, Math.floor(envInt('DB_POOL_SIZE', 64) / workers));
