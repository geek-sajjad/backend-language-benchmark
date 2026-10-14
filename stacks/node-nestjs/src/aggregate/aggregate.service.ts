import { Injectable } from '@nestjs/common';

const SALES_SEED = 0x5eed_be7c_0001n;
const SALES_COUNT = 50_000;
const MASK64 = 0xffff_ffff_ffff_ffffn;

export interface Sale {
  id: number;
  region: number;
  category: number;
  customer_tier: number;
  amount_cents: number;
  quantity: number;
  day: number;
}

interface Group {
  category: number;
  count: number;
  sum_amount: number;
  min_amount: number;
  max_amount: number;
  avg_amount: number;
  total_quantity: number;
}

export interface AggregateResponse {
  region: number;
  min_amount: number;
  groups: Group[];
}

/** splitmix64 on unsigned 64-bit BigInts; only used once, at startup. */
class SplitMix64 {
  constructor(private state: bigint) {}

  next(): bigint {
    this.state = (this.state + 0x9e37_79b9_7f4a_7c15n) & MASK64;
    let z = this.state;
    z = ((z ^ (z >> 30n)) * 0xbf58_476d_1ce4_e5b9n) & MASK64;
    z = ((z ^ (z >> 27n)) * 0x94d0_49bb_1331_11ebn) & MASK64;
    return z ^ (z >> 31n);
  }

  below(n: number): number {
    return Number(this.next() % BigInt(n));
  }
}

/** The test 8 dataset; fields are drawn in declaration order (conformance/reference/reference.py). */
export function buildSales(): Sale[] {
  const rng = new SplitMix64(SALES_SEED);
  const sales: Sale[] = [];
  for (let id = 1; id <= SALES_COUNT; id++) {
    sales.push({
      id,
      region: rng.below(8),
      category: rng.below(50),
      customer_tier: rng.below(4),
      amount_cents: 100 + rng.below(100_000),
      quantity: 1 + rng.below(10),
      day: rng.below(365),
    });
  }
  return sales;
}

@Injectable()
export class AggregateService {
  // Built when the module is created, before the server starts listening.
  private readonly sales = buildSales();

  /** Test 8: GET /aggregate?region={r}&min_amount={m} */
  aggregate(region: number, minAmount: number): AggregateResponse {
    const groups = new Map<number, Group>();
    for (const sale of this.sales) {
      if (sale.region !== region || sale.amount_cents < minAmount) continue;
      let g = groups.get(sale.category);
      if (!g) {
        g = {
          category: sale.category,
          count: 0,
          sum_amount: 0,
          min_amount: Number.MAX_SAFE_INTEGER,
          max_amount: 0,
          avg_amount: 0,
          total_quantity: 0,
        };
        groups.set(sale.category, g);
      }
      g.count++;
      g.sum_amount += sale.amount_cents;
      g.min_amount = Math.min(g.min_amount, sale.amount_cents);
      g.max_amount = Math.max(g.max_amount, sale.amount_cents);
      g.total_quantity += sale.quantity;
    }

    const sorted = [...groups.values()];
    for (const g of sorted) g.avg_amount = Math.floor(g.sum_amount / g.count);
    sorted.sort((a, b) => b.sum_amount - a.sum_amount || a.category - b.category);
    return { region, min_amount: minAmount, groups: sorted.slice(0, 10) };
  }
}
