import { Type } from 'class-transformer';
import { IsArray, IsIn, IsInt, IsObject, IsString, ValidateNested } from 'class-validator';

export class DiscountDto {
  @IsIn(['percent', 'fixed'])
  kind: 'percent' | 'fixed';

  @IsInt()
  value: number;
}

export class PricingDto {
  @IsInt()
  unit: number;

  @IsString()
  currency: string;

  @IsArray()
  @ValidateNested({ each: true })
  @Type(() => DiscountDto)
  discounts: DiscountDto[];
}

export class CartProductDto {
  @IsInt()
  id: number;

  @IsString()
  name: string;

  @IsString()
  category: string;

  @IsObject()
  attributes: Record<string, string>;
}

export class LineItemDto {
  @IsInt()
  line_id: number;

  @IsInt()
  quantity: number;

  @ValidateNested()
  @Type(() => CartProductDto)
  product: CartProductDto;

  @ValidateNested()
  @Type(() => PricingDto)
  pricing: PricingDto;
}

export class CartDto {
  @IsString()
  cart_id: string;

  @IsString()
  currency: string;

  @IsArray()
  @ValidateNested({ each: true })
  @Type(() => LineItemDto)
  items: LineItemDto[];
}
