import { Type } from 'class-transformer';
import { ArrayMaxSize, ArrayMinSize, ArrayUnique, IsArray, IsInt, Max, Min, ValidateNested } from 'class-validator';

export class OrderLineDto {
  @IsInt()
  @Min(1)
  product_id: number;

  @IsInt()
  @Min(1)
  @Max(10)
  quantity: number;
}

/** `/secure/orders`: any `customer_id` in the body is ignored; the token's `sub` is used. */
export class SecureCreateOrderDto {
  @IsArray()
  @ArrayMinSize(1)
  @ArrayMaxSize(10)
  @ArrayUnique((line: OrderLineDto) => line.product_id, { message: 'duplicate product_id' })
  @ValidateNested({ each: true })
  @Type(() => OrderLineDto)
  items: OrderLineDto[];
}

export class CreateOrderDto extends SecureCreateOrderDto {
  @IsInt()
  @Min(1)
  customer_id: number;
}
