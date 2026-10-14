import {
  Body,
  Controller,
  DefaultValuePipe,
  ForbiddenException,
  Get,
  HttpCode,
  Param,
  ParseIntPipe,
  Post,
  Query,
  UseGuards,
} from '@nestjs/common';
import { AuthGuard } from '@nestjs/passport';

import { AggregateService } from './aggregate/aggregate.service.js';
import { CurrentUser } from './auth/current-user.decorator.js';
import type { AuthUser } from './auth/jwt.strategy.js';
import { CatalogService } from './catalog/catalog.service.js';
import { DownstreamService } from './downstream/downstream.service.js';
import { CreateOrderDto, SecureCreateOrderDto } from './orders/create-order.dto.js';
import { OrdersService } from './orders/orders.service.js';
import { CartDto } from './transform/cart.dto.js';
import { TransformService } from './transform/transform.service.js';

/**
 * Endpoints served both unprotected (tests 2–8) and under `/secure` (test 9).
 * Each controller below adds its own variant of the two customer-specific order routes.
 */
abstract class SharedRoutes {
  constructor(
    protected readonly catalog: CatalogService,
    protected readonly orders: OrdersService,
    protected readonly downstream: DownstreamService,
    protected readonly transformer: TransformService,
    protected readonly aggregator: AggregateService,
  ) {}

  @Get('products/:id')
  getProduct(@Param('id', ParseIntPipe) id: number) {
    return this.catalog.getProduct(id);
  }

  @Get('categories/:id/products')
  categoryProducts(
    @Param('id', ParseIntPipe) id: number,
    @Query('after', new DefaultValuePipe(0), ParseIntPipe) after: number,
  ) {
    return this.catalog.categoryPage(id, after);
  }

  @Get('downstream/:id')
  inventory(@Param('id', ParseIntPipe) id: number) {
    return this.downstream.inventory(id);
  }

  @Post('transform')
  @HttpCode(200)
  transform(@Body() cart: CartDto) {
    return this.transformer.transform(cart);
  }

  @Get('aggregate')
  aggregate(@Query('region', ParseIntPipe) region: number, @Query('min_amount', ParseIntPipe) minAmount: number) {
    return this.aggregator.aggregate(region, minAmount);
  }
}

@Controller()
export class ApiController extends SharedRoutes {
  constructor(
    catalog: CatalogService,
    orders: OrdersService,
    downstream: DownstreamService,
    transformer: TransformService,
    aggregator: AggregateService,
  ) {
    super(catalog, orders, downstream, transformer, aggregator);
  }

  @Get('health')
  health() {
    return 'ok';
  }

  @Get('customers/:id/orders')
  customerOrders(@Param('id', ParseIntPipe) id: number) {
    return this.orders.customerOrders(id);
  }

  @Post('orders')
  createOrder(@Body() body: CreateOrderDto) {
    return this.orders.createOrder(body.customer_id, body.items);
  }
}

@Controller('secure')
@UseGuards(AuthGuard('jwt'))
export class SecureApiController extends SharedRoutes {
  constructor(
    catalog: CatalogService,
    orders: OrdersService,
    downstream: DownstreamService,
    transformer: TransformService,
    aggregator: AggregateService,
  ) {
    super(catalog, orders, downstream, transformer, aggregator);
  }

  /** Only the token's own customer may read their orders. */
  @Get('customers/:id/orders')
  customerOrders(@Param('id', ParseIntPipe) id: number, @CurrentUser() user: AuthUser) {
    if (id !== user.customerId) throw new ForbiddenException();
    return this.orders.customerOrders(id);
  }

  @Post('orders')
  createOrder(@Body() body: SecureCreateOrderDto, @CurrentUser() user: AuthUser) {
    return this.orders.createOrder(user.customerId, body.items);
  }
}
