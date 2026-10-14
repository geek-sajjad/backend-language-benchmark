import { Injectable } from '@nestjs/common';

import type { CartDto, PricingDto } from './cart.dto.js';

interface TopItem {
  line_id: number;
  product_id: number;
  name: string;
  total_cents: number;
}

interface CategorySummary {
  category: string;
  count: number;
  subtotal_cents: number;
  top_items: TopItem[];
}

export interface CartSummary {
  cart_id: string;
  currency: string;
  item_count: number;
  total_cents: number;
  categories: CategorySummary[];
}

@Injectable()
export class TransformService {
  /** Test 7: POST /transform */
  transform(cart: CartDto): CartSummary {
    let total = 0;
    const groups = new Map<string, CategorySummary>();

    for (const item of cart.items) {
      const lineTotal = discountedUnit(item.pricing) * item.quantity;
      total += lineTotal;
      const category = item.product.category;
      let group = groups.get(category);
      if (!group) {
        group = { category, count: 0, subtotal_cents: 0, top_items: [] };
        groups.set(category, group);
      }
      group.count++;
      group.subtotal_cents += lineTotal;
      group.top_items.push({
        line_id: item.line_id,
        product_id: item.product.id,
        name: normalizeName(item.product.name),
        total_cents: lineTotal,
      });
    }

    const categories = [...groups.values()];
    for (const c of categories) {
      c.top_items.sort((a, b) => b.total_cents - a.total_cents || a.line_id - b.line_id);
      c.top_items.length = Math.min(c.top_items.length, 3);
    }
    categories.sort((a, b) => b.subtotal_cents - a.subtotal_cents || compare(a.category, b.category));

    return {
      cart_id: cart.cart_id,
      currency: cart.currency,
      item_count: cart.items.length,
      total_cents: total,
      categories,
    };
  }
}

/** Discounts apply to the unit price in order; integer cents, rounding toward zero. */
function discountedUnit(pricing: PricingDto): number {
  let unit = pricing.unit;
  for (const d of pricing.discounts) {
    unit = d.kind === 'percent' ? unit - Math.trunc((unit * d.value) / 100) : Math.max(0, unit - d.value);
  }
  return unit;
}

/** Trim, lowercase and collapse whitespace runs to a single space. */
function normalizeName(name: string): string {
  return name.trim().toLowerCase().split(/\s+/).join(' ');
}

/** Plain code-unit order (names are ASCII), not locale order. */
function compare(a: string, b: string): number {
  return a < b ? -1 : a > b ? 1 : 0;
}
