import { Module } from '@nestjs/common';
import { PassportModule } from '@nestjs/passport';
import { TypeOrmModule } from '@nestjs/typeorm';

import { AggregateService } from './aggregate/aggregate.service.js';
import { ApiController, SecureApiController } from './api.controller.js';
import { JwtStrategy } from './auth/jwt.strategy.js';
import { CatalogService } from './catalog/catalog.service.js';
import { env, poolPerWorker } from './config.js';
import { DownstreamService } from './downstream/downstream.service.js';
import { OrderItem } from './entities/order-item.entity.js';
import { Order } from './entities/order.entity.js';
import { Product } from './entities/product.entity.js';
import { OrdersService } from './orders/orders.service.js';
import { TransformService } from './transform/transform.service.js';

const entities = [Product, Order, OrderItem];

@Module({
  imports: [
    TypeOrmModule.forRoot({
      type: 'postgres',
      host: env('DB_HOST', '127.0.0.1'),
      port: Number(env('DB_PORT', '5432')),
      database: env('DB_NAME', 'bench'),
      username: env('DB_USER', 'bench'),
      password: env('DB_PASSWORD', 'bench'),
      poolSize: poolPerWorker,
      parseInt8: true,
      entities,
      logging: ['error', 'warn'],
      // The DB may still be starting (compose up, DB reset): retry for up to a minute.
      retryAttempts: 30,
      retryDelay: 2000,
    }),
    TypeOrmModule.forFeature(entities),
    PassportModule,
  ],
  controllers: [ApiController, SecureApiController],
  providers: [CatalogService, OrdersService, DownstreamService, TransformService, AggregateService, JwtStrategy],
})
export class AppModule {}
