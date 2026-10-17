import { Type } from 'class-transformer';

// Test 7 request body. The global ValidationPipe builds these classes with class-transformer
// (`@Type` for the nested ones) but finds no constraints to check: test 7 validates nothing
// beyond parsing (docs/02-test-spec.md, test 7). Nest's pipe passes undecorated classes
// (`forbidUnknownValues: false`).

export class DiscountDto {
  kind: 'percent' | 'fixed';
  value: number;
}

export class PricingDto {
  unit: number;
  currency: string;

  @Type(() => DiscountDto)
  discounts: DiscountDto[];
}

export class CartProductDto {
  id: number;
  name: string;
  category: string;
  attributes: Record<string, string>;
}

export class LineItemDto {
  line_id: number;
  quantity: number;

  @Type(() => CartProductDto)
  product: CartProductDto;

  @Type(() => PricingDto)
  pricing: PricingDto;
}

export class CartDto {
  cart_id: string;
  currency: string;

  @Type(() => LineItemDto)
  items: LineItemDto[];
}
